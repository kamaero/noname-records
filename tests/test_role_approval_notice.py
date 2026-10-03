"""«Ты утверждён на роль» — сообщение, которое диктор ждёт и которое некому было послать.

Утверждение — это назначение актёра на роль в касте. До сих пор оно происходило молча:
диктор узнавал о нём, когда сам заходил и замечал роль в списке своих.

Два подводных камня, оба про доставку.

Первый: `send_telegram_message` при заданном `OWNER_TELEGRAM_ID` подменяет адресата
владельцем — так задумано для всех уведомлений о ходе работ, чтобы они не разлетались
по студии. Личное письмо человеку — не уведомление о ходе работ, и подмена превратила
бы его в шестое подряд сообщение владельцу самому себе.

Второй: имя актёра в касте и имя учётки в телеграме — разные строки. У Зотова каст
говорит «Pavel Petrovich», а телеграм — «Сергей Зотов»; связывает их учётка, к
которой привязаны обе.
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import TelegramAuthAccount, User, UserRole
from app.services.role_approval import approval_notice, notify_role_approved, resolve_actor_chat_id, should_notify_actor_change


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        yield session


def _account(db, *, name, chat_id, user_id="", role="dictor", active="true"):
    db.add(TelegramAuthAccount(telegram_user_id=chat_id, role=role, display_name=name, is_active=active, user_id=user_id))
    db.flush()


class TestWhatTheMessageSays:
    def test_it_names_the_role_and_the_book(self):
        text = approval_notice(role="Дгарнин", book_title="Крылья полумрака")

        assert "Дгарнин" in text
        assert "Крылья полумрака" in text

    def test_it_says_what_to_do_next(self):
        text = approval_notice(role="Дгарнин", book_title="Крылья полумрака")

        assert "по главам" in text

    def test_it_carries_a_link_to_the_role_when_there_is_one(self):
        text = approval_notice(role="Дгарнин", book_title="Крылья полумрака", link="https://studio.example/app/reader/role/b1")

        assert "https://studio.example/app/reader/role/b1" in text

    def test_a_link_is_not_invented_when_there_is_none(self):
        text = approval_notice(role="Дгарнин", book_title="Крылья полумрака")

        # С 01.10 в письме есть ссылки на «Помощь» и первые шаги; ссылку на реплики роли
        # без адреса книги по-прежнему не выдумываем.
        assert "/app/reader/role" not in text and "Все реплики роли" not in text


class TestFindingThePerson:
    def test_by_the_name_on_the_telegram_account(self, db):
        _account(db, name="Роман Сомов", chat_id="111")

        assert resolve_actor_chat_id(db, "Сомов Роман") == "111"

    def test_by_the_name_on_the_account_it_signs_into(self, db):
        """Каст зовёт его «Pavel Petrovich», телеграм — «Сергей Зотов»."""
        user = User(login="tg_900000102", password_hash="x", display_name="Pavel Petrovich")
        db.add(user)
        db.flush()
        _account(db, name="Сергей Зотов", chat_id="900000102", user_id=user.id)

        assert resolve_actor_chat_id(db, "Pavel Petrovich") == "900000102"

    def test_a_stranger_has_no_chat(self, db):
        _account(db, name="Роман Сомов", chat_id="111")

        assert resolve_actor_chat_id(db, "Кто-то Другой") == ""

    def test_nobody_is_not_somebody(self, db):
        _account(db, name="Роман Сомов", chat_id="111")

        assert resolve_actor_chat_id(db, "") == ""

    def test_a_switched_off_account_is_not_written_to(self, db):
        _account(db, name="Роман Сомов", chat_id="111", active="false")

        assert resolve_actor_chat_id(db, "Роман Сомов") == ""


class TestSendingIt:
    def test_it_reaches_the_dictor_and_says_so(self, db, monkeypatch):
        box: list[tuple] = []
        monkeypatch.setattr(
            "app.services.role_approval.send_telegram_message",
            lambda db, text, chat_ids=None, direct=False: box.append((text, chat_ids, direct)) or 1,
        )
        _account(db, name="Роман Сомов", chat_id="111")

        result = notify_role_approved(db, book_id="b1", book_title="Крылья полумрака", role="Дгарнин", actor_name="Роман Сомов")

        assert result["notified"] is True
        text, chat_ids, direct = box[0]
        assert chat_ids == ["111"] and direct is True
        assert "Дгарнин" in text

    def test_an_actor_with_no_telegram_is_reported_not_hidden(self, db, monkeypatch):
        """Владелец должен узнать, что диктор не получил письма, а не думать, что получил."""
        monkeypatch.setattr("app.services.role_approval.send_telegram_message", lambda *args, **kwargs: 1)

        result = notify_role_approved(db, book_id="b1", book_title="Книга", role="Дгарнин", actor_name="Никого Нет")

        assert result["notified"] is False
        assert result["reason"] == "no_telegram"
        assert result["actor_name"] == "Никого Нет"

    def test_taking_an_actor_off_a_role_writes_to_nobody(self, db, monkeypatch):
        box: list = []
        monkeypatch.setattr("app.services.role_approval.send_telegram_message", lambda *a, **k: box.append(a) or 1)

        result = notify_role_approved(db, book_id="b1", book_title="Книга", role="Дгарнин", actor_name="")

        assert result["notified"] is False and result["reason"] == "no_actor"
        assert box == []

    def test_telegram_refusing_the_message_is_not_a_success(self, db, monkeypatch):
        monkeypatch.setattr("app.services.role_approval.send_telegram_message", lambda *a, **k: 0)
        _account(db, name="Роман Сомов", chat_id="111")

        result = notify_role_approved(db, book_id="b1", book_title="Книга", role="Дгарнин", actor_name="Роман Сомов")

        assert result["notified"] is False and result["reason"] == "not_sent"

    def test_the_undecided_mark_alone_sends_nothing(self, db, monkeypatch):
        """«???» разбирается в пустое имя (`parse_actor_name`) — назначать и звать
        на пробу некого, и это не должно даже пытаться постучаться в телеграм."""
        box: list = []
        monkeypatch.setattr("app.services.role_approval.send_telegram_message", lambda *a, **k: box.append(a) or 1)

        result = notify_role_approved(db, book_id="b1", book_title="Книга", role="Дгарнин", actor_name="???")

        assert result["notified"] is False and result["reason"] == "no_actor"
        assert result["actor_name"] == ""
        assert box == []


class TestAnAmbiguousApprovedNameIsNotGuessed:
    """I1: `names_match` нечёткий, и на утверждение (не только на пробу) может
    отозваться больше одной учётки — писать наугад нельзя и здесь."""

    def test_two_matching_accounts_relay_instead_of_guessing(self, db, monkeypatch):
        box: list = []
        monkeypatch.setattr(
            "app.services.role_approval.send_telegram_message",
            lambda db, text, chat_ids=None, direct=False: box.append((text, chat_ids, direct)) or 1,
        )
        _account(db, name="Роман Сомов", chat_id="111")
        _account(db, name="Сомов Роман Второй", chat_id="222")

        result = notify_role_approved(db, book_id="b1", book_title="Книга", role="Дгарнин", actor_name="Роман Сомов")

        assert result["notified"] is False
        assert result["reason"] == "ambiguous"
        assert result["kind"] == "approval"
        assert all(not direct for _text, _chat_ids, direct in box)


class TestATentativeAssignmentIsAnAuditionInvite:
    """«Натали Ким?» — предложение агента, а не решение о назначении. Актёру не «вы
    утверждены», а «вас зовут на пробу»: другой текст, тот же механизм доставки.

    `_normalize_person_name` стирает знак вопроса перед сравнением, поэтому учётку
    ищем явно по имени без «?» (`parse_actor_name`) — не потому, что найти иначе не
    получится, а чтобы приглашение ушло именно предложенному человеку, а не тёзке.
    """

    def test_it_reaches_the_proposed_actor_directly(self, db, monkeypatch):
        box: list = []
        monkeypatch.setattr(
            "app.services.role_approval.send_telegram_message",
            lambda db, text, chat_ids=None, direct=False: box.append((text, chat_ids, direct)) or 1,
        )
        _account(db, name="Натали Ким", chat_id="222")

        result = notify_role_approved(db, book_id="b1", book_title="Книга", role="Химера", actor_name="Натали Ким?")

        assert result["notified"] is True
        assert result["kind"] == "audition"
        assert result["actor_name"] == "Натали Ким"
        assert len(box) == 1
        text, chat_ids, direct = box[0]
        assert chat_ids == ["222"] and direct is True
        assert "пробу" in text and "Химера" in text

    def test_a_stranger_never_gets_it(self, db, monkeypatch):
        """Предложенного имени в системе нет — приглашение никому и не уходит адресно,
        оно уезжает в релей (владельцу), а не постороннему тёзке."""
        box: list = []
        monkeypatch.setattr(
            "app.services.role_approval.send_telegram_message",
            lambda db, text, chat_ids=None, direct=False: box.append((chat_ids, direct)) or 1,
        )
        _account(db, name="Кто-то Другой", chat_id="333")

        result = notify_role_approved(db, book_id="b1", book_title="Книга", role="Химера", actor_name="Натали Ким?")

        assert result["kind"] == "audition"
        assert result["reason"] == "no_telegram"
        assert all(not (direct and chat_ids == ["333"]) for chat_ids, direct in box)

    @pytest.mark.parametrize("order", ["a_then_b", "b_then_a"])
    def test_a_namesake_makes_it_ambiguous_not_a_coin_flip(self, db, monkeypatch, order):
        """«Натали Ким» и «Ким Натали Вторая» пересекаются двумя словами из двух —
        `names_match` фактически считает их одним именем. Раньше первое совпадение
        по порядку запроса тихо получало чужую пробу; порядок вставки в базу не
        должен решать, кому уйдёт личное письмо, — поэтому обе учётки должны
        вернуть один и тот же результат: письма не адресно, а в релей."""
        box: list = []
        monkeypatch.setattr(
            "app.services.role_approval.send_telegram_message",
            lambda db, text, chat_ids=None, direct=False: box.append((text, chat_ids, direct)) or 1,
        )
        accounts = [("Натали Ким", "222"), ("Ким Натали Вторая", "444")]
        if order == "b_then_a":
            accounts.reverse()
        for name, chat_id in accounts:
            _account(db, name=name, chat_id=chat_id)

        result = notify_role_approved(db, book_id="b1", book_title="Книга", role="Химера", actor_name="Натали Ким?")

        assert result["notified"] is False
        assert result["reason"] == "ambiguous"
        assert result["kind"] == "audition"
        assert result["actor_name"] == "Натали Ким"
        assert all(not direct for _text, _chat_ids, direct in box), "никому напрямую — имя неоднозначно"
        to_owner = [text for text, _chat_ids, direct in box if not direct]
        assert to_owner and "неоднозначн" in to_owner[0].lower()

    def test_a_single_token_name_ambiguous_across_two_accounts(self, db, monkeypatch):
        """Однословное имя сверяется мягче (`len(actor_tokens) == 1`): «Натали»
        подходит любой учётке, где это слово есть хоть одним из имён, — а значит
        подходит сразу двум разным людям."""
        box: list = []
        monkeypatch.setattr(
            "app.services.role_approval.send_telegram_message",
            lambda db, text, chat_ids=None, direct=False: box.append((text, chat_ids, direct)) or 1,
        )
        _account(db, name="Натали Ким", chat_id="222")
        _account(db, name="Натали Зотова", chat_id="555")

        result = notify_role_approved(db, book_id="b1", book_title="Книга", role="Химера", actor_name="Натали?")

        assert result["notified"] is False
        assert result["reason"] == "ambiguous"
        assert result["kind"] == "audition"
        assert all(not direct for _text, _chat_ids, direct in box)

    def test_the_same_name_without_the_mark_is_an_approval_not_an_audition(self, db, monkeypatch):
        """Проверка на предварительность, а не на имя: без знака это обычное утверждение."""
        monkeypatch.setattr("app.services.role_approval.send_telegram_message", lambda *a, **k: 1)
        _account(db, name="Натали Ким", chat_id="222")

        result = notify_role_approved(db, book_id="b1", book_title="Книга", role="Химера", actor_name="Натали Ким")

        assert result["notified"] is True
        assert result["kind"] == "approval"


class TestATentativeActorWithNoAccount:
    """Как и у утверждения: нет учётки — письмо идёт владельцу и агентам, не в никуда."""

    def test_it_relays_to_the_owner(self, db, monkeypatch):
        box: list = []
        monkeypatch.setattr(
            "app.services.role_approval.send_telegram_message",
            lambda db, text, chat_ids=None, direct=False: box.append((text, chat_ids, direct)) or 1,
        )

        result = notify_role_approved(db, book_id="b1", book_title="Книга", role="Химера", actor_name="Без Учётки?")

        assert result["notified"] is False
        assert result["reason"] == "no_telegram"
        assert result["kind"] == "audition"
        assert result["actor_name"] == "Без Учётки"
        assert result["relayed"] is True
        to_owner = [call for call in box if call[2] is False]
        assert len(to_owner) == 1
        assert "пробу" in to_owner[0][0] and "Без Учётки" in to_owner[0][0]


class TestAnActorWithNoAccount:
    """Часть актёров агента в системе есть, часть нет, и так будет всегда.

    Раньше на таком назначении система молчала: возвращала `no_telegram` и на этом
    заканчивала. Теперь письмо уходит владельцу и агенту — передайте сами.
    """

    def _agent(self, db):
        user = User(login="tg_900000103", password_hash="x", display_name="Агата Ковалёва")
        db.add(user)
        db.flush()
        db.add(UserRole(user_id=user.id, role="agent"))
        _account(db, name="Агата Ковалёва", chat_id="900000103", user_id=user.id, role="agent")

    def _agent_via_user_roles(self, db):
        """Как на живой базе: у учётки телеграм-доступа обычная роль ('dictor'),
        агентом её делает только строка в `user_roles`."""
        user = User(login="tg_900000103", password_hash="x", display_name="Агата Ковалёва")
        db.add(user)
        db.flush()
        db.add(UserRole(user_id=user.id, role="agent"))
        _account(db, name="Агата Ковалёва", chat_id="900000103", user_id=user.id, role="dictor")

    def _agent_via_account_role(self, db):
        """Обратный случай: `user_roles` про неё ничего не знает, агентом её делает
        только `role` самой учётки телеграм-доступа."""
        _account(db, name="Агата Ковалёва", chat_id="900000103", role="agent")

    def test_the_owner_and_the_agent_are_told(self, db, monkeypatch):
        """Два вызова, а не один: без `direct` функция выбрасывает `chat_ids` и шлёт
        только владельцу. Проверяется, что агент получил своё письмо адресно."""
        box: list = []
        monkeypatch.setattr(
            "app.services.role_approval.send_telegram_message",
            lambda db, text, chat_ids=None, direct=False: box.append((text, chat_ids, direct)) or 1,
        )
        self._agent(db)

        result = notify_role_approved(db, book_id="b1", book_title="Книга", role="Химера", actor_name="Без Учётки")

        assert result["notified"] is False and result["reason"] == "no_telegram"
        assert result["relayed"] is True

        to_owner = [call for call in box if call[2] is False]
        to_agent = [call for call in box if call[2] is True]
        assert len(to_owner) == 1, "владельцу — вызовом без direct, иначе подмена не сработает"
        assert to_agent and "900000103" in (to_agent[0][1] or [])
        assert "Без Учётки" in to_owner[0][0] and "Химера" in to_owner[0][0]

    def test_the_message_says_the_actor_has_no_account(self, db, monkeypatch):
        """Иначе владелец решит, что человек предупреждён, и будет ждать записи."""
        box: list = []
        monkeypatch.setattr(
            "app.services.role_approval.send_telegram_message",
            lambda db, text, chat_ids=None, direct=False: box.append(text) or 1,
        )
        self._agent(db)

        notify_role_approved(db, book_id="b1", book_title="Книга", role="Химера", actor_name="Без Учётки")

        assert "учётки" in box[0]

    def test_the_owner_hears_it_even_with_no_agent_at_all(self, db, monkeypatch):
        box: list = []
        monkeypatch.setattr(
            "app.services.role_approval.send_telegram_message",
            lambda db, text, chat_ids=None, direct=False: box.append((text, chat_ids)) or 1,
        )

        result = notify_role_approved(db, book_id="b1", book_title="Книга", role="Химера", actor_name="Без Учётки")

        assert result["relayed"] is True
        assert len(box) == 1, "агентов нет — второго вызова быть не должно"
        assert box[0][1] is None

    def test_a_switched_off_agent_is_not_written_to(self, db, monkeypatch):
        box: list = []
        monkeypatch.setattr(
            "app.services.role_approval.send_telegram_message",
            lambda db, text, chat_ids=None, direct=False: box.append(chat_ids) or 1,
        )
        _account(db, name="Агата Ковалёва", chat_id="900000103", role="agent", active="false")

        notify_role_approved(db, book_id="b1", book_title="Книга", role="Химера", actor_name="Без Учётки")

        assert all("900000103" not in (chat_ids or []) for chat_ids in box)

    def test_user_roles_alone_reaches_her(self, db, monkeypatch):
        """`agent_chat_ids` ищет роль в двух местах, и `_agent()` выше задаёт оба
        сразу — ломая любую из веток по отдельности, ни один тест этого не заметит.
        Это ветка живой базы: там у любого недиктора учётка помечена 'dictor', и
        агентом её делает только `user_roles`."""
        box: list = []
        monkeypatch.setattr(
            "app.services.role_approval.send_telegram_message",
            lambda db, text, chat_ids=None, direct=False: box.append((text, chat_ids, direct)) or 1,
        )
        self._agent_via_user_roles(db)

        result = notify_role_approved(db, book_id="b1", book_title="Книга", role="Химера", actor_name="Без Учётки")

        assert result["relayed"] is True
        to_agent = [call for call in box if call[2] is True]
        assert to_agent and "900000103" in (to_agent[0][1] or [])

    def test_the_account_role_alone_reaches_her(self, db, monkeypatch):
        """Симметричная ветка: `user_roles` про неё ничего не знает, находит её
        только `role` на самой учётке телеграм-доступа."""
        box: list = []
        monkeypatch.setattr(
            "app.services.role_approval.send_telegram_message",
            lambda db, text, chat_ids=None, direct=False: box.append((text, chat_ids, direct)) or 1,
        )
        self._agent_via_account_role(db)

        result = notify_role_approved(db, book_id="b1", book_title="Книга", role="Химера", actor_name="Без Учётки")

        assert result["relayed"] is True
        to_agent = [call for call in box if call[2] is True]
        assert to_agent and "900000103" in (to_agent[0][1] or [])


class TestShouldNotifyActorChange:
    """Кладётся на месте вызова в `app/api/budget_api.py`, чтобы решить, стоит ли
    звать `notify_role_approved` вовсе — сравнением РАЗОБРАННОГО имени, а не сырой
    строки поля."""

    def test_unchanged_name_is_not_news(self):
        assert should_notify_actor_change("Роман Сомов", "Роман Сомов") is False

    def test_a_new_assignment_is_news(self):
        assert should_notify_actor_change("", "Роман Сомов") is True

    def test_a_different_person_is_news_in_either_status(self):
        assert should_notify_actor_change("Роман Сомов", "Натали Ким") is True
        assert should_notify_actor_change("Роман Сомов?", "Натали Ким?") is True

    def test_removing_the_question_mark_is_the_approval_news(self):
        """«Иван?» → «Иван» — то самое «утверждён», старый путь его посылает."""
        assert should_notify_actor_change("Иван?", "Иван") is True

    def test_adding_the_question_mark_to_an_approved_name_is_not_news(self):
        """M2, решение владельца: переписать утверждённого в предложение того же
        человека — не новость, письма быть не должно."""
        assert should_notify_actor_change("Иван", "Иван?") is False

    def test_cosmetic_extra_question_marks_are_not_a_change(self):
        """«Имя?» → «Имя??» — тот же разобранный человек в том же статусе."""
        assert should_notify_actor_change("Иван?", "Иван??") is False

    def test_a_stray_space_before_the_mark_is_not_a_change(self):
        assert should_notify_actor_change("Иван?", "Иван ?") is False

    def test_a_vote_round_trip_back_to_the_same_tentative_name_is_not_news(self):
        assert should_notify_actor_change("Иван?", "Иван?") is False

    def test_clearing_the_actor_is_not_news(self):
        """Снятие актёра — `notify_role_approved` тут ни при чём, извещать некого."""
        assert should_notify_actor_change("Иван", "") is False

    def test_the_undecided_mark_alone_is_not_news(self):
        assert should_notify_actor_change("", "???") is False
        assert should_notify_actor_change("Иван", "???") is False

    def test_a_full_round_trip_ends_silent(self):
        """«Иван» → «Иван?» (M2, тихо) → «Иван» (approval, письмо) → «Иван?» (снова
        M2, тихо) — круг не возвращается к первому приглашению: второй раз «Иван?»
        уже ничего не посылает, потому что непосредственно предыдущим значением
        был утверждённый «Иван», а не пустота."""
        assert should_notify_actor_change("Иван", "Иван?") is False
        assert should_notify_actor_change("Иван?", "Иван") is True
        assert should_notify_actor_change("Иван", "Иван?") is False


def test_the_approval_notice_lists_the_other_books_of_the_cycle():
    from app.services.role_approval import approval_notice

    text = approval_notice(role="Куйбу Дегатти", book_title="Сказки волшебников 1",
                           also_in=["Сказки волшебников 2", "Крылья Полумрака"])
    assert "Сказки волшебников 1" in text
    assert "Также в книгах: Сказки волшебников 2, Крылья Полумрака" in text


def test_without_other_books_the_notice_is_unchanged():
    from app.services.role_approval import approval_notice

    assert "Также в книгах" not in approval_notice(role="Куйбу", book_title="Книга")

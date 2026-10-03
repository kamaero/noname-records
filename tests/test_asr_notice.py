"""Диктору сообщают, каких его реплик не нашлось.

Вы просили сигнализировать обоим — вам и диктору. Ваша половина была: число на экране.
Его половина — это письмо, потому что на экран он заходит, когда сам захочет, а
перезаписывать надо, пока он ещё у микрофона.

Молчание тут дороже ошибки в обе стороны: не сказали — глава ждёт неизвестно чего;
сказали лишнего — диктор перепишет то, что и так на месте.
"""
import pytest

from app.services.asr_notice import missing_lines_notice


def _line(text, matched=True):
    return {"text": text, "matched": matched}


class TestWhatItSays:
    def test_it_names_the_chapter_the_role_and_the_count(self):
        text = missing_lines_notice(
            role="Бдеукс", chapter="Глава 15. От тебя такого не ожидал",
            book_title="Крылья полумрака",
            lines=[_line("есть"), _line("первая пропущенная", matched=False), _line("вторая пропущенная", matched=False)],
        )

        assert "Бдеукс" in text
        assert "Глава 15" in text
        assert "2" in text

    def test_it_quotes_the_lines_so_he_knows_what_to_read(self):
        text = missing_lines_notice(
            role="Бдеукс", chapter="Глава 15", book_title="Крылья полумрака",
            lines=[_line("Но кого-то особенно сильно?!", matched=False)],
        )

        assert "Но кого-то особенно сильно?!" in text

    def test_a_long_line_is_cut_not_dumped_whole(self):
        text = missing_lines_notice(
            role="Бдеукс", chapter="Глава 15", book_title="К",
            lines=[_line("а" * 400, matched=False)],
        )

        assert len(text) < 500

    def test_many_misses_are_counted_not_all_listed(self):
        text = missing_lines_notice(
            role="Бдеукс", chapter="Глава 15", book_title="К",
            lines=[_line(f"реплика номер {n}", matched=False) for n in range(12)],
        )

        assert "и ещё" in text
        assert text.count("реплика номер") <= 6


class TestWhenToSayNothing:
    def test_a_role_read_whole_gets_no_letter(self):
        assert missing_lines_notice(role="Бдеукс", chapter="Глава 15", book_title="К",
                                    lines=[_line("есть"), _line("и эта есть")]) == ""

    def test_neither_does_an_empty_alignment(self):
        assert missing_lines_notice(role="Бдеукс", chapter="Глава 15", book_title="К", lines=[]) == ""


class TestSendingIt:
    """Письмо уходит тому, кто читал, — тем же путём, что и «ты утверждён на роль»."""

    @pytest.fixture()
    def db(self):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        from app.db import Base
        from app.models import TelegramAuthAccount

        engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(engine)
        with sessionmaker(bind=engine)() as session:
            session.add(TelegramAuthAccount(telegram_user_id="111", role="dictor",
                                            display_name="Сергей Зотов", is_active="true"))
            session.flush()
            yield session

    def test_it_reaches_the_actor_who_read(self, db, monkeypatch):
        from app.services import asr_notice

        box = []
        monkeypatch.setattr(asr_notice, "send_telegram_message",
                            lambda db, text, chat_ids=None, direct=False: box.append((text, chat_ids, direct)) or 1)

        result = asr_notice.notify_missing_lines(
            db, actor_name="Зотов Сергей", role="Бдеукс", chapter="Глава 15",
            book_title="Крылья полумрака", book_id="b1",
            lines=[{"text": "пропущенная", "matched": False}],
        )

        assert result["notified"] is True
        text, chat_ids, direct = box[0]
        assert chat_ids == ["111"] and direct is True
        assert "пропущенная" in text

    def test_nothing_is_sent_when_nothing_is_missing(self, db, monkeypatch):
        from app.services import asr_notice

        box = []
        monkeypatch.setattr(asr_notice, "send_telegram_message", lambda *a, **k: box.append(a) or 1)

        result = asr_notice.notify_missing_lines(
            db, actor_name="Зотов Сергей", role="Бдеукс", chapter="Глава 15",
            book_title="К", book_id="b1", lines=[{"text": "есть", "matched": True}],
        )

        assert result["notified"] is False and result["reason"] == "nothing_missing"
        assert box == []

    def test_an_actor_without_telegram_is_reported(self, db, monkeypatch):
        from app.services import asr_notice

        monkeypatch.setattr(asr_notice, "send_telegram_message", lambda *a, **k: 1)

        result = asr_notice.notify_missing_lines(
            db, actor_name="Никого Нет", role="Бдеукс", chapter="Глава 15",
            book_title="К", book_id="b1", lines=[{"text": "пропущенная", "matched": False}],
        )

        assert result == {"notified": False, "reason": "no_telegram", "actor_name": "Никого Нет", "missing": 1}


def test_moved_lines_notice_names_the_role_and_quotes_the_lines():
    from app.services.asr_notice import moved_lines_notice

    text = moved_lines_notice(role="Гамук", chapter="Глава 11. Дорога", book_title="Крылья полумрака",
                              texts=["Я знаю короткую дорогу к реке."], link="https://x/role")

    assert text.startswith("✏️ В разметке поправили роль — эти реплики теперь ваши, а в записи их нет: 1")
    assert "Роль: Гамук" in text
    assert "Крылья полумрака · Глава 11. Дорога" in text
    assert "• Я знаю короткую дорогу к реке." in text
    assert "Допишите их отдельным файлом." in text
    assert text.endswith("https://x/role")


def test_moved_lines_notice_is_empty_without_lines():
    from app.services.asr_notice import moved_lines_notice

    assert moved_lines_notice(role="Гамук", chapter="Г", book_title="К", texts=[]) == ""


class TestAmbiguousActorName:
    """`names_match` нечёткий (пересечение слов), и короткому/частому имени может
    отозваться больше одной учётки — тем же способом, что в `role_approval.py`
    (`resolve_actor_candidates`). Письмо о репликах адресовано конкретному человеку
    у микрофона, писать наугад нельзя: неоднозначное имя уходит в релей владельцу и
    агентам, а не первому, кто подвернулся по порядку запроса."""

    @pytest.fixture()
    def db(self):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        from app.db import Base

        engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(engine)
        with sessionmaker(bind=engine)() as session:
            yield session

    def _two_matching_accounts(self, db, order):
        from app.models import TelegramAuthAccount

        pairs = [("Натали Ким", "222"), ("Ким Натали Вторая", "444")]
        if order == "b_then_a":
            pairs.reverse()
        for name, chat_id in pairs:
            db.add(TelegramAuthAccount(telegram_user_id=chat_id, role="dictor", display_name=name, is_active="true"))
        db.flush()

    @pytest.mark.parametrize("order", ["a_then_b", "b_then_a"])
    def test_missing_lines_relay_instead_of_guessing(self, db, monkeypatch, order):
        from app.services import asr_notice, role_approval

        self._two_matching_accounts(db, order)
        box = []
        # Релей идёт через `role_approval.relay_to_owner_and_agents`, у которого своя
        # ссылка на `send_telegram_message` — подменять обе, иначе релей молча уйдёт
        # в настоящую (в тестах отключённую) отправку и `relayed` окажется `False`.
        spy = lambda db, text, chat_ids=None, direct=False: box.append((text, chat_ids, direct)) or 1
        monkeypatch.setattr(asr_notice, "send_telegram_message", spy)
        monkeypatch.setattr(role_approval, "send_telegram_message", spy)

        result = asr_notice.notify_missing_lines(
            db, actor_name="Натали Ким", role="Бдеукс", chapter="Глава 15",
            book_title="Крылья полумрака", book_id="b1",
            lines=[{"text": "пропущенная", "matched": False}],
        )

        assert result["notified"] is False
        assert result["reason"] == "ambiguous"
        assert result["missing"] == 1
        assert result["relayed"] is True
        assert all(not direct for _text, _chat_ids, direct in box), "никому напрямую — имя неоднозначно"
        to_owner = [text for text, _chat_ids, direct in box if not direct]
        assert to_owner and "Натали Ким" in to_owner[0] and "пропущенных репликах" in to_owner[0]
        assert "Ким Натали Вторая" in to_owner[0]

    @pytest.mark.parametrize("order", ["a_then_b", "b_then_a"])
    def test_moved_lines_relay_instead_of_guessing(self, db, monkeypatch, order):
        from app.services import asr_notice, role_approval

        self._two_matching_accounts(db, order)
        box = []
        spy = lambda db, text, chat_ids=None, direct=False: box.append((text, chat_ids, direct)) or 1
        monkeypatch.setattr(asr_notice, "send_telegram_message", spy)
        monkeypatch.setattr(role_approval, "send_telegram_message", spy)

        result = asr_notice.notify_moved_lines(
            db, actor_name="Натали Ким", role="Гамук", chapter="Глава 11",
            book_title="Крылья полумрака", book_id="b1",
            texts=["Я знаю короткую дорогу к реке."],
        )

        assert result["notified"] is False
        assert result["reason"] == "ambiguous"
        assert result["relayed"] is True
        assert all(not direct for _text, _chat_ids, direct in box)
        to_owner = [text for text, _chat_ids, direct in box if not direct]
        assert to_owner and "перенесённых репликах" in to_owner[0]

    def test_a_single_match_is_unaffected(self, db, monkeypatch):
        """Одна подходящая учётка — письмо уходит ей напрямую, как и до фикса."""
        from app.models import TelegramAuthAccount
        from app.services import asr_notice

        db.add(TelegramAuthAccount(telegram_user_id="111", role="dictor", display_name="Сергей Зотов", is_active="true"))
        db.flush()
        box = []
        monkeypatch.setattr(
            asr_notice, "send_telegram_message",
            lambda db, text, chat_ids=None, direct=False: box.append((text, chat_ids, direct)) or 1,
        )

        result = asr_notice.notify_missing_lines(
            db, actor_name="Зотов Сергей", role="Бдеукс", chapter="Глава 15",
            book_title="Крылья полумрака", book_id="b1",
            lines=[{"text": "пропущенная", "matched": False}],
        )

        assert result == {"notified": True, "reason": "sent", "actor_name": "Зотов Сергей", "missing": 1}
        text, chat_ids, direct = box[0]
        assert chat_ids == ["111"] and direct is True

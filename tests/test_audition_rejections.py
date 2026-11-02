"""Отказ по пробе — при утверждении роли (с 03.10). Письмо живому человеку не отзовёшь,
поэтому почти все тесты — про то, когда письма НЕ должно быть.
"""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import AudioFile, AuditionRejection, AuditionScore, Character, ScriptBook
from app.services import audition_rejections as ar
from app.services.audio_uploads import derive_book_code

NOW = datetime(2026, 9, 25, 12, 0, 0)


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        yield session


def _book(db, title="Карта приозерья"):
    book = ScriptBook(title=title, source_filename="k.txt", source_format="txt", created_at=NOW)
    db.add(book)
    db.flush()
    return book


def _audition(db, book, *, role="Пяднтикух", actor="Примеронгялюцз Примерчюнгнчэлв", book_code=None):
    item = AudioFile(
        book_code=book_code if book_code is not None else derive_book_code(book.title),
        original_filename="p.wav", stored_key=f"k/{role}/{actor}.wav", mime_type="audio/wav",
        size_bytes=10, chapter="", role=role, actor_name=actor, kind="audition", uploaded_at=NOW,
    )
    db.add(item)
    db.commit()
    return item


def _rejections(db):
    return db.query(AuditionRejection).all()



# ---------- рассылка ----------

def _approve(db, book, *, role="Пяднтикух", actor="Примерктенчечж Примерптмюгэюц"):
    """Утвердили `actor` на роль — так, как это делает ручка каста: имя в карточке и событие."""
    ch = db.query(Character).filter_by(book_id=book.id, name=role).one_or_none()
    if ch is None:
        ch = Character(book_id=book.id, name=role, actor_name="")
        db.add(ch)
    ch.actor_name = actor
    db.flush()
    ar.schedule_rejections_for_approval(db, book=book, role=role, approved_actor=actor, now=NOW)
    db.commit()


def _due(db, *, actor="Примеронгялюцз Примерчюнгнчэлв", role="Пяднтикух"):
    book = _book(db)
    item = _audition(db, book, role=role, actor=actor)
    _approve(db, book, role=role)
    return book, item


@pytest.fixture()
def mail(monkeypatch):
    sent = {"direct": [], "relay": []}

    def send(db, text, chat_ids=None, direct=False, **_):
        assert direct, "письмо актёру без direct=True уйдёт владельцу"
        sent["direct"].append((tuple(chat_ids or ()), text))
        return 1

    monkeypatch.setattr(ar, "send_telegram_message", send)
    monkeypatch.setattr(ar, "relay_to_owner_and_agents", lambda db, text: sent["relay"].append(text) or True)
    monkeypatch.setattr(ar, "resolve_actor_candidates", _resolve_people)
    return sent


#: учётки Telegram в тестах: каждому — свой чат, Панков — «111». Раньше фикстура
#: находила «111» по любому имени, то есть утверждённый и отвергнутый были одним
#: человеком — с проверкой победителя по чату такой мир отменял бы все письма.
PEOPLE = {"Примеронгялюцз Примерчюнгнчэлв": "111"}


def _resolve_people(db, name):
    for known, chat_id in PEOPLE.items():
        if ar.names_match(known, name) or ar.names_match(name, known):
            return [(chat_id, known)]
    return [(f"tg-{name}", name)]


LATER = NOW + timedelta(minutes=15)


def test_the_letter_word_for_word():
    assert ar.rejection_notice(role="Пяднтикух", book_title='Пяволов - "Карта Приозерья"') == (
        "🎙 Noname Records — про вашу пробу\n"
        "Роль: «Пяднтикух»\n"
        'Книга: Пяволов - "Карта Приозерья"\n'
        "\n"
        "Спасибо, что попробовались! В этот раз на персонажа «Пяднтикух» выбран другой голос. "
        "Запись тут ни при чём — просто этот герой должен звучать немного иначе.\n"
        "\n"
        "Не прощаемся: героев много, и среди них наверняка есть тот, с кем ваш голос "
        "сойдётся с первого слова 🙂"
    )


def test_nothing_goes_out_before_the_deadline(db, mail):
    _due(db)

    assert ar.send_due_rejections(db, now=LATER - timedelta(seconds=1)) == []
    assert mail["direct"] == []


def test_a_ripe_rejection_goes_to_the_actor_once(db, mail):
    _due(db)

    [(_, status)] = ar.send_due_rejections(db, now=LATER)
    assert ar.send_due_rejections(db, now=LATER + timedelta(minutes=5)) == []

    assert status == "sent"
    [(chat_ids, text)] = mail["direct"]
    assert chat_ids == ("111",) and "«Пяднтикух»" in text
    rej = _rejections(db)[0]
    assert (rej.status, rej.sent_at) == ("sent", LATER)


def test_approved_by_the_deadline_means_no_letter(db, mail):
    book, _ = _due(db)
    # утверждение переиграли в пользу Панкова — без нового события, только карточка
    db.query(Character).filter_by(book_id=book.id, name="Пяднтикух").one().actor_name = "Примерчюнгнчэлв Примеронгялюцз"
    db.commit()

    assert ar.send_due_rejections(db, now=LATER) == [(_rejections(db)[0].id, "cancelled")]
    assert mail["direct"] == []


def test_a_role_back_to_tentative_by_the_deadline_means_no_letter(db, mail):
    book, _ = _due(db)
    db.query(Character).filter_by(book_id=book.id, name="Пяднтикух").one().actor_name = "Примерптмюгэюц Примерктенчечж?"
    db.commit()

    [(_, status)] = ar.send_due_rejections(db, now=LATER)
    assert status == "cancelled" and mail["direct"] == []


def test_no_account_goes_to_the_owner(db, mail, monkeypatch):
    monkeypatch.setattr(ar, "resolve_actor_candidates", lambda db, name: [])
    _due(db)

    [(_, status)] = ar.send_due_rejections(db, now=LATER)

    assert status == "no_account" and mail["direct"] == []
    [relay] = mail["relay"]
    assert "Примеронгялюцз Примерчюнгнчэлв" in relay and "не доставлен" in relay


def test_an_ambiguous_name_goes_to_the_owner_with_candidates(db, mail, monkeypatch):
    monkeypatch.setattr(ar, "resolve_actor_candidates", lambda db, name: (
        [("1", "Примеронгялюцз Примерчюнгнчэлв"), ("2", "Роман Пан")] if name == "Примеронгялюцз Примерчюнгнчэлв" else _resolve_people(db, name)
    ))
    _due(db)

    [(_, status)] = ar.send_due_rejections(db, now=LATER)

    assert status == "ambiguous" and mail["direct"] == []
    assert "Роман Пан" in mail["relay"][0]


def test_telegram_silence_is_failed_and_not_retried(db, mail, monkeypatch):
    monkeypatch.setattr(ar, "send_telegram_message", lambda *a, **k: 0)
    _due(db)

    assert [s for _, s in ar.send_due_rejections(db, now=LATER)] == ["failed"]
    assert ar.send_due_rejections(db, now=LATER + timedelta(hours=1)) == []


def test_each_row_is_committed_before_the_next(db, mail, monkeypatch):
    book = _book(db)
    _audition(db, book, actor="Примеронгялюцз Примерчюнгнчэлв")
    _audition(db, book, actor="Примерктенчечж Примерптмюгэюц")
    _approve(db, book, actor="Примервсммчпфиб Примерубщркгюд")
    calls = []

    def send(db_, text, chat_ids=None, direct=False, **_):
        calls.append(text)
        if len(calls) == 2:
            raise RuntimeError("telegram упал")
        return 1

    monkeypatch.setattr(ar, "send_telegram_message", send)
    ar.send_due_rejections(db, now=LATER)

    assert sorted(r.status for r in _rejections(db)) == ["failed", "sent"]


def test_a_crash_after_sending_never_sends_twice(db, mail, monkeypatch):
    """Письмо ушло, а процесс умер до фиксации: следующий проход не должен слать его снова."""
    _due(db)
    calls = []

    def send_then_die(db_, text, chat_ids=None, direct=False, **_):
        calls.append(text)
        raise KeyboardInterrupt("процесс убит после отправки")

    monkeypatch.setattr(ar, "send_telegram_message", send_then_die)
    with pytest.raises(KeyboardInterrupt):
        ar.send_due_rejections(db, now=LATER)
    db.rollback()
    monkeypatch.setattr(ar, "send_telegram_message", lambda *a, **k: calls.append("again") or 1)

    ar.send_due_rejections(db, now=LATER + timedelta(minutes=1))

    assert len(calls) == 1
    assert _rejections(db)[0].status == "failed"


def test_one_broken_row_does_not_block_the_rest(db, mail, monkeypatch):
    book = _book(db)
    _audition(db, book, actor="Примеронгялюцз Примерчюнгнчэлв")
    _audition(db, book, actor="Примерктенчечж Примерптмюгэюц")
    _approve(db, book, actor="Примервсммчпфиб Примерубщркгюд")

    def resolve(db_, name):
        if name == "Примеронгялюцз Примерчюнгнчэлв":
            raise RuntimeError("база занята")
        return [("222", name)] if name == "Примерктенчечж Примерптмюгэюц" else [(f"tg-{name}", name)]

    monkeypatch.setattr(ar, "resolve_actor_candidates", resolve)
    ar.send_due_rejections(db, now=LATER)
    ar.send_due_rejections(db, now=LATER + timedelta(minutes=1))

    statuses = {r.actor_name: r.status for r in _rejections(db)}
    assert statuses == {"Примеронгялюцз Примерчюнгнчэлв": "failed", "Примерктенчечж Примерптмюгэюц": "sent"}
    assert [ids for ids, _ in mail["direct"]] == [("222",)]


# ---------- заведение отказов утверждением ----------

def test_approval_schedules_a_rejection_to_everybody_else_in_fifteen_minutes(db):
    book = _book(db)
    for actor in ("Примеронгялюцз Примерчюнгнчэлв", "Примерктенчечж Примерптмюгэюц", "Ветрова Ольга"):
        _audition(db, book, actor=actor)

    _approve(db, book, actor="Примерптмюгэюц Примерктенчечж")

    rows = {r.actor_name: (r.status, r.due_at) for r in _rejections(db)}
    assert rows == {"Примеронгялюцз Примерчюнгнчэлв": ("pending", LATER), "Ветрова Ольга": ("pending", LATER)}


def test_four_auditions_of_one_actor_are_one_rejection(db):
    book = _book(db)
    for _ in range(4):
        _audition(db, book)
    _approve(db, book)
    assert len(_rejections(db)) == 1


def test_another_role_and_another_book_are_untouched(db):
    book = _book(db)
    other = _book(db, title="Сезон ветров")
    _audition(db, book, role="Сылядир")
    _audition(db, other, role="Пяднтикух")
    _approve(db, book)
    assert _rejections(db) == []


def test_reapproval_within_fifteen_minutes_moves_the_rejection(db, mail):
    """Утвердили Ивашкину, через 5 минут передумали в пользу Панкова: письмо уходит
    Ивашкиной (новым сроком), а не Панкову."""
    book = _book(db)
    _audition(db, book, actor="Примеронгялюцз Примерчюнгнчэлв")
    _audition(db, book, actor="Примерктенчечж Примерптмюгэюц")
    _approve(db, book, actor="Примерктенчечж Примерптмюгэюц")
    ch = db.query(Character).filter_by(book_id=book.id, name="Пяднтикух").one()
    ch.actor_name = "Примеронгялюцз Примерчюнгнчэлв"
    ar.schedule_rejections_for_approval(db, book=book, role="Пяднтикух", approved_actor="Примеронгялюцз Примерчюнгнчэлв",
                                        now=NOW + timedelta(minutes=5))
    db.commit()

    done = dict((r.actor_name, r) for r in _rejections(db))
    assert done["Примерктенчечж Примерптмюгэюц"].due_at == LATER + timedelta(minutes=5)
    # отказ новому победителю гаснет сразу при его утверждении (с 04.10), а не к сроку
    assert done["Примеронгялюцз Примерчюнгнчэлв"].status == "cancelled"
    assert ar.send_due_rejections(db, now=LATER) == []
    assert mail["direct"] == []


def test_a_settled_rejection_is_never_reopened(db):
    book = _book(db)
    _audition(db, book)
    _approve(db, book)
    _rejections(db)[0].status = "sent"
    db.commit()

    _approve(db, book, actor="Примервсммчпфиб Примерубщркгюд")

    assert [r.status for r in _rejections(db)] == ["sent"]


def test_approval_never_rejects_the_approved_actor_spelled_differently(db):
    book = _book(db)
    _audition(db, book, actor="Примеронгялюцз Примерчюнгнчэлв")
    _approve(db, book, actor="Примерчюнгнчэлв Примеронгялюцз")
    assert _rejections(db) == []


def test_an_audition_without_actor_name_gets_no_rejection(db):
    book = _book(db)
    _audition(db, book, actor="")
    _approve(db, book)
    assert _rejections(db) == []


def test_no_auditions_left_by_the_deadline_means_no_letter(db, mail):
    """Страховка от гонки: пробу убрали мимо `forget_audition`."""
    _due(db)
    db.query(AudioFile).delete()
    db.commit()

    [(_, status)] = ar.send_due_rejections(db, now=LATER)
    assert status == "cancelled" and mail["direct"] == []


# ---------- удаление пробы ----------

def test_deleting_the_last_audition_of_a_pair_takes_scores_and_cancels(db):
    book, item = _due(db)
    db.add(AuditionScore(book_id=book.id, role="Пяднтикух", actor_name="Примеронгялюцз Примерчюнгнчэлв", lane="author",
                         voter_uid="a", voter_name="Автор", score=3))
    db.commit()

    ar.forget_audition(db, item)
    db.commit()

    assert db.query(AuditionScore).count() == 0
    assert _rejections(db)[0].status == "cancelled"


def test_deleting_one_of_two_auditions_keeps_scores_and_rejection(db):
    book, item = _due(db)
    _audition(db, book)
    db.add(AuditionScore(book_id=book.id, role="Пяднтикух", actor_name="Примеронгялюцз Примерчюнгнчэлв", lane="author",
                         voter_uid="a", voter_name="Автор", score=3))
    db.commit()

    ar.forget_audition(db, item)
    db.commit()

    assert db.query(AuditionScore).count() == 1
    assert _rejections(db)[0].status == "pending"


# ---------- один человек — одно письмо (финальное ревью 03.10) ----------

def test_the_winner_under_another_name_gets_no_letter(db, mail, monkeypatch):
    """Проба залита под телеграм-именем, утверждён под именем из каста: имена разные,
    а учётка одна. Отказ за выигранную роль — худшее письмо из возможных."""
    monkeypatch.setitem(PEOPLE, "Примербояюррнэв Примержзаэезяхв", "777")
    monkeypatch.setitem(PEOPLE, "Примервсммчпфиб Примерубщркгюд", "777")
    book = _book(db)
    _audition(db, book, actor="Примербояюррнэв Примержзаэезяхв")
    _approve(db, book, actor="Примервсммчпфиб Примерубщркгюд")

    [(_, status)] = ar.send_due_rejections(db, now=LATER)

    assert status == "cancelled"
    assert mail["direct"] == [] and mail["relay"] == []


def test_two_spellings_of_one_loser_get_one_letter(db, mail, monkeypatch):
    monkeypatch.setitem(PEOPLE, "Примербояюррнэв Примержзаэезяхв", "777")
    monkeypatch.setitem(PEOPLE, "Примервсммчпфиб Примерубщркгюд", "777")
    book = _book(db)
    _audition(db, book, actor="Примербояюррнэв Примержзаэезяхв")
    _audition(db, book, actor="Примервсммчпфиб Примерубщркгюд")
    _approve(db, book, actor="Примерктенчечж Примерптмюгэюц")

    statuses = sorted(s for _, s in ar.send_due_rejections(db, now=LATER))

    assert statuses == ["cancelled", "sent"]
    assert [ids for ids, _ in mail["direct"]] == [("777",)]


def test_approval_landing_while_claiming_cancels_the_letter(db, mail, monkeypatch):
    """Чтения таймера идут вне транзакции, а захват строки может ждать сохранения каста,
    которое утверждает этого же актёра. После захвата всё проверяется заново."""
    book, _ = _due(db)
    calls = []

    def resolve_and_reapprove(db_, name):
        if not calls:
            db_.query(Character).filter_by(book_id=book.id, name="Пяднтикух").one().actor_name = "Примеронгялюцз Примерчюнгнчэлв"
            db_.commit()
        calls.append(name)
        return _resolve_people(db_, name)

    monkeypatch.setattr(ar, "resolve_actor_candidates", resolve_and_reapprove)

    [(_, status)] = ar.send_due_rejections(db, now=LATER)

    assert status == "cancelled"
    assert mail["direct"] == [] and mail["relay"] == []
    assert _rejections(db)[0].sent_at is None


def test_an_unknown_uploader_gets_no_rejection(db):
    """«Unknown» — подстановка загрузки, а не человек: писать некому."""
    book = _book(db)
    _audition(db, book, actor="Unknown")
    _approve(db, book)
    assert _rejections(db) == []


# ---------- роль в пробе написана иначе, чем в касте ----------

def test_approval_reaches_auditions_whose_role_is_spelled_differently(db, mail):
    """Проба «тик-так» (из имени файла), роль в касте «Тик-Так»: отказ всё равно заводится
    и уходит — иначе проигравшие на такую роль не узнали бы ничего."""
    book = _book(db)
    _audition(db, book, role="тик-так", actor="Примеронгялюцз Примерчюнгнчэлв")
    db.add(Character(book_id=book.id, name="Тик-Так", actor_name="Примергдпнкюцю Примероюбргфюу"))
    db.flush()
    ar.schedule_rejections_for_approval(db, book=book, role="Тик-Так", approved_actor="Примергдпнкюцю Примероюбргфюу", now=NOW)
    db.commit()

    [rej] = _rejections(db)
    assert (rej.role, rej.actor_name, rej.status) == ("тик-так", "Примеронгялюцз Примерчюнгнчэлв", "pending")
    assert [s for _, s in ar.send_due_rejections(db, now=LATER)] == ["sent"]


def test_approval_reaches_auditions_named_by_an_alias(db):
    book = _book(db)
    _audition(db, book, role="Отец Чудовищ", actor="Примеронгялюцз Примерчюнгнчэлв")
    db.add(Character(book_id=book.id, name="Убкнэляб", aliases="Отец Чудовищ", actor_name="Примергдпнкюцю Примероюбргфюу"))
    db.flush()
    ar.schedule_rejections_for_approval(db, book=book, role="Убкнэляб", aliases="Отец Чудовищ",
                                        approved_actor="Примергдпнкюцю Примероюбргфюу", now=NOW)
    db.commit()

    assert [(r.role, r.actor_name) for r in _rejections(db)] == [("Отец Чудовищ", "Примеронгялюцз Примерчюнгнчэлв")]


# ---------- отказ по оценкам: оба оценили, обе ниже 7 (решение владельца 04.10) ----------

def _score(db, book, lane, score, *, role="Пяднтикух", actor="Примеронгялюцз Примерчюнгнчэлв"):
    row = db.query(AuditionScore).filter_by(book_id=book.id, role=role, actor_name=actor, lane=lane).one_or_none()
    if score is None:
        if row is not None:
            db.delete(row)
    elif row is None:
        db.add(AuditionScore(book_id=book.id, role=role, actor_name=actor, lane=lane,
                             voter_uid=lane, voter_name=lane, score=score))
    else:
        row.score = score
    db.flush()
    ar.sync_rejection_with_scores(db, book_id=book.id, role=role, actor_name=actor, now=NOW)
    db.commit()


def test_both_scores_below_seven_schedule_a_rejection_that_goes_out(db, mail):
    book = _book(db)
    _audition(db, book)
    _score(db, book, "author", 5)
    assert _rejections(db) == []          # оценил только автор — письма нет
    _score(db, book, "studio", 6)

    [rej] = _rejections(db)
    assert (rej.status, rej.due_at) == ("pending", LATER)
    assert [s for _, s in ar.send_due_rejections(db, now=LATER)] == ["sent"]


def test_one_score_of_seven_or_more_means_no_rejection(db):
    book = _book(db)
    _audition(db, book)
    _score(db, book, "author", 5)
    _score(db, book, "studio", 7)
    assert _rejections(db) == []


def test_only_one_score_never_rejects_however_low(db):
    book = _book(db)
    _audition(db, book)
    _score(db, book, "studio", 1)
    assert _rejections(db) == []


def test_raising_a_score_within_fifteen_minutes_cancels_at_once(db, mail):
    book = _book(db)
    _audition(db, book)
    _score(db, book, "author", 4)
    _score(db, book, "studio", 4)
    _score(db, book, "author", 8)

    assert [r.status for r in _rejections(db)] == ["cancelled"]
    assert ar.send_due_rejections(db, now=LATER) == []


def test_taking_a_score_back_cancels(db):
    book = _book(db)
    _audition(db, book)
    _score(db, book, "author", 4)
    _score(db, book, "studio", 4)
    _score(db, book, "studio", None)
    assert [r.status for r in _rejections(db)] == ["cancelled"]


def test_low_scores_do_not_reject_the_actor_approved_on_the_role(db):
    book = _book(db)
    _audition(db, book)
    db.add(Character(book_id=book.id, name="Пяднтикух", actor_name="Примерчюнгнчэлв Примеронгялюцз"))
    db.commit()
    _score(db, book, "author", 3)
    _score(db, book, "studio", 3)
    assert _rejections(db) == []


def test_a_score_raise_does_not_cancel_a_rejection_for_an_approved_rival(db):
    """Утвердили другого — отказ уходит, какой бы ни была оценка: роль одна."""
    book = _book(db)
    _audition(db, book)
    _approve(db, book, actor="Примерктенчечж Примерптмюгэюц")
    _score(db, book, "author", 9)
    _score(db, book, "studio", 9)
    assert [r.status for r in _rejections(db)] == ["pending"]


def test_a_settled_rejection_is_not_reopened_by_scores(db):
    book = _book(db)
    _audition(db, book)
    _score(db, book, "author", 4)
    _score(db, book, "studio", 4)
    _rejections(db)[0].status = "sent"
    db.commit()
    _score(db, book, "studio", 3)
    assert [r.status for r in _rejections(db)] == ["sent"]


def test_settle_rechecks_scores_when_nothing_is_approved(db, mail):
    """Страховка: оценку подняли мимо `sync_rejection_with_scores` — письмо не уходит."""
    book = _book(db)
    _audition(db, book)
    _score(db, book, "author", 4)
    _score(db, book, "studio", 4)
    db.query(AuditionScore).filter_by(lane="author").one().score = 9
    db.commit()

    assert [s for _, s in ar.send_due_rejections(db, now=LATER)] == ["cancelled"]
    assert mail["direct"] == []


def _old_scores(db, book, author, studio, *, role="Пяднтикух", actor="Примеронгялюцз Примерчюнгнчэлв"):
    """Оценки, стоящие с прошлых дней, — без нажатия, без сверки отказа."""
    for lane, score in (("author", author), ("studio", studio)):
        db.add(AuditionScore(book_id=book.id, role=role, actor_name=actor, lane=lane,
                             voter_uid=lane, voter_name=lane, score=score))
    db.commit()


def test_old_low_scores_do_not_turn_a_withdrawn_rival_approval_into_a_letter(db, mail):
    """Ревью 04.10: утвердили соперника, через 5 минут сняли — отказ по утверждению должен
    отмениться, даже если у пробы с прошлых дней стоят две оценки ниже 7: их сейчас
    никто не нажимал, а письма заводит только нажатие."""
    book = _book(db)
    _audition(db, book)
    _old_scores(db, book, 4, 5)
    _approve(db, book, actor="Примерктенчечж Примерптмюгэюц")
    db.query(Character).filter_by(book_id=book.id, name="Пяднтикух").one().actor_name = ""
    db.commit()

    assert [s for _, s in ar.send_due_rejections(db, now=LATER)] == ["cancelled"]
    assert mail["direct"] == []


def test_approving_this_actor_cancels_his_score_rejection_at_once(db, mail):
    """Ревью 04.10: утвердили самого актёра — его ждущий отказ по оценкам гаснет сразу, а не
    к сроку: иначе снятое через 5 минут утверждение отправило бы письмо."""
    book = _book(db)
    _audition(db, book)
    _score(db, book, "author", 4)
    _score(db, book, "studio", 4)
    _approve(db, book, actor="Примеронгялюцз Примерчюнгнчэлв")
    assert [r.status for r in _rejections(db)] == ["cancelled"]

    db.query(Character).filter_by(book_id=book.id, name="Пяднтикух").one().actor_name = ""
    db.commit()
    assert ar.send_due_rejections(db, now=LATER) == []
    assert mail["direct"] == []


def test_a_new_low_press_revives_a_cancelled_rejection_as_a_score_one(db, mail):
    book = _book(db)
    _audition(db, book)
    _score(db, book, "author", 4)
    _score(db, book, "studio", 4)
    _score(db, book, "author", 8)          # передумали — отменено
    _score(db, book, "author", 3)          # снова оба ниже 7 — письмо через 15 минут
    [rej] = _rejections(db)
    assert (rej.status, rej.cause) == ("pending", "scores")
    assert [s for _, s in ar.send_due_rejections(db, now=LATER)] == ["sent"]


# ---------- причина автора в письме (04.10) ----------

@pytest.mark.parametrize("reason,line", [
    ("voice", "Для этого героя нужен другой тембр."),
    ("manner", "Для этого героя нужна другая манера подачи."),
    ("all", "Для этого героя нужны и другой тембр, и другая манера подачи."),
])
def test_the_letter_carries_the_reason(reason, line):
    text = ar.rejection_notice(role="Пяднтикух", book_title="Карта", reason=reason)
    assert f"звучать немного иначе.\n{line}\n" in text


@pytest.mark.parametrize("reason", ["", "voice", "manner", "all"])
def test_the_letter_names_nobody_who_decided(reason):
    """👎 ставят и автор, и студия: письмо не приписывает отказ никому (решение 05.10)."""
    text = ar.rejection_notice(role="Пяднтикух", book_title="Карта", reason=reason).lower()
    assert "автор" not in text and "студи" not in text


def test_the_letter_without_a_reason_is_word_for_word_the_old_one():
    assert ar.rejection_notice(role="Пяднтикух", book_title="Карта", reason="") == \
        ar.rejection_notice(role="Пяднтикух", book_title="Карта")


def test_the_sent_letter_includes_the_reason_the_author_gave(db, mail):
    book = _book(db)
    _audition(db, book)
    db.add(AuditionScore(book_id=book.id, role="Пяднтикух", actor_name="Примеронгялюцз Примерчюнгнчэлв", lane="author",
                         voter_uid="a", voter_name="А", score=3, reason="manner"))
    db.commit()
    _score(db, book, "studio", 4)

    ar.send_due_rejections(db, now=LATER)
    [(_, text)] = mail["direct"]
    assert "другая манера подачи" in text


def test_the_authors_own_words_never_reach_the_dictor(db, mail):
    """Комментарий — только студии (решение 11.10): в письме отказа его нет ни при какой причине."""
    book = _book(db)
    _audition(db, book)
    db.add(AuditionScore(book_id=book.id, role="Пяднтикух", actor_name="Примеронгялюцз Примерчюнгнчэлв", lane="author",
                         voter_uid="a", voter_name="А", score=3, reason="voice",
                         comment="Тут нужен скрипучий старик, а не баритон"))
    db.commit()
    _score(db, book, "studio", 4)

    ar.send_due_rejections(db, now=LATER)
    [(_, text)] = mail["direct"]
    assert "другой тембр" in text and "скрипучий" not in text and "баритон" not in text


# ---------- «Отказать» рукой (13.10) ----------

def _by_hand(db, *, actor="Примеронгялюцз Примерчюнгнчэлв", role="Пяднтикух"):
    book = _book(db)
    _audition(db, book, role=role, actor=actor)
    rej = ar.reject_by_hand(db, book_id=book.id, role=role, actor_name=actor, now=NOW)
    db.commit()
    return book, rej


def test_a_hand_rejection_goes_out_without_approval_or_scores(db, mail):
    _, rej = _by_hand(db)

    assert rej.cause == ar.CAUSE_MANUAL and rej.due_at == LATER
    assert ar.send_due_rejections(db, now=LATER - timedelta(seconds=1)) == []
    [(_, status)] = ar.send_due_rejections(db, now=LATER)
    assert status == "sent"
    [(chat_ids, _)] = mail["direct"]
    assert chat_ids == ("111",)


def test_take_it_back_before_the_deadline(db, mail):
    book, _ = _by_hand(db)
    ar.cancel_pending_rejection(db, book_id=book.id, role="Пяднтикух", actor_name="Примеронгялюцз Примерчюнгнчэлв")
    db.commit()

    assert ar.send_due_rejections(db, now=LATER) == []
    assert mail["direct"] == []


def test_a_score_does_not_undo_a_hand_rejection(db, mail):
    """Оценку поставили после «Отказать» — сверка с оценками не должна гасить решение человека."""
    book, _ = _by_hand(db)
    db.add(AuditionScore(book_id=book.id, role="Пяднтикух", actor_name="Примеронгялюцз Примерчюнгнчэлв", lane="author",
                         voter_uid="a", voter_name="Автор", score=9))
    db.flush()
    rej = ar.sync_rejection_with_scores(db, book_id=book.id, role="Пяднтикух", actor_name="Примеронгялюцз Примерчюнгнчэлв", now=NOW)
    db.commit()

    assert rej.status == ar.PENDING and rej.cause == ar.CAUSE_MANUAL
    [(_, status)] = ar.send_due_rejections(db, now=LATER)
    assert status == "sent"


def test_approving_the_same_actor_cancels_a_hand_rejection(db, mail):
    book, _ = _by_hand(db)
    _approve(db, book, actor="Примеронгялюцз Примерчюнгнчэлв")

    assert ar.send_due_rejections(db, now=LATER) == []
    assert mail["direct"] == []


def test_the_approved_actor_cannot_be_rejected(db):
    book = _book(db)
    _audition(db, book, actor="Примеронгялюцз Примерчюнгнчэлв")
    _approve(db, book, actor="Примеронгялюцз Примерчюнгнчэлв")

    with pytest.raises(ar.RejectError) as error:
        ar.reject_by_hand(db, book_id=book.id, role="Пяднтикух", actor_name="Примеронгялюцз Примерчюнгнчэлв", now=NOW)
    assert error.value.code == "approved"


def test_a_sent_rejection_is_not_sent_again(db, mail):
    book, _ = _by_hand(db)
    ar.send_due_rejections(db, now=LATER)

    rej = ar.reject_by_hand(db, book_id=book.id, role="Пяднтикух", actor_name="Примеронгялюцз Примерчюнгнчэлв", now=LATER)
    db.commit()
    assert rej.status == ar.SENT
    assert ar.send_due_rejections(db, now=LATER + timedelta(hours=1)) == []
    assert len(mail["direct"]) == 1

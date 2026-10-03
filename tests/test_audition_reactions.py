"""Реакция автора на пробу и отказ, который за 👎 следует через четверть часа.

Отказ — письмо живому человеку, и отозвать его нельзя. Поэтому почти все тесты здесь
про то, когда письма НЕ должно быть: промах пальцем, передумал, утвердили, удалили пробу.
"""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import AudioFile, AuditionRejection, ScriptBook
from app.services import audition_reactions as ar
from app.services.audio_uploads import derive_book_code

NOW = datetime(2026, 9, 25, 12, 0, 0)
AUTHOR = {"voter_uid": "author-1", "voter_name": "Александр Белозёров"}


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        yield session


def _book(db, title="Крылья полумрака"):
    book = ScriptBook(title=title, source_filename="k.txt", source_format="txt", created_at=NOW)
    db.add(book)
    db.flush()
    return book


def _audition(db, book, *, role="Вукьрадух", actor="Роман Сомов", book_code=None):
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


def test_thumbs_down_schedules_a_rejection_in_fifteen_minutes(db):
    book = _book(db)
    item = _audition(db, book)

    result = ar.set_reaction(db, item.id, value=-1, now=NOW, **AUTHOR)

    assert result["author_reaction"] == -1
    [rej] = _rejections(db)
    assert (rej.status, rej.due_at, rej.role, rej.actor_name) == ("pending", NOW + timedelta(minutes=15), "Вукьрадух", "Роман Сомов")
    assert result["rejection"] == {"status": "pending", "due_at": "2026-09-25T12:15:00Z", "sent_at": ""}


def test_thumbs_up_schedules_nothing(db):
    item = _audition(db, _book(db))

    assert ar.set_reaction(db, item.id, value=1, now=NOW, **AUTHOR) == {"author_reaction": 1, "rejection": None}
    assert _rejections(db) == []


def test_three_thumbs_down_on_one_pair_are_one_rejection(db):
    book = _book(db)
    items = [_audition(db, book) for _ in range(3)]

    for step, item in enumerate(items):
        ar.set_reaction(db, item.id, value=-1, now=NOW + timedelta(minutes=step), **AUTHOR)

    [rej] = _rejections(db)
    assert rej.due_at == NOW + timedelta(minutes=2 + 15)


def test_taking_back_the_thumbs_down_cancels(db):
    item = _audition(db, _book(db))
    ar.set_reaction(db, item.id, value=-1, now=NOW, **AUTHOR)

    result = ar.set_reaction(db, item.id, value=0, now=NOW + timedelta(minutes=5), **AUTHOR)

    assert result == {"author_reaction": None, "rejection": None}
    assert _rejections(db)[0].status == "cancelled"


def test_switching_to_thumbs_up_cancels(db):
    item = _audition(db, _book(db))
    ar.set_reaction(db, item.id, value=-1, now=NOW, **AUTHOR)

    ar.set_reaction(db, item.id, value=1, now=NOW, **AUTHOR)

    assert _rejections(db)[0].status == "cancelled"


def test_another_thumbs_down_on_the_pair_keeps_it_pending(db):
    book = _book(db)
    first, second = _audition(db, book), _audition(db, book)
    ar.set_reaction(db, first.id, value=-1, now=NOW, **AUTHOR)
    ar.set_reaction(db, second.id, value=-1, now=NOW, **AUTHOR)

    ar.set_reaction(db, first.id, value=0, now=NOW, **AUTHOR)

    assert _rejections(db)[0].status == "pending"


def test_a_cancelled_rejection_comes_back_with_a_new_thumbs_down(db):
    item = _audition(db, _book(db))
    ar.set_reaction(db, item.id, value=-1, now=NOW, **AUTHOR)
    ar.set_reaction(db, item.id, value=0, now=NOW, **AUTHOR)

    ar.set_reaction(db, item.id, value=-1, now=NOW + timedelta(hours=1), **AUTHOR)

    [rej] = _rejections(db)
    assert (rej.status, rej.due_at) == ("pending", NOW + timedelta(hours=1, minutes=15))


@pytest.mark.parametrize("final", ["sent", "no_account", "ambiguous", "failed"])
def test_a_settled_pair_is_never_reopened(db, final):
    item = _audition(db, _book(db))
    ar.set_reaction(db, item.id, value=-1, now=NOW, **AUTHOR)
    rej = _rejections(db)[0]
    rej.status = final
    db.commit()

    ar.set_reaction(db, item.id, value=0, now=NOW, **AUTHOR)
    ar.set_reaction(db, item.id, value=-1, now=NOW + timedelta(days=1), **AUTHOR)

    [rej] = _rejections(db)
    assert (rej.status, rej.due_at) == (final, NOW + timedelta(minutes=15))


def test_a_different_actor_on_the_same_role_is_a_different_pair(db):
    book = _book(db)
    ar.set_reaction(db, _audition(db, book, actor="Роман Сомов").id, value=-1, now=NOW, **AUTHOR)
    ar.set_reaction(db, _audition(db, book, actor="София Ершова").id, value=-1, now=NOW, **AUTHOR)

    assert sorted(r.actor_name for r in _rejections(db)) == ["Роман Сомов", "София Ершова"]


def test_reaction_on_orphan_audition_has_no_rejection(db):
    item = _audition(db, _book(db), book_code="НЕТ")

    assert ar.set_reaction(db, item.id, value=-1, now=NOW, **AUTHOR) == {"author_reaction": -1, "rejection": None}
    assert _rejections(db) == []


def test_unknown_audition_and_bad_value_are_refused(db):
    item = _audition(db, _book(db))

    with pytest.raises(ar.ReactionError) as missing:
        ar.set_reaction(db, "nope", value=1, now=NOW, **AUTHOR)
    with pytest.raises(ar.ReactionError) as bad:
        ar.set_reaction(db, item.id, value=2, now=NOW, **AUTHOR)

    assert (missing.value.code, bad.value.code) == ("not_found", "bad_value")


# ---------- рассылка ----------

from app.models import Character  # noqa: E402


def _due(db, *, actor="Роман Сомов", role="Вукьрадух"):
    book = _book(db)
    item = _audition(db, book, role=role, actor=actor)
    ar.set_reaction(db, item.id, value=-1, now=NOW, **AUTHOR)
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
    monkeypatch.setattr(ar, "resolve_actor_candidates", lambda db, name: [("111", "Роман Сомов")])
    return sent


LATER = NOW + timedelta(minutes=15)


def test_the_letter_word_for_word():
    assert ar.rejection_notice(role="Вукьрадух", book_title='Белозёров - "Крылья Полумрака"') == (
        "🎙 Noname Records — про вашу пробу\n"
        "Роль: «Вукьрадух»\n"
        'Книга: Белозёров - "Крылья Полумрака"\n'
        "\n"
        "Спасибо, что попробовались! В этот раз вы с персонажем «Вукьрадух» не сошлись "
        "характерами — точнее, тембрами. Запись тут ни при чём: просто у автора в голове "
        "этот герой говорит чуть иначе.\n"
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
    assert chat_ids == ("111",) and "«Вукьрадух»" in text
    rej = _rejections(db)[0]
    assert (rej.status, rej.sent_at) == ("sent", LATER)


def test_approved_by_the_deadline_means_no_letter(db, mail):
    book, _ = _due(db)
    db.add(Character(book_id=book.id, name="Вукьрадух", actor_name="Сомов Роман"))
    db.commit()

    assert ar.send_due_rejections(db, now=LATER) == [(_rejections(db)[0].id, "cancelled")]
    assert mail["direct"] == []


def test_a_tentative_assignment_is_not_an_approval(db, mail):
    book, _ = _due(db)
    db.add(Character(book_id=book.id, name="Вукьрадух", actor_name="Роман Сомов?"))
    db.commit()

    [(_, status)] = ar.send_due_rejections(db, now=LATER)
    assert status == "sent"


def test_thumbs_down_gone_by_the_deadline_means_no_letter(db, mail):
    """Страховка от гонки: реакцию сняли мимо `sync_rejection` (например, удаление пробы)."""
    _due(db)
    db.query(ar.AuditionReaction).delete()
    db.commit()

    [(_, status)] = ar.send_due_rejections(db, now=LATER)
    assert status == "cancelled" and mail["direct"] == []


def test_no_account_goes_to_the_owner(db, mail, monkeypatch):
    monkeypatch.setattr(ar, "resolve_actor_candidates", lambda db, name: [])
    _due(db)

    [(_, status)] = ar.send_due_rejections(db, now=LATER)

    assert status == "no_account" and mail["direct"] == []
    [relay] = mail["relay"]
    assert "Роман Сомов" in relay and "не доставлен" in relay


def test_an_ambiguous_name_goes_to_the_owner_with_candidates(db, mail, monkeypatch):
    monkeypatch.setattr(ar, "resolve_actor_candidates", lambda db, name: [("1", "Роман Сомов"), ("2", "Роман Пан")])
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
    _due(db, actor="Роман Сомов")
    _due(db, actor="София Ершова")
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
    _due(db, actor="Роман Сомов")
    _due(db, actor="София Ершова")

    def resolve(db_, name):
        if name == "Роман Сомов":
            raise RuntimeError("база занята")
        return [("222", name)]

    monkeypatch.setattr(ar, "resolve_actor_candidates", resolve)
    ar.send_due_rejections(db, now=LATER)
    ar.send_due_rejections(db, now=LATER + timedelta(minutes=1))

    statuses = {r.actor_name: r.status for r in _rejections(db)}
    assert statuses == {"Роман Сомов": "failed", "София Ершова": "sent"}
    assert [ids for ids, _ in mail["direct"]] == [("222",)]


def test_dictor_does_not_see_a_pending_rejection_through_a_thumbs_up_row(db):
    """Отказ — по паре, а не по файлу: 👍 на одной пробе не должен выдать 👎 на другой."""
    book = _book(db)
    liked, disliked = _audition(db, book), _audition(db, book)
    ar.set_reaction(db, liked.id, value=1, now=NOW, **AUTHOR)
    ar.set_reaction(db, disliked.id, value=-1, now=NOW, **AUTHOR)

    rows = ar.attach_reactions(db, _rows(db, book), book_id=book.id, viewer_name="Роман Сомов", sees_all=False)

    assert all(row["rejection"] is None for row in rows)


# ---------- кто что видит ----------

def _rows(db, book):
    from app.services.auditions import book_auditions
    return book_auditions(db, book_id=book.id)


def test_owner_sees_everything_at_once(db):
    book = _book(db)
    item = _audition(db, book)
    ar.set_reaction(db, item.id, value=-1, now=NOW, **AUTHOR)

    [row] = ar.attach_reactions(db, _rows(db, book), book_id=book.id, viewer_name="Владелец", sees_all=True)

    assert row["author_reaction"] == -1
    assert row["rejection"]["status"] == "pending"


def test_dictor_sees_nothing_on_somebody_elses(db):
    book = _book(db)
    ar.set_reaction(db, _audition(db, book, actor="София Ершова").id, value=1, now=NOW, **AUTHOR)

    [row] = ar.attach_reactions(db, _rows(db, book), book_id=book.id, viewer_name="Роман Сомов", sees_all=False)

    assert (row["author_reaction"], row["rejection"]) == (None, None)


def test_dictor_sees_own_thumbs_up_at_once(db):
    book = _book(db)
    ar.set_reaction(db, _audition(db, book).id, value=1, now=NOW, **AUTHOR)

    [row] = ar.attach_reactions(db, _rows(db, book), book_id=book.id, viewer_name="Роман Сомов", sees_all=False)

    assert row["author_reaction"] == 1


def test_dictor_does_not_see_own_thumbs_down_before_the_letter(db):
    """Иначе 15 минут на передумать ничего не спасают: промах диктор увидит сразу."""
    book = _book(db)
    ar.set_reaction(db, _audition(db, book).id, value=-1, now=NOW, **AUTHOR)

    [row] = ar.attach_reactions(db, _rows(db, book), book_id=book.id, viewer_name="Роман Сомов", sees_all=False)

    assert (row["author_reaction"], row["rejection"]) == (None, None)


def test_dictor_sees_own_thumbs_down_after_the_letter(db):
    book = _book(db)
    ar.set_reaction(db, _audition(db, book).id, value=-1, now=NOW, **AUTHOR)
    _rejections(db)[0].status = "sent"
    db.commit()

    [row] = ar.attach_reactions(db, _rows(db, book), book_id=book.id, viewer_name="Роман Сомов", sees_all=False)

    assert row["author_reaction"] == -1 and row["rejection"]["status"] == "sent"


def test_dictor_sees_own_by_fuzzy_name(db):
    book = _book(db)
    ar.set_reaction(db, _audition(db, book, actor="Роман Сомов").id, value=1, now=NOW, **AUTHOR)

    [row] = ar.attach_reactions(db, _rows(db, book), book_id=book.id, viewer_name="Сомов Роман", sees_all=False)

    assert row["author_reaction"] == 1


def test_times_are_utc_with_z(db):
    book = _book(db)
    ar.set_reaction(db, _audition(db, book).id, value=-1, now=NOW, **AUTHOR)

    [row] = ar.attach_reactions(db, _rows(db, book), book_id=book.id, viewer_name="", sees_all=True)

    assert row["rejection"]["due_at"] == "2026-09-25T12:15:00Z"


# ---------- удаление пробы ----------

def test_deleting_the_last_thumbs_down_audition_cancels(db):
    book = _book(db)
    item = _audition(db, book)
    ar.set_reaction(db, item.id, value=-1, now=NOW, **AUTHOR)

    ar.forget_audition(db, item)
    db.delete(item)
    db.commit()

    assert db.query(ar.AuditionReaction).count() == 0
    assert _rejections(db)[0].status == "cancelled"


def test_deleting_one_of_two_thumbs_down_keeps_it_pending(db):
    book = _book(db)
    first, second = _audition(db, book), _audition(db, book)
    ar.set_reaction(db, first.id, value=-1, now=NOW, **AUTHOR)
    ar.set_reaction(db, second.id, value=-1, now=NOW, **AUTHOR)

    ar.forget_audition(db, first)
    db.delete(first)
    db.commit()

    assert _rejections(db)[0].status == "pending"

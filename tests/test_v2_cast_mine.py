"""«Мои роли»: which rows of the book's cast belong to the person reading it.

A dictor opens the reader to find where his own character speaks. The cast knows the
actor of every role, the session knows who is logged in, and the two are written by
different hands: the cast says «Белозёров Александр», the account says «Александр
Белозёров». Deciding they are the same person is `names_match`'s job — the same rule
the recording panel already uses — so the answer is computed here, next to the cast,
rather than re-derived in the browser where the rule would quietly drift.

Being nobody's role is the normal case: 205 roles, one reader. The flag is a hint for
sorting a picker, never a permission.
"""
import uuid

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Character, ScriptBook, ScriptChapter
from app.v2.cast_ops import book_cast
from app.v2.models import V2Attribution, V2Segment
from app.v2.reader import NARRATOR

BOOK = "book-1"
CH = "ch-1"


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)


def _seed(db):
    db.add(ScriptBook(id=BOOK, title="Крылья", source_filename="k.txt", source_format="txt"))
    db.add(ScriptChapter(id=CH, book_id=BOOK, chapter_index=1, chapter_title="Глава 1"))
    db.add(Character(id="c1", book_id=BOOK, name="Дгарнин", actor_name="Белозёров Александр"))
    db.add(Character(id="c2", book_id=BOOK, name="Пупип", actor_name="Жукова Виктория"))
    db.add(Character(id="c3", book_id=BOOK, name="Молчун", actor_name=""))
    db.add(Character(id="c4", book_id=BOOK, name="Совита", actor_name="Фёдоров Пётр"))
    for ordinal, speaker in enumerate(("Дгарнин", "Пупип", "Совита")):
        segment = f"{CH}:{ordinal:05d}"
        db.add(V2Segment(id=segment, book_id=BOOK, chapter_id=CH, ordinal=ordinal, text="…", char_end=1))
        db.add(V2Attribution(id=str(uuid.uuid4()), segment_id=segment, span_start=0, span_end=10,
                             speaker=speaker, confidence=0.9, source="llm", version=1))
    db.commit()


def _mine(rows) -> list[str]:
    return sorted(row["name"] for row in rows if row["mine"])


def test_the_reader_owns_the_role_whose_actor_is_him_however_the_name_is_ordered():
    """The cast writes «Белозёров Александр»; the account says «Александр Белозёров»."""
    with _session()() as db:
        _seed(db)

        rows = book_cast(db, BOOK, actor_name="Александр Белозёров")

        assert _mine(rows) == ["Дгарнин"]


def test_a_role_cast_to_someone_else_is_not_mine():
    with _session()() as db:
        _seed(db)

        rows = book_cast(db, BOOK, actor_name="Александр Белозёров")

        assert not next(row for row in rows if row["name"] == "Пупип")["mine"]


def test_a_role_with_no_actor_belongs_to_nobody():
    with _session()() as db:
        _seed(db)

        rows = book_cast(db, BOOK, actor_name="Александр Белозёров")

        assert not next(row for row in rows if row["name"] == "Молчун")["mine"]


def test_yo_and_ye_are_the_same_person():
    """«Фёдоров Пётр» in the cast, «Петр Федоров» typed into the account."""
    with _session()() as db:
        _seed(db)

        rows = book_cast(db, BOOK, actor_name="Петр Федоров")

        assert _mine(rows) == ["Совита"]


def test_without_a_name_nothing_is_mine():
    """An admin, a service account, a session with no display name: the picker just lists."""
    with _session()() as db:
        _seed(db)

        assert _mine(book_cast(db, BOOK, actor_name="")) == []
        assert _mine(book_cast(db, BOOK)) == []


def test_the_narrator_can_be_a_role_of_mine_too():
    """«Старик Ворчалыч» reads the narration of «Крыльев» — it is his part like any other."""
    with _session()() as db:
        _seed(db)
        db.add(Character(id="cn", book_id=BOOK, name=NARRATOR, actor_name="Старик Ворчалыч"))
        db.commit()

        rows = book_cast(db, BOOK, actor_name="Ворчалыч Старик")

        assert next(row for row in rows if row["is_narrator"])["mine"]


def test_the_cast_endpoint_answers_for_whoever_is_logged_in(monkeypatch):
    """The wiring: the session's display name is what «мои роли» is measured against."""
    import json

    from app.v2 import api

    SessionLocal = _session()
    with SessionLocal() as db:
        _seed(db)
    monkeypatch.setattr(api, "SessionLocal", SessionLocal)
    monkeypatch.setattr(api, "_is_authenticated", lambda request: True)
    monkeypatch.setattr(api, "session_payload", lambda request: {"uid": "u1", "display_name": "Александр Белозёров"})

    response = api.api_v2_cast(object(), BOOK)
    payload = json.loads(bytes(response.body).decode())

    assert [row["name"] for row in payload["characters"] if row["mine"]] == ["Дгарнин"]


def test_a_role_i_am_only_suggested_for_is_not_mine_yet():
    """«Натали Ким?» — предложение агента: панель записи не вправе звать роль своей.

    `names_match` знака вопроса не видит, поэтому без отдельной проверки диктор
    увидел бы среди «моих ролей» ту, на которую его только предложили.
    """
    with _session()() as db:
        _seed(db)
        db.add(Character(id="c5", book_id=BOOK, name="Химера", actor_name="Натали Ким?"))
        db.commit()

        rows = book_cast(db, BOOK, actor_name="Натали Ким")

        assert _mine(rows) == []


def test_and_becomes_mine_the_moment_the_mark_goes_away():
    with _session()() as db:
        _seed(db)
        db.add(Character(id="c5", book_id=BOOK, name="Химера", actor_name="Натали Ким"))
        db.commit()

        rows = book_cast(db, BOOK, actor_name="Натали Ким")

        assert _mine(rows) == ["Химера"]

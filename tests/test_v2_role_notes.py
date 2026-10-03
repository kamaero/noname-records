"""What a role is, told to the person who has to voice it.

The pipeline already writes down what it learned about a character — «фархеррим
(квартерон)», «Холоден, расчётлив и вездесущ; говорит взвешенно» — for 148 of the
206 roles in «Крыльях», and none of it reaches anybody: the cast table has no column
for it and the dictor never sees it at all.

`operator_note` is the other half: the author's own word about how a role should
sound. The field has existed all along and is editable on one screen the dictors do
not open. Both travel with the cast row now, because the cast row is what the reader,
the recording panel and the role script all already ask for.
"""
import json
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Character, ScriptBook, ScriptChapter
from app.v2.cast_ops import book_cast
from app.v2.models import V2Attribution, V2Segment

BOOK = "book-1"
CH = "ch-1"


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with sessionmaker(bind=engine, autocommit=False, autoflush=False)() as session:
        session.add(ScriptBook(id=BOOK, title="Крылья", source_filename="k.txt", source_format="txt"))
        session.add(ScriptChapter(id=CH, book_id=BOOK, chapter_index=1, chapter_title="Глава 1"))
        session.add(Character(id="c1", book_id=BOOK, name="Дгарнин",
                              race="фархеррим (квартерон)",
                              temperament="Холоден, расчётлив и вездесущ; говорит взвешенно",
                              operator_note=""))
        segment = f"{CH}:00000"
        session.add(V2Segment(id=segment, book_id=BOOK, chapter_id=CH, ordinal=0, text="…", char_end=1))
        session.add(V2Attribution(id=str(uuid.uuid4()), segment_id=segment, span_start=0, span_end=1,
                                  speaker="Дгарнин", confidence=0.9, source="llm", version=1))
        session.commit()
        yield session


def _row(db, name: str = "Дгарнин") -> dict:
    return next(row for row in book_cast(db, BOOK) if row["name"] == name)


def test_the_cast_row_carries_what_the_pipeline_learned_about_the_character(db):
    row = _row(db)

    assert row["race"] == "фархеррим (квартерон)"
    assert row["temperament"].startswith("Холоден, расчётлив")


def test_the_note_starts_empty_and_travels_with_the_row(db):
    assert _row(db)["note"] == ""

    db.get(Character, "c1").operator_note = "Низкий голос, без нажима. Не злодей — уставший."
    db.commit()

    assert _row(db)["note"] == "Низкий голос, без нажима. Не злодей — уставший."


def test_the_narrator_row_survives_having_no_character_of_its_own(db):
    """The narrator is a row without a `Character` unless the cast made one."""
    row = _row(db, "Рассказчик")

    assert row["race"] == "" and row["temperament"] == "" and row["note"] == ""


class TestAboutEndpoint:
    def _handlers(self, SessionLocal, monkeypatch, *, can_edit=True):
        from app.v2 import api

        monkeypatch.setattr(api, "SessionLocal", SessionLocal)
        monkeypatch.setattr(api, "_is_authenticated", lambda request: True)
        monkeypatch.setattr(api, "_can_edit", lambda request: can_edit)
        monkeypatch.setattr(api, "session_payload", lambda request: {"uid": "u1", "display_name": "Белозёров"})
        return api

    def test_the_author_writes_the_note_and_it_lands_on_the_role(self, db, monkeypatch):
        from asyncio import run

        SessionLocal = sessionmaker(bind=db.get_bind(), autocommit=False, autoflush=False)
        api = self._handlers(SessionLocal, monkeypatch)

        class Body:
            async def json(self):
                return {"note": "  Хриплый, медленный.  "}
            query_params: dict = {}

        response = run(api.api_v2_character_about(Body(), "c1"))
        payload = json.loads(bytes(response.body).decode())

        assert payload["ok"] is True and payload["note"] == "Хриплый, медленный."
        assert db.get(Character, "c1").operator_note == "Хриплый, медленный."

    def test_a_dictor_may_read_the_note_but_not_write_it(self, db, monkeypatch):
        from asyncio import run

        SessionLocal = sessionmaker(bind=db.get_bind(), autocommit=False, autoflush=False)
        api = self._handlers(SessionLocal, monkeypatch, can_edit=False)

        class Body:
            async def json(self):
                return {"note": "не моё дело"}
            query_params: dict = {}

        response = run(api.api_v2_character_about(Body(), "c1"))

        assert response.status_code == 403
        assert db.get(Character, "c1").operator_note == ""


class TestEditingWhoTheCharacterIs:
    """The description the pipeline wrote is a draft, not a verdict.

    «фархеррим (квартерон хилактока) · Холоден, расчётлив и вездесущ» came out of an
    LLM reading the book. The author knows better, and until now the only editable
    thing on the row was a note beside it.
    """

    def _api(self, db, monkeypatch, *, can_edit=True):
        from app.v2 import api

        monkeypatch.setattr(api, "SessionLocal", sessionmaker(bind=db.get_bind(), autocommit=False, autoflush=False))
        monkeypatch.setattr(api, "_is_authenticated", lambda request: True)
        monkeypatch.setattr(api, "_can_edit", lambda request: can_edit)
        monkeypatch.setattr(api, "session_payload", lambda request: {"uid": "u1", "display_name": "Белозёров"})
        return api

    def _send(self, api, body: dict, character_id: str = "c1"):
        from asyncio import run

        class Body:
            async def json(self):
                return body

        return run(api.api_v2_character_about(Body(), character_id))

    def test_the_race_and_the_temperament_can_be_corrected(self, db, monkeypatch):
        api = self._api(db, monkeypatch)

        self._send(api, {"race": "фархеррим", "temperament": "Спокоен, говорит негромко."})

        row = db.get(Character, "c1")
        assert row.race == "фархеррим"
        assert row.temperament == "Спокоен, говорит негромко."

    def test_a_field_left_out_is_left_alone(self, db, monkeypatch):
        """The screen edits one line at a time; the others must survive it."""
        api = self._api(db, monkeypatch)

        self._send(api, {"note": "Тише, чем кажется."})

        row = db.get(Character, "c1")
        assert row.operator_note == "Тише, чем кажется."
        assert row.race == "фархеррим (квартерон)"
        assert row.temperament.startswith("Холоден, расчётлив")

    def test_a_field_can_be_emptied_on_purpose(self, db, monkeypatch):
        api = self._api(db, monkeypatch)

        self._send(api, {"race": ""})

        assert db.get(Character, "c1").race == ""

    def test_a_dictor_changes_nothing(self, db, monkeypatch):
        api = self._api(db, monkeypatch, can_edit=False)

        response = self._send(api, {"race": "не моё дело"})

        assert response.status_code == 403
        assert db.get(Character, "c1").race == "фархеррим (квартерон)"

    def test_the_answer_carries_the_row_back_for_the_table(self, db, monkeypatch):
        api = self._api(db, monkeypatch)

        payload = json.loads(bytes(self._send(api, {"temperament": "Резкий."}).body).decode())

        assert payload["ok"] is True
        assert payload["character"]["temperament"] == "Резкий."
        assert payload["character"]["race"] == "фархеррим (квартерон)"

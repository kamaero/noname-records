"""Сроки проб и ролей: сверка с кастом (план 2026-10-01-role-deadlines, задача 1)."""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import AudioFile, BookBudget, Character, RoleDeadline, ScriptBook, ScriptChapter
from app.services import role_deadlines
from app.services.audio_uploads import derive_book_code

NOW = datetime(2026, 10, 1, 12, 0, 0)
ROLE_DUE = datetime(2026, 12, 31, 20, 59, 59)  # конец 31.12 по Москве, в UTC


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, autoflush=False)() as session:
        session.add(ScriptBook(id="b1", title="Крылья", display_title="Крылья", source_filename="x.docx",
                               source_format="docx", total_chars=0, author_sheets_x1000=0, chapter_count=2,
                               has_chapters="true", status="processing"))
        session.add(ScriptChapter(id="ch1", book_id="b1", chapter_index=1, chapter_title="Глава 1"))
        session.add(ScriptChapter(id="ch2", book_id="b1", chapter_index=2, chapter_title="Глава 2"))
        session.commit()
        yield session


def _char(db, actor, cid="c1", name="Куйбу", appears="1,2"):
    ch = db.get(Character, cid)
    if ch is None:
        ch = Character(id=cid, book_id="b1", name=name, aliases="", character_color="", appears_in=appears)
        db.add(ch)
    ch.actor_name = actor
    db.commit()
    return ch


def _take(db, chapter, actor="Ветрова Ольга", role="Куйбу", kind="take"):
    db.add(AudioFile(book_code=derive_book_code("Крылья"), original_filename="t.wav", stored_key=f"k/{chapter}{kind}{actor}",
                     mime_type="audio/wav", size_bytes=1, chapter=chapter, role=role, actor_name=actor, kind=kind,
                     canonical_filename=f"{chapter}{kind}{actor}.wav"))
    db.commit()


def _open(db, cid="c1"):
    return role_deadlines.open_deadline(db, cid)


def test_a_proposal_opens_an_audition_for_48_hours(db):
    _char(db, "Ветрова Ольга?")
    role_deadlines.sync_book(db, "b1", now=NOW)
    row = _open(db)
    assert (row.kind, row.actor_name, row.due_at) == ("audition", "Ветрова Ольга", NOW + timedelta(hours=48))


def test_approval_replaces_the_audition_with_the_role_until_the_studio_date(db):
    _char(db, "Ветрова Ольга?")
    role_deadlines.sync_book(db, "b1", now=NOW)
    _char(db, "Ветрова Ольга")
    role_deadlines.sync_book(db, "b1", now=NOW + timedelta(hours=1))
    rows = db.query(RoleDeadline).order_by(RoleDeadline.created_at).all()
    assert [(r.kind, r.close_reason) for r in rows] == [("audition", "changed"), ("role", None)]
    assert rows[1].due_at == ROLE_DUE
    assert rows[0].recast_notified_at is None and not role_deadlines.needs_recast_notice(rows[0])


def test_another_actor_closes_the_role_and_asks_for_a_recast_letter(db):
    _char(db, "Ветрова Ольга")
    role_deadlines.sync_book(db, "b1", now=NOW)
    _char(db, "Иванов Пётр")
    role_deadlines.sync_book(db, "b1", now=NOW)
    rows = {r.actor_name: r for r in db.query(RoleDeadline).all()}
    old, new = rows["Ветрова Ольга"], rows["Иванов Пётр"]
    assert (old.actor_name, old.close_reason) == ("Ветрова Ольга", "replaced")
    assert role_deadlines.needs_recast_notice(old)
    assert (new.actor_name, new.kind, new.closed_at) == ("Иванов Пётр", "role", None)


def test_clearing_the_actor_removes_the_deadline(db):
    _char(db, "Ветрова Ольга")
    role_deadlines.sync_book(db, "b1", now=NOW)
    _char(db, "")
    role_deadlines.sync_book(db, "b1", now=NOW)
    row = db.query(RoleDeadline).one()
    assert row.close_reason == "removed" and role_deadlines.needs_recast_notice(row)


def test_the_same_person_written_the_other_way_round_keeps_the_deadline(db):
    _char(db, "Ветрова Ольга")
    role_deadlines.sync_book(db, "b1", now=NOW)
    _char(db, "Ольга Ветрова")
    role_deadlines.sync_book(db, "b1", now=NOW)
    assert db.query(RoleDeadline).count() == 1 and _open(db) is not None


def test_the_narrator_gets_no_deadline(db):
    _char(db, "Ветрова Ольга", cid="n", name="Рассказчик")
    db.add(BookBudget(book_id="b1", narrator_actor_name="Ветрова Ольга"))
    db.commit()
    role_deadlines.sync_book(db, "b1", now=NOW)
    assert db.query(RoleDeadline).count() == 0


def test_a_role_already_recorded_in_every_chapter_gets_no_deadline(db):
    _char(db, "Ветрова Ольга")
    _take(db, "Глава 1")
    _take(db, "Глава 2", actor="Ольга Ветрова")
    assert role_deadlines.role_progress(db, db.get(Character, "c1"), "Ветрова Ольга") == (2, 2)
    role_deadlines.sync_book(db, "b1", now=NOW)
    assert db.query(RoleDeadline).count() == 0


def test_a_chapter_missing_from_the_book_is_not_counted(db):
    _char(db, "Ветрова Ольга", appears="1,2,7")
    _take(db, "Глава 1")
    assert role_deadlines.role_progress(db, db.get(Character, "c1"), "Ветрова Ольга") == (1, 2)


def test_a_baseline_row_keeps_an_old_proposal_without_a_deadline(db):
    _char(db, "Ветрова Ольга?")
    db.add(RoleDeadline(character_id="c1", book_id="b1", actor_name="Ветрова Ольга", kind="audition",
                        due_at=NOW, created_at=NOW, closed_at=NOW, close_reason="baseline"))
    db.commit()
    role_deadlines.sync_book(db, "b1", now=NOW)
    assert db.query(RoleDeadline).count() == 1 and _open(db) is None


def test_syncing_twice_changes_nothing(db):
    _char(db, "Ветрова Ольга?")
    first = role_deadlines.sync_book(db, "b1", now=NOW)
    second = role_deadlines.sync_book(db, "b1", now=NOW)
    assert first["opened"] == 1 and second == {"opened": 0, "closed": 0}


def test_rebuilding_assignments_syncs_the_deadlines(db):
    from app.services.casting import rebuild_assignments
    _char(db, "Ветрова Ольга")
    rebuild_assignments(db, "b1")
    assert _open(db).kind == "role"


def test_the_studio_date_is_a_setting(db):
    from app.services.studio_settings import studio_settings
    studio_settings(db).role_deadline_date = "2027-03-01"
    db.commit()
    assert role_deadlines.due_for(db, "role", now=NOW) == datetime(2027, 3, 1, 20, 59, 59)


# --- ревью 01.10 ---

def test_clear_and_restore_the_same_actor_reopens_the_deadline(db):
    _char(db, "Ветрова Ольга")
    role_deadlines.sync_book(db, "b1", now=NOW)
    _char(db, "")
    role_deadlines.sync_book(db, "b1", now=NOW)
    _char(db, "Ветрова Ольга")
    role_deadlines.sync_book(db, "b1", now=NOW + timedelta(minutes=1))
    assert _open(db) is not None and _open(db).kind == "role"


def test_demoting_to_a_proposal_opens_no_audition(db):
    _char(db, "Ветрова Ольга")
    role_deadlines.sync_book(db, "b1", now=NOW)
    _char(db, "Ветрова Ольга?")
    role_deadlines.sync_book(db, "b1", now=NOW)
    assert _open(db) is None


def test_a_role_without_known_chapters_gets_no_deadline(db):
    _char(db, "Ветрова Ольга", appears="")
    role_deadlines.sync_book(db, "b1", now=NOW)
    assert _open(db) is None


def test_renaming_a_dictor_keeps_his_deadline_and_sends_no_recast(db):
    from app.models import User, UserRole
    from app.services import dictors
    db.add(User(id="u1", login="olga", password_hash="x", display_name="Ветрова Ольга", is_active="true"))
    db.add(UserRole(user_id="u1", role="dictor"))
    _char(db, "Ветрова Ольга")
    role_deadlines.sync_book(db, "b1", now=NOW)
    before = _open(db).id
    dictors.rename_dictor(db, "u1", "Лунная Ольга")
    db.commit()
    rows = db.query(RoleDeadline).all()
    assert len(rows) == 1 and rows[0].id == before and rows[0].actor_name == "Лунная Ольга" and rows[0].closed_at is None

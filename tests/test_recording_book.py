"""«Сейчас записывается»: одна книга студии, которую слушает бот."""
import importlib.util
from asyncio import run
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.api.budget_api import SaveRecordingBookPayload, build_budget_api_handlers
from app.db import Base
from app.models import ScriptBook
from app.services.studio_settings import active_book, set_active_book


@pytest.fixture()
def factory():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)


def _book(db, book_id, title):
    db.add(ScriptBook(id=book_id, title=title, display_title=title, source_filename="b.txt",
                      source_format="txt", chapter_count=1, status="author_review"))
    db.flush()


def test_one_book_at_a_time(factory):
    with factory() as db:
        _book(db, "b1", "Первая")
        _book(db, "b2", "Вторая")
        set_active_book(db, "b1", updated_by="t")
        assert active_book(db).id == "b1"
        set_active_book(db, "b2", updated_by="t")
        assert active_book(db).id == "b2"
        set_active_book(db, None, updated_by="t")
        assert active_book(db) is None


def test_missing_book_reads_as_none(factory):
    with factory() as db:
        _book(db, "b1", "Первая")
        set_active_book(db, "b1", updated_by="t")
        db.delete(db.get(ScriptBook, "b1"))
        db.flush()
        assert active_book(db) is None
        with pytest.raises(ValueError, match="book_not_found"):
            set_active_book(db, "nope", updated_by="t")


def _handlers(factory, roles):
    return build_budget_api_handlers({
        "is_authenticated": lambda _r: True,
        "has_workspace_full_access": lambda _r: True,
        "has_any_role": lambda _r, allowed: bool("admin" in roles or set(roles) & set(allowed)),
        "is_agent": lambda _r: False,
        "session_payload": lambda _r: {"uid": "u1"},
        "session_roles": lambda _r: set(roles),
        "session_display_name": lambda _r: "tester",
        "SessionLocal": factory,
        "get_book": lambda db, book_id: db.get(ScriptBook, book_id),
        "build_character_style_maps": lambda _c: None,
        "format_dt": lambda v: str(v) if v else None,
        "BookBudget": object, "Character": object, "CharacterBudgetSnapshot": object, "ScriptChapter": object,
    })


def test_only_admin_switches(factory):
    with factory() as db:
        _book(db, "b1", "Первая")
        db.commit()
    denied = run(_handlers(factory, ["author"])["api_save_recording_book"](None, SaveRecordingBookPayload(book_id="b1")))
    assert denied.status_code == 403
    ok = run(_handlers(factory, ["admin"])["api_save_recording_book"](None, SaveRecordingBookPayload(book_id="b1")))
    assert ok == {"ok": True, "book_id": "b1"}
    assert _handlers(factory, ["dictor"])["api_recording_book"](None) == {"ok": True, "book_id": "b1"}
    with factory() as db:
        assert active_book(db).id == "b1"


def test_migration_does_not_overwrite_an_existing_recording_book(factory):
    path = Path(__file__).resolve().parents[1] / "alembic/versions/0056_recording_book.py"
    spec = importlib.util.spec_from_file_location("rev0056", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    with factory() as db:
        _book(db, "b1", "Другая")
        db.execute(text("INSERT INTO studio_settings (id, default_rate_rub_per_min, usd_rub_rate, updated_by, updated_at,"
                        " role_deadline_date, deadline_digest_day, onboarding_no_limit, onboarding_hidden)"
                        " VALUES ('default',1000,0,'',CURRENT_TIMESTAMP,'2026-12-31','',0,0)"))
        db.commit()
        mod.set_default_recording_book(db.connection())
        assert db.execute(text("SELECT recording_book_id FROM studio_settings")).scalar() == ""
        db.execute(text("UPDATE studio_settings SET recording_book_id = 'b1'"))
        db.commit()
        mod.set_default_recording_book(db.connection())
        assert db.execute(text("SELECT recording_book_id FROM studio_settings")).scalar() == "b1"

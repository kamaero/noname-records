"""«Первые шаги»: каждый шаг отмечается по настоящим данным, а не кнопкой."""
from datetime import timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import ScriptBook
from app.services import book_import, first_steps, provider_keys, spend, step_models
from app.services.studio_settings import studio_settings
from app.time_utils import utcnow_naive
from app.v2.models import V2Run

ENV: dict[str, str] = {}


@pytest.fixture()
def factory(monkeypatch, tmp_path):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    f = sessionmaker(bind=engine, autoflush=False)
    for module in (provider_keys, step_models, spend):
        monkeypatch.setattr(module, "SessionLocal", f)
    ENV.clear()
    monkeypatch.setattr(provider_keys, "env_value", lambda name, default="": ENV.get(name, default))
    for attr in provider_keys.SETTINGS_ATTRS.values():
        monkeypatch.setattr(provider_keys.settings, attr, "", raising=False)
    monkeypatch.setattr(book_import, "book_source_dir", lambda: tmp_path / "sources")
    provider_keys.clear_cache()
    step_models.clear_cache()
    return f


def _steps(db) -> dict[str, dict]:
    return {s["key"]: s for s in first_steps.checklist(db)["steps"]}


def test_fresh_studio_nothing_done(factory):
    with factory() as db:
        body = first_steps.checklist(db)
        assert [s["key"] for s in body["steps"]] == ["key", "models", "limit", "sample", "run", "result"]
        assert body["done_count"] == 0 and body["total"] == 6 and body["visible"] is True
        steps = _steps(db)
        assert steps["run"]["state"] == "locked" and steps["result"]["state"] == "locked"


def test_env_key_from_installer_counts_without_a_check(factory):
    ENV["DEEPSEEK_API_KEY"] = "sk-env-123456"
    with factory() as db:
        assert _steps(db)["key"]["done"] is True


def test_failed_check_keeps_the_key_step_open(factory):
    with factory() as db:
        provider_keys.set_key(db, "deepseek", "sk-site-123456", actor="admin")
        provider_keys.record_check(db, "deepseek", "bad_key", "Ключ не подходит — проверьте, что скопирован целиком.")
        step = _steps(db)["key"]
        assert step["done"] is False and "не подходит" in step["detail"]


def test_models_step_names_the_provider_without_a_key(factory):
    with factory() as db:
        provider_keys.set_key(db, "claude", "sk-ant-abcdefgh", actor="admin")
        step = _steps(db)["models"]
        # разметка по умолчанию на DeepSeek, а ключ только у Claude
        assert step["done"] is False and "DeepSeek" in step["detail"]


def test_limit_done_by_value_or_by_choice(factory):
    with factory() as db:
        assert _steps(db)["limit"]["done"] is False
        first_steps.set_flags(db, no_limit=True)
        assert _steps(db)["limit"]["done"] is True
        first_steps.set_flags(db, no_limit=False)
        studio_settings(db).monthly_limit_rub = 500
        assert _steps(db)["limit"]["done"] is True


def test_import_is_idempotent_and_reimports_after_delete(factory):
    with factory() as db:
        first = first_steps.import_sample(db, user_id="admin", user_name="Админ")
        again = first_steps.import_sample(db, user_id="admin", user_name="Админ")
        assert first.id == again.id
        assert db.query(ScriptBook).count() == 1
        assert first.title == "Номер двенадцатый" and first.author_label == "Noname Records"
        assert first.chapter_count == 2
        assert _steps(db)["sample"]["done"] is True
        assert first_steps.mark_seen(db, first.id) is True
        db.delete(first)
        db.commit()
        steps = _steps(db)
        assert steps["sample"]["done"] is False and steps["run"]["state"] == "locked"
        second = first_steps.import_sample(db, user_id="admin", user_name="Админ")
        assert second.id != first.id
        assert studio_settings(db).onboarding_result_seen_at is None


@pytest.mark.parametrize("status,state,done", [
    ("queued", "running", False), ("running", "running", False), ("done", "done", True),
    ("failed", "failed", False), ("stopped", "failed", False),
])
def test_run_step_follows_the_latest_run(factory, status, state, done):
    with factory() as db:
        book = first_steps.import_sample(db, user_id="admin", user_name="Админ")
        now = utcnow_naive()
        db.add(V2Run(id="old", book_id=book.id, status="done", updated_at=now - timedelta(hours=1)))
        db.add(V2Run(id="new", book_id=book.id, status=status, updated_at=now))
        db.commit()
        step = _steps(db)["run"]
        assert (step["state"], step["done"]) == (state, done)


def test_seen_only_for_the_sample(factory):
    with factory() as db:
        book = first_steps.import_sample(db, user_id="admin", user_name="Админ")
        assert first_steps.mark_seen(db, "some-other-book") is False
        assert _steps(db)["result"]["done"] is False
        assert first_steps.mark_seen(db, book.id) is True
        assert _steps(db)["result"]["done"] is True


def test_estimate_only_before_the_first_run(factory, monkeypatch):
    calls = []
    monkeypatch.setattr(first_steps.spend, "estimate_markup",
                        lambda db, book, steps, **kw: calls.append(steps) or (4.2, False))
    with factory() as db:
        assert first_steps.checklist(db)["sample_estimate_rub"] is None
        book = first_steps.import_sample(db, user_id="admin", user_name="Админ")
        assert first_steps.checklist(db)["sample_estimate_rub"] == 4.2
        db.add(V2Run(id="r", book_id=book.id, status="running"))
        db.commit()
        calls.clear()
        assert first_steps.checklist(db)["sample_estimate_rub"] is None and calls == []


def test_card_hides_when_dismissed_or_all_done(factory):
    with factory() as db:
        first_steps.set_flags(db, hidden=True)
        body = first_steps.checklist(db)
        assert body["visible"] is False and body["hidden"] is True

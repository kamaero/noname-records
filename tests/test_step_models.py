import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.services import provider_keys, step_models as sm


@pytest.fixture()
def factory(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    f = sessionmaker(bind=engine, autoflush=False)
    monkeypatch.setattr(sm, "SessionLocal", f)
    sm.clear_cache()
    yield f
    sm.clear_cache()


def test_defaults_are_todays_constants(factory):
    assert sm.step_model("attribution") == ("deepseek", "deepseek-v4-pro")
    assert sm.step_model("consilium_reader_1") == ("routerai", "anthropic/claude-opus-5")
    assert sm.step_model("consilium_reader_2") == ("routerai", "openai/gpt-5.6-sol")
    assert sm.step_model("consilium_arbiter") == ("routerai", "anthropic/claude-opus-5")
    assert sm.step_model("sound") == ("routerai", "anthropic/claude-opus-5")
    assert sm.step_model("ambient_text") == ("routerai", "anthropic/claude-opus-5")
    assert sm.step_model("ambient_audio") == ("elevenlabs", "music_v2")
    assert sm.step_model("asr") == ("openai", "whisper-1")


def test_a_chosen_model_wins(factory):
    with factory() as db:
        sm.set_step_model(db, "sound", "openrouter", "google/gemini-3-pro", actor="admin")
        db.commit()
    assert sm.step_model("sound") == ("openrouter", "google/gemini-3-pro")


def test_a_provider_the_step_cannot_use_is_refused(factory):
    with factory() as db, pytest.raises(ValueError):
        sm.set_step_model(db, "ambient_audio", "deepseek", "deepseek-v4-pro", actor="admin")


def test_a_step_without_its_key_refuses_before_spending(factory, monkeypatch):
    monkeypatch.setattr(provider_keys, "provider_key", lambda name: "")
    with pytest.raises(sm.MissingKeyError) as err:
        sm.require_key_for("sound")
    assert "RouterAI" in str(err.value) and "звуковой разметки" in str(err.value)


def test_asr_default_follows_env(factory, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "asr_provider", "routerai")
    monkeypatch.setattr(settings, "asr_model", "openai/whisper-1")
    assert sm.step_model("asr") == ("routerai", "openai/whisper-1")


def test_a_custom_attribution_model_reaches_the_run(factory, monkeypatch):
    from app.v2 import model_catalog
    with factory() as db:
        sm.set_step_model(db, "attribution", "openrouter", "vendor/new-model", actor="admin")
        db.commit()
    chosen = model_catalog.for_book(type("B", (), {"llm_provider": "", "llm_model": ""})())
    assert (chosen.provider, chosen.model) == ("openrouter", "vendor/new-model")


def test_a_books_own_choice_still_wins(factory):
    from app.v2 import model_catalog
    with factory() as db:
        sm.set_step_model(db, "attribution", "openrouter", "vendor/new-model", actor="admin")
        db.commit()
    book = type("B", (), {"llm_provider": "deepseek", "llm_model": "deepseek-v4-pro"})()
    assert model_catalog.for_book(book).model == "deepseek-v4-pro"


def test_a_custom_model_has_no_price_rather_than_a_zero_one(factory):
    from app.v2 import model_catalog
    with factory() as db:
        sm.set_step_model(db, "attribution", "openrouter", "vendor/new-model", actor="admin")
        db.commit()
    chosen = model_catalog.for_book(type("B", (), {"llm_provider": "", "llm_model": ""})())
    assert chosen.cost_rub(1000, 1000, 95.0) is None
    assert chosen.as_dict(95.0)["rub_in"] is None


def test_a_database_without_the_table_gives_the_defaults(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    monkeypatch.setattr(sm, "SessionLocal", sessionmaker(bind=engine))
    sm.clear_cache()
    assert sm.step_model("sound") == ("routerai", "anthropic/claude-opus-5")

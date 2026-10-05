import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db import Base
from app.services import provider_keys as pk


@pytest.fixture()
def factory(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    f = sessionmaker(bind=engine, autoflush=False)
    monkeypatch.setattr(pk, "SessionLocal", f)
    monkeypatch.setattr(settings, "keys_encryption_key", "")
    monkeypatch.setattr(settings, "secret_key", "s" * 48)
    monkeypatch.setattr(pk, "env_value", lambda name, default="": {"DEEPSEEK_API_KEY": "env-deepseek-key"}.get(name, default))
    pk.clear_cache()
    yield f
    pk.clear_cache()


def test_the_site_key_wins_over_env(factory):
    with factory() as db:
        pk.set_key(db, "deepseek", "  sk-site-123456\n", actor="admin")
        db.commit()
    assert pk.provider_key("deepseek") == "sk-site-123456"


def test_without_a_site_key_env_is_used(factory):
    assert pk.provider_key("deepseek") == "env-deepseek-key"
    assert pk.provider_key("claude") == ""


def test_the_stored_value_is_encrypted_and_trimmed(factory):
    with factory() as db:
        pk.set_key(db, "claude", " sk-ant-abcdefgh \n", actor="admin")
        db.commit()
        row = db.get(pk.ProviderKey, "claude")
        assert "sk-ant" not in row.ciphertext and row.last4 == "efgh"
        info = pk.key_info(db, "claude")
    assert info == {**info, "source": "site", "last4": "efgh", "unreadable": False}


def test_a_changed_secret_makes_the_key_unreadable_not_fatal(factory, monkeypatch):
    with factory() as db:
        pk.set_key(db, "deepseek", "sk-site-123456", actor="admin")
        db.commit()
    monkeypatch.setattr(settings, "secret_key", "t" * 48)
    pk.clear_cache()
    assert pk.provider_key("deepseek") == "env-deepseek-key"
    with factory() as db:
        assert pk.key_info(db, "deepseek")["unreadable"] is True


def test_the_cache_holds_for_thirty_seconds(factory, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(pk, "_now", lambda: clock[0])
    assert pk.provider_key("claude") == ""
    with factory() as db:
        pk.set_key(db, "claude", "sk-new-key-0001", actor="admin")
        db.commit()
    assert pk.provider_key("claude") == "sk-new-key-0001"  # set_key сбрасывает кэш своего процесса
    pk._CACHE["claude"] = (clock[0], "stale")               # чужой процесс: только время
    clock[0] += 29
    assert pk.provider_key("claude") == "stale"
    clock[0] += 2
    assert pk.provider_key("claude") == "sk-new-key-0001"


def test_delete_falls_back_to_env(factory):
    with factory() as db:
        pk.set_key(db, "deepseek", "sk-site-123456", actor="admin")
        pk.delete_key(db, "deepseek", actor="admin")
        db.commit()
    assert pk.provider_key("deepseek") == "env-deepseek-key"


def test_an_unknown_provider_is_refused(factory):
    with factory() as db, pytest.raises(ValueError):
        pk.set_key(db, "zai", "x" * 20, actor="admin")

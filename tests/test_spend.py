from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import ModelPrice, SpendEntry
from app.services import spend


@pytest.fixture()
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, autoflush=False)() as session:
        from app.services.studio_settings import studio_settings
        studio_settings(db=session).usd_rub_rate = 100.0
        session.commit()
        yield session


def test_a_catalog_model_is_priced_in_rubles(db):
    row = spend.record(db, step="attribution", provider="deepseek", model="deepseek-v4-pro",
                       unit="tokens", input_units=1_000_000, output_units=1_000_000)
    assert row.price_known and row.rub == pytest.approx((0.66 + 1.98) * 100)


def test_an_unknown_model_has_no_price_not_zero(db):
    row = spend.record(db, step="sound", provider="routerai", model="anthropic/claude-opus-5",
                       unit="tokens", input_units=500, output_units=100)
    assert row.price_known is False and row.rub is None


def test_a_studio_price_wins_and_does_not_rewrite_the_past(db):
    first = spend.record(db, step="sound", provider="routerai", model="vendor/x", unit="tokens",
                         input_units=1_000_000, output_units=0)
    db.add(ModelPrice(provider="routerai", model="vendor/x", unit="tokens", price_in=200, price_out=400, currency="RUB"))
    db.flush()
    second = spend.record(db, step="sound", provider="routerai", model="vendor/x", unit="tokens",
                          input_units=1_000_000, output_units=0)
    assert first.rub is None and second.rub == pytest.approx(200)


def test_no_usd_rate_means_unknown(db):
    from app.services.studio_settings import studio_settings
    studio_settings(db=db).usd_rub_rate = 0.0
    db.flush()
    assert spend.record(db, step="attribution", provider="deepseek", model="deepseek-v4-pro",
                        unit="tokens", input_units=10, output_units=10).rub is None


def test_whisper_is_priced_per_minute(db):
    row = spend.record(db, step="asr", provider="openai", model="whisper-1", unit="seconds",
                       input_units=120, output_units=0)
    assert row.rub == pytest.approx(0.006 * 2 * 100)


def test_the_month_is_moscow_time():
    assert spend.month_key(datetime(2026, 10, 31, 20, 30)) == "2026-10"   # 23:30 МСК 31-го
    assert spend.month_key(datetime(2026, 10, 31, 21, 10)) == "2026-11"   # 00:10 МСК 1-го


def test_the_summary_sums_the_month_and_never_shows_a_negative_rest(db):
    from app.services.studio_settings import studio_settings
    studio_settings(db=db).monthly_limit_rub = 100
    for _ in range(3):
        spend.record(db, step="attribution", provider="deepseek", model="deepseek-v4-pro",
                     unit="tokens", input_units=1_000_000, output_units=0)
    spend.record(db, step="sound", provider="routerai", model="vendor/unknown", unit="tokens",
                 input_units=1, output_units=1)
    summary = spend.month_summary(db, spend.month_key(datetime.utcnow()))
    assert summary["total_rub"] == pytest.approx(3 * 66)
    assert summary["unknown_calls"] == 1 and summary["left_rub"] == 0
    assert summary["by_step"]["attribution"]["rub"] == pytest.approx(198)

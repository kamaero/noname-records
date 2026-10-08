"""Месячный лимит: новые платные прогоны сверх него не стартуют без «сверх лимита» администратора."""
import json
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import AuditLog
from app.services import spend
from app.services.studio_settings import studio_settings
from tests.spend_helpers import calm_now


@pytest.fixture()
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, autoflush=False)() as session:
        studio_settings(session).usd_rub_rate = 100.0
        session.commit()
        yield session


def _spent(db, rub_millions=1):
    for _ in range(rub_millions):  # 1 млн входных токенов DeepSeek = 66 ₽ при курсе 100
        spend.record(db, step="attribution", provider="deepseek", model="deepseek-v4-pro",
                     unit="tokens", input_units=1_000_000, output_units=0, now=calm_now())


def _check(db, estimate, **kw):
    return spend.check_start(db, estimate_rub=estimate, unknown_price=kw.get("unknown", False),
                             override=kw.get("override", False), is_admin=kw.get("admin", False),
                             actor_uid="u1", what="консилиум")


def test_no_limit_lets_everything_start(db):
    _spent(db, 3)
    assert _check(db, 10_000).allowed


def test_enough_left_starts(db):
    studio_settings(db).monthly_limit_rub = 1000
    _spent(db, 1)
    decision = _check(db, 500)
    assert decision.allowed and decision.left_rub == pytest.approx(934)


def test_not_enough_left_is_refused_with_the_numbers(db):
    studio_settings(db).monthly_limit_rub = 100
    _spent(db, 1)
    decision = _check(db, 900)
    assert not decision.allowed and (decision.estimate_rub, decision.left_rub, decision.limit_rub) == (900, 34, 100)


def test_a_limit_lowered_below_the_spend_leaves_zero_not_a_negative(db):
    studio_settings(db).monthly_limit_rub = 50
    _spent(db, 2)
    decision = _check(db, 1)
    assert not decision.allowed and decision.left_rub == 0


def test_an_admin_override_starts_and_is_logged(db):
    studio_settings(db).monthly_limit_rub = 10
    _spent(db, 1)
    assert _check(db, 900, override=True, admin=True).allowed
    [log] = db.query(AuditLog).filter(AuditLog.entity_type == "spend_limit").all()
    assert log.action == "override" and json.loads(log.payload_json)["estimate_rub"] == 900


def test_a_non_admin_cannot_override(db):
    studio_settings(db).monthly_limit_rub = 10
    with pytest.raises(PermissionError):
        _check(db, 900, override=True, admin=False)


def test_an_unknown_price_starts_with_a_warning(db):
    studio_settings(db).monthly_limit_rub = 1000
    decision = _check(db, 0, unknown=True)
    assert decision.allowed and decision.unknown_price


def test_thresholds_warn_once_each_and_again_next_month(db):
    studio_settings(db).monthly_limit_rub = 100
    sent = []
    send = lambda text: sent.append(text) or True  # noqa: E731
    now = datetime.utcnow()
    assert spend.warn_thresholds(db, now, send) == 0
    _spent(db, 1)                                     # 66 % — ещё молчим
    spend.record(db, step="sound", provider="deepseek", model="deepseek-v4-pro", unit="tokens",
                 input_units=200_000, output_units=0, now=calm_now())  # +13,2 ₽ → 79,2 %
    assert spend.warn_thresholds(db, now, send) == 0
    _spent(db, 1)                                     # 145 % — сразу 100 %
    assert spend.warn_thresholds(db, now, send) == 1 and "100" in sent[-1]
    assert spend.warn_thresholds(db, now, send) == 0


def test_eighty_percent_warns_before_a_hundred(db):
    studio_settings(db).monthly_limit_rub = 80
    sent = []
    _spent(db, 1)                                     # 66 / 80 = 82 %
    assert spend.warn_thresholds(db, datetime.utcnow(), lambda t: sent.append(t) or True) == 1
    assert "80" in sent[0]

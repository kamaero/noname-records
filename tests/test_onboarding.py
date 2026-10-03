"""Приветственное окно диктора: правило показа, кэш достижимости бота, ручки /api/me.

`should_show` — чистая функция, её ветки не трогают ни БД, ни сеть. `bot_reachable`
дороже — поход в Telegram, поэтому кэш и таймаут проверяются с подменённым `check`,
а не настоящим `check_bot_reach` (сторож в conftest всё равно не пустит настоящий
`requests.get`, если кто-то забудет подменить).
"""
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from app.auth import session_serializer
from app.config import settings
from app.db import Base, SessionLocal, engine as app_engine
from app.main import app
from app.models import User
from app.services import onboarding, user_admin
from app.time_utils import utcnow_naive

NOW = datetime(2026, 9, 22, 12, 0, 0)


class _Answer:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


@pytest.fixture(autouse=True)
def _database():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    SessionLocal.configure(bind=engine)
    try:
        yield
    finally:
        SessionLocal.configure(bind=app_engine)


@pytest.fixture(autouse=True)
def _clean_bot_reach_cache():
    onboarding.reset_state()
    yield
    onboarding.reset_state()


def _inline(job):
    job()


def _make_user(display_name: str = "Диктор Новый") -> str:
    with SessionLocal() as db:
        made = user_admin.create_user(db, display_name=display_name, roles=["dictor"])
        db.commit()
        return made["user"]["id"]


def _cookie(uid: str, roles, *, auth_source: str = "", telegram_user_id: str = "") -> str:
    payload = {"uid": uid, "sub": uid, "roles": list(roles), "display_name": "Кто-то"}
    if auth_source:
        payload["auth_source"] = auth_source
    if telegram_user_id:
        payload["telegram_user_id"] = telegram_user_id
    return session_serializer.dumps(payload)


class TestShouldShow:
    def test_not_a_dictor_never_shows(self):
        assert onboarding.should_show(is_dictor=False, auth_source="", shown_at=None, bot_reachable=None, now=NOW) is False

    def test_a_dictor_never_shown_before_sees_it(self):
        assert onboarding.should_show(is_dictor=True, auth_source="", shown_at=None, bot_reachable=None, now=NOW) is True

    def test_password_login_after_first_show_stays_quiet(self):
        shown_at = NOW - timedelta(days=10)
        assert onboarding.should_show(is_dictor=True, auth_source="", shown_at=shown_at, bot_reachable=None, now=NOW) is False

    def test_telegram_with_a_working_bot_stays_quiet(self):
        shown_at = NOW - timedelta(days=10)
        assert onboarding.should_show(is_dictor=True, auth_source="telegram", shown_at=shown_at, bot_reachable=True, now=NOW) is False

    def test_telegram_unknown_reachability_does_not_push(self):
        """`bot_reachable is None` значит «не дозвонились», а не «нельзя писать» — не повод показывать снова."""
        shown_at = NOW - timedelta(days=10)
        assert onboarding.should_show(is_dictor=True, auth_source="telegram", shown_at=shown_at, bot_reachable=None, now=NOW) is False

    def test_telegram_unreachable_but_recent_stays_quiet(self):
        shown_at = NOW - timedelta(hours=1)
        assert onboarding.should_show(is_dictor=True, auth_source="telegram", shown_at=shown_at, bot_reachable=False, now=NOW) is False

    def test_telegram_unreachable_for_a_day_shows_again(self):
        shown_at = NOW - timedelta(hours=24)
        assert onboarding.should_show(is_dictor=True, auth_source="telegram", shown_at=shown_at, bot_reachable=False, now=NOW) is True


class TestBotReachable:
    def test_an_empty_id_asks_nothing(self):
        calls = []

        def fake_check(rows, *, token, timeout):
            calls.append(rows)
            return {"items": []}

        assert onboarding.bot_reachable("", check=fake_check) is None
        assert calls == []

    def test_a_second_call_within_ttl_uses_the_cache(self):
        calls = []

        def fake_check(rows, *, token, timeout):
            calls.append(rows)
            return {"items": [{"telegram_user_id": rows[0]["telegram_user_id"], "reachable": True}]}

        clock = iter([0.0, 1.0, 10.0])

        first = onboarding.bot_reachable("123", check=fake_check, now=lambda: next(clock), spawn=_inline)
        second = onboarding.bot_reachable("123", check=fake_check, now=lambda: next(clock), spawn=_inline)

        assert first is None  # промах кэша: ответ ещё неизвестен, проверка ушла в фон
        assert second is True
        assert len(calls) == 1

    def test_fresh_bypasses_the_cache(self):
        calls = []

        def fake_check(rows, *, token, timeout):
            calls.append(rows)
            return {"items": [{"telegram_user_id": rows[0]["telegram_user_id"], "reachable": False}]}

        onboarding.bot_reachable("123", check=fake_check, now=lambda: 0.0, spawn=_inline)
        fresh = onboarding.bot_reachable("123", fresh=True, check=fake_check, now=lambda: 1.0, spawn=_inline)

        assert fresh is False
        assert len(calls) == 2

    def test_the_cache_expires_after_its_ttl(self):
        calls = []

        def fake_check(rows, *, token, timeout):
            calls.append(rows)
            return {"items": [{"telegram_user_id": rows[0]["telegram_user_id"], "reachable": True}]}

        clock = iter([0.0, 0.0, onboarding.CACHE_TTL_SECONDS + 1, onboarding.CACHE_TTL_SECONDS + 1])

        onboarding.bot_reachable("123", check=fake_check, now=lambda: next(clock), spawn=_inline)
        stale = onboarding.bot_reachable("123", check=fake_check, now=lambda: next(clock), spawn=_inline)

        assert stale is True  # просроченный ответ отдаётся сразу, а перепроверка — в фоне
        assert len(calls) == 2

    def test_a_failed_check_is_unknown_not_unreachable(self):
        def fake_check(rows, *, token, timeout):
            return {"items": [], "error": "no_token"}

        assert onboarding.bot_reachable("123", fresh=True, check=fake_check, now=lambda: 0.0) is None

    def test_it_hands_the_real_check_bot_reach_its_timeout_and_token(self, monkeypatch):
        monkeypatch.setattr(settings, "telegram_bot_token", "test-token")
        seen = {}

        def fake_check(rows, *, token, timeout):
            seen["rows"] = rows
            seen["token"] = token
            seen["timeout"] = timeout
            return {"items": [{"telegram_user_id": "123", "reachable": True}]}

        onboarding.bot_reachable("123", fresh=True, check=fake_check, now=lambda: 0.0)

        assert seen == {"rows": [{"telegram_user_id": "123"}], "token": "test-token", "timeout": onboarding.REACH_TIMEOUT_SECONDS}


class TestBackgroundRefresh:
    """`/api/me` не ждёт Telegram: промах кэша отвечает сразу, проверка уходит в фон."""

    def test_a_cache_miss_answers_at_once_and_schedules_one_refresh(self):
        calls, jobs = [], []

        def fake_check(rows, *, token, timeout):
            calls.append(rows)
            return {"items": [{"telegram_user_id": "123", "reachable": True}]}

        value = onboarding.bot_reachable("123", check=fake_check, now=lambda: 0.0, spawn=jobs.append)

        assert value is None
        assert calls == []  # синхронно в Telegram не ходили
        assert len(jobs) == 1
        jobs[0]()
        assert len(calls) == 1
        assert onboarding.bot_reachable("123", check=fake_check, now=lambda: 1.0, spawn=jobs.append) is True

    def test_a_second_miss_during_the_refresh_does_not_schedule_another(self):
        jobs = []

        def fake_check(rows, *, token, timeout):
            return {"items": [{"telegram_user_id": "123", "reachable": False}]}

        onboarding.bot_reachable("123", check=fake_check, now=lambda: 0.0, spawn=jobs.append)
        onboarding.bot_reachable("123", check=fake_check, now=lambda: 0.5, spawn=jobs.append)

        assert len(jobs) == 1
        jobs[0]()  # перепроверка закончилась — следующий промах вправе завести новую
        onboarding._cache.clear()
        onboarding.bot_reachable("123", check=fake_check, now=lambda: 1.0, spawn=jobs.append)
        assert len(jobs) == 2

    def test_a_failed_refresh_frees_the_slot_and_keeps_the_old_answer(self):
        jobs = []

        def broken_check(rows, *, token, timeout):
            raise RuntimeError("сеть")

        onboarding._cache["123"] = (0.0, True)
        stale_at = onboarding.CACHE_TTL_SECONDS + 1
        assert onboarding.bot_reachable("123", check=broken_check, now=lambda: stale_at, spawn=jobs.append) is True
        jobs[0]()
        assert "123" not in onboarding._refreshing
        assert onboarding._cache["123"] == (0.0, True)

    def test_fresh_asks_synchronously_and_schedules_nothing(self):
        calls, jobs = [], []

        def fake_check(rows, *, token, timeout):
            calls.append(rows)
            return {"items": [{"telegram_user_id": "123", "reachable": True}]}

        assert onboarding.bot_reachable("123", fresh=True, check=fake_check, now=lambda: 0.0, spawn=jobs.append) is True
        assert len(calls) == 1
        assert jobs == []

    def test_the_real_spawn_is_a_thread_not_the_request(self, monkeypatch):
        """Без подмены из conftest перепроверка уходит в поток, а не выполняется на месте."""
        started = []

        class FakeThread:
            def __init__(self, target, name, daemon):
                started.append((name, daemon))

            def start(self):
                pass

        monkeypatch.setattr(onboarding.threading, "Thread", FakeThread)
        onboarding._spawn_thread(lambda: None)
        assert started == [("bot-reach-refresh", True)]


class TestApiMe:
    def test_logged_out_hides_the_popup(self):
        client = TestClient(app)

        resp = client.get("/api/me")

        assert resp.status_code == 401
        body = resp.json()
        assert body["bot_reachable"] is None
        assert body["show_onboarding"] is False

    def test_a_new_dictor_by_password_is_shown_the_popup(self):
        uid = _make_user()
        client = TestClient(app)
        client.cookies.set("session", _cookie(uid, ["dictor"]))

        body = client.get("/api/me").json()

        assert body["show_onboarding"] is True
        assert body["bot_reachable"] is None

    def test_marking_it_shown_turns_the_popup_off(self):
        uid = _make_user()
        client = TestClient(app)
        client.cookies.set("session", _cookie(uid, ["dictor"]))

        assert client.post("/api/me/onboarding-shown").json() == {"ok": True}
        assert client.get("/api/me").json()["show_onboarding"] is False

    def test_marking_it_shown_requires_a_login(self):
        client = TestClient(app)

        assert client.post("/api/me/onboarding-shown").status_code == 401

    def test_a_non_dictor_never_sees_it(self):
        uid = _make_user()
        client = TestClient(app)
        client.cookies.set("session", _cookie(uid, ["author"]))

        assert client.get("/api/me").json()["show_onboarding"] is False

    def test_a_telegram_non_dictor_never_asks_telegram(self, monkeypatch):
        """Владелец чаще всех входит через Telegram — окно не его, и цена его /api/me не его."""
        uid = _make_user()
        monkeypatch.setattr(settings, "telegram_bot_token", "test-token")
        calls = []

        def fake_get(*a, **k):
            calls.append(1)
            return _Answer({"ok": True, "result": {"first_name": "Жека"}})

        monkeypatch.setattr("app.services.bot_reach.requests.get", fake_get)
        client = TestClient(app)
        client.cookies.set("session", _cookie(uid, ["admin"], auth_source="telegram", telegram_user_id="777"))

        body = client.get("/api/me").json()

        assert calls == []
        assert body["bot_reachable"] is None
        assert body["show_onboarding"] is False

    def test_a_password_dictor_stays_quiet_after_the_first_show_however_long(self):
        uid = _make_user()
        with SessionLocal() as db:
            user = db.query(User).filter(User.id == uid).first()
            user.onboarding_shown_at = utcnow_naive() - timedelta(days=30)
            db.commit()
        client = TestClient(app)
        client.cookies.set("session", _cookie(uid, ["dictor"]))

        assert client.get("/api/me").json()["show_onboarding"] is False

    def test_telegram_dictor_gets_the_bot_reachable_field(self, monkeypatch):
        uid = _make_user()
        monkeypatch.setattr(settings, "telegram_bot_token", "test-token")
        monkeypatch.setattr(
            "app.services.bot_reach.requests.get",
            lambda *a, **k: _Answer({"ok": False, "description": "Bad Request: chat not found"}),
        )
        client = TestClient(app)
        client.cookies.set("session", _cookie(uid, ["dictor"], auth_source="telegram", telegram_user_id="777"))

        first = client.get("/api/me").json()
        body = client.get("/api/me").json()

        assert first["bot_reachable"] is None  # первый заход не ждёт Telegram
        assert first["show_onboarding"] is True
        assert body["bot_reachable"] is False
        # ещё ни разу не показывали — показывается независимо от достижимости бота
        assert body["show_onboarding"] is True

    def test_telegram_dictor_unreachable_a_day_after_the_first_show_is_reminded(self, monkeypatch):
        uid = _make_user()
        monkeypatch.setattr(settings, "telegram_bot_token", "test-token")
        monkeypatch.setattr(
            "app.services.bot_reach.requests.get",
            lambda *a, **k: _Answer({"ok": False, "description": "Bad Request: chat not found"}),
        )
        client = TestClient(app)
        client.cookies.set("session", _cookie(uid, ["dictor"], auth_source="telegram", telegram_user_id="777"))
        client.post("/api/me/onboarding-shown")
        with SessionLocal() as db:
            user = db.query(User).filter(User.id == uid).first()
            user.onboarding_shown_at = utcnow_naive() - timedelta(hours=25)
            db.commit()
        onboarding.reset_state()  # свежий взгляд, а не тот, что кэшировала первая проверка
        client.get("/api/me")  # промах кэша: ответ приходит фоном

        body = client.get("/api/me").json()

        assert body["bot_reachable"] is False
        assert body["show_onboarding"] is True

    def test_telegram_dictor_with_a_working_bot_is_not_nagged_twice(self, monkeypatch):
        uid = _make_user()
        monkeypatch.setattr(settings, "telegram_bot_token", "test-token")
        monkeypatch.setattr(
            "app.services.bot_reach.requests.get",
            lambda *a, **k: _Answer({"ok": True, "result": {"first_name": "Жека"}}),
        )
        client = TestClient(app)
        client.cookies.set("session", _cookie(uid, ["dictor"], auth_source="telegram", telegram_user_id="777"))
        client.post("/api/me/onboarding-shown")
        client.get("/api/me")  # промах кэша: ответ приходит фоном

        body = client.get("/api/me").json()

        assert body["bot_reachable"] is True
        assert body["show_onboarding"] is False


class TestApiMeBotReach:
    def test_requires_a_login(self):
        client = TestClient(app)

        assert client.get("/api/me/bot-reach").status_code == 401

    def test_password_login_has_nothing_to_check(self):
        uid = _make_user()
        client = TestClient(app)
        client.cookies.set("session", _cookie(uid, ["dictor"]))

        assert client.get("/api/me/bot-reach").json() == {"ok": True, "bot_reachable": None}

    def test_the_retry_button_asks_fresh_not_cached(self, monkeypatch):
        uid = _make_user()
        monkeypatch.setattr(settings, "telegram_bot_token", "test-token")
        calls = []

        def fake_get(*a, **k):
            calls.append(1)
            return _Answer({"ok": True, "result": {"first_name": "Жека"}})

        monkeypatch.setattr("app.services.bot_reach.requests.get", fake_get)
        client = TestClient(app)
        client.cookies.set("session", _cookie(uid, ["dictor"], auth_source="telegram", telegram_user_id="777"))

        client.get("/api/me")  # первый заход кладёт ответ в кэш
        resp = client.get("/api/me/bot-reach")

        assert resp.json() == {"ok": True, "bot_reachable": True}
        # первый запрос кладёт ответ в кэш на 5 минут, второй (`fresh=True`) его обходит —
        # значит настоящих обращений к Telegram должно быть два, а не одно
        assert len(calls) == 2

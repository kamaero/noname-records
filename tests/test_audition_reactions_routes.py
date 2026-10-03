"""Ручка реакции: ставят автор и владелец студии (с 03.10 — решение владельца: «как у автора»). Список проб: реакции видны по правилам сервиса."""
from fastapi.testclient import TestClient

from app.auth import session_serializer
from app.main import app
from app.services.audition_reactions import ReactionError


def _client(roles, name="Александр Белозёров"):
    client = TestClient(app)
    client.cookies.set("session", session_serializer.dumps({"uid": "u1", "sub": "u1", "roles": roles, "display_name": name}))
    return client


def _patch_set(monkeypatch, calls, error=None):
    def fake(db, audio_id, *, voter_uid, voter_name, value, now=None):
        if error:
            raise ReactionError(error)
        calls.append((audio_id, voter_uid, voter_name, value))
        return {"author_reaction": value or None, "rejection": None}

    monkeypatch.setattr("app.v2.api.set_reaction", fake)


def test_a_stranger_cannot_react():
    assert TestClient(app).post("/api/v2/auditions/a1/reaction", json={"value": 1}).status_code == 401


def test_only_the_author_and_the_admin_react(monkeypatch):
    calls: list = []
    _patch_set(monkeypatch, calls)

    for roles in (["agent"], ["dictor"]):
        assert _client(roles).post("/api/v2/auditions/a1/reaction", json={"value": -1}).status_code == 403
    assert calls == []


def test_the_author_reacts(monkeypatch):
    calls: list = []
    _patch_set(monkeypatch, calls)

    response = _client(["author"]).post("/api/v2/auditions/a1/reaction", json={"value": -1})

    assert response.status_code == 200
    assert response.json() == {"ok": True, "author_reaction": -1, "rejection": None}
    assert calls == [("a1", "u1", "Александр Белозёров", -1)]


def test_the_admin_reacts_like_the_author(monkeypatch):
    calls: list = []
    _patch_set(monkeypatch, calls)
    response = _client(["admin"], name="Тимур Садыков").post("/api/v2/auditions/a1/reaction", json={"value": -1})
    assert response.status_code == 200 and calls == [("a1", "u1", "Тимур Садыков", -1)]


def test_missing_audition_and_bad_value(monkeypatch):
    _patch_set(monkeypatch, [], error="not_found")
    assert _client(["author"]).post("/api/v2/auditions/a1/reaction", json={"value": 1}).status_code == 404

    _patch_set(monkeypatch, [], error="bad_value")
    assert _client(["author"]).post("/api/v2/auditions/a1/reaction", json={"value": 7}).status_code == 400
    assert _client(["author"]).post("/api/v2/auditions/a1/reaction", content=b"nope").status_code == 400


def test_the_list_says_who_may_react(monkeypatch):
    seen = {}
    monkeypatch.setattr("app.v2.api.book_auditions", lambda db, *, book_id: [{"id": "a1", "actor_name": "X"}])

    def attach(db, rows, *, book_id, viewer_name, sees_all):
        seen.update(viewer_name=viewer_name, sees_all=sees_all)
        return rows

    monkeypatch.setattr("app.v2.api.attach_reactions", attach)

    assert _client(["author"]).get("/api/v2/books/b1/auditions").json()["can_react"] is True
    assert _client(["admin"]).get("/api/v2/books/b1/auditions").json()["can_react"] is True
    assert _client(["agent"]).get("/api/v2/books/b1/auditions").json()["can_react"] is False
    body = _client(["dictor"], name="Роман Сомов").get("/api/v2/books/b1/auditions").json()
    assert body["can_react"] is False
    assert seen == {"viewer_name": "Роман Сомов", "sees_all": False}

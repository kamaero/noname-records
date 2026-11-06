"""Ручка оценки: автор жмёт верхнюю шкалу, владелец — нижнюю, остальные — никакую."""
from fastapi.testclient import TestClient

from app.auth import session_serializer
from app.main import app
from app.services.audition_scores import ScoreError

BODY = {"book_id": "b1", "role": "Саяху", "actor_name": "Гленто Гарсин", "lane": "author", "score": 8}


def _client(roles, name="Вискон Варени"):
    client = TestClient(app)
    client.cookies.set("session", session_serializer.dumps({"uid": "u1", "sub": "u1", "roles": roles, "display_name": name}))
    return client


def _patch(monkeypatch, calls, error=None):
    def fake(db, *, book_id, role, actor_name, lane, score, voter_uid, voter_name, reason=None):
        if error:
            raise ScoreError(error)
        calls.append((book_id, role, actor_name, lane, score, voter_uid, voter_name))
        return {"author": {"score": score, "name": voter_name} if score else None, "studio": None}

    monkeypatch.setattr("app.v2.api.set_score", fake)
    monkeypatch.setattr("app.v2.api.sync_rejection_with_scores",
                        lambda db, *, book_id, role, actor_name: calls.append(("sync", book_id, role, actor_name)) or None)


def test_a_stranger_cannot_score():
    assert TestClient(app).post("/api/v2/auditions/score", json=BODY).status_code == 401


def test_the_author_scores_the_author_lane(monkeypatch):
    calls: list = []
    _patch(monkeypatch, calls)

    response = _client(["author"]).post("/api/v2/auditions/score", json=BODY)

    assert response.status_code == 200
    assert response.json() == {"ok": True, "scores": {"author": {"score": 8, "name": "Вискон Варени"}, "studio": None},
                               "rejection": None}
    # после оценки ручка сверяет отказ пары — оба ниже 7 значит письмо через 15 минут
    assert calls == [("b1", "Саяху", "Гленто Гарсин", "author", 8, "u1", "Вискон Варени"),
                     ("sync", "b1", "Саяху", "Гленто Гарсин")]


def test_nobody_presses_somebody_elses_lane(monkeypatch):
    calls: list = []
    _patch(monkeypatch, calls)

    assert _client(["admin"], name="Фернос Фарита").post("/api/v2/auditions/score", json=BODY).status_code == 403
    assert _client(["author"]).post("/api/v2/auditions/score", json={**BODY, "lane": "studio"}).status_code == 403
    for roles in (["agent"], ["dictor"]):
        assert _client(roles).post("/api/v2/auditions/score", json=BODY).status_code == 403
    assert calls == []


def test_the_admin_scores_the_studio_lane(monkeypatch):
    calls: list = []
    _patch(monkeypatch, calls)
    response = _client(["admin"], name="Фернос Фарита").post("/api/v2/auditions/score", json={**BODY, "lane": "studio"})
    assert response.status_code == 200 and calls[0][3] == "studio"


def test_null_takes_the_score_back(monkeypatch):
    calls: list = []
    _patch(monkeypatch, calls)
    assert _client(["author"]).post("/api/v2/auditions/score", json={**BODY, "score": None}).status_code == 200
    assert calls[0][4] is None


def test_service_refusals_become_http(monkeypatch):
    _patch(monkeypatch, [], error="not_found")
    assert _client(["author"]).post("/api/v2/auditions/score", json=BODY).status_code == 404
    _patch(monkeypatch, [], error="bad_score")
    assert _client(["author"]).post("/api/v2/auditions/score", json={**BODY, "score": 11}).status_code == 400
    assert _client(["author"]).post("/api/v2/auditions/score", content=b"nope").status_code == 400


def test_the_old_reaction_route_is_gone():
    assert _client(["author"]).post("/api/v2/auditions/a1/reaction", json={"value": 1}).status_code in (404, 405)


def test_the_reason_reaches_the_service(monkeypatch):
    seen = {}

    def fake(db, **kw):
        seen.update(kw)
        return {"author": {"score": kw["score"], "name": "А", "reason": kw["reason"] or ""}, "studio": None}

    monkeypatch.setattr("app.v2.api.set_score", fake)
    monkeypatch.setattr("app.v2.api.sync_rejection_with_scores", lambda db, **kw: None)
    response = _client(["author"]).post("/api/v2/auditions/score", json={**BODY, "score": 3, "reason": "manner"})
    assert response.status_code == 200 and seen["reason"] == "manner"
    _client(["author"]).post("/api/v2/auditions/score", json={**BODY, "score": 3})
    assert seen["reason"] is None


def test_a_reason_from_the_author_notifies_the_owner_after_the_response(monkeypatch):
    """Реакция автора — владельцу; из фоновой задачи ответа, а не из обработчика (uvicorn один)."""
    sent: list = []
    _patch(monkeypatch, [])
    monkeypatch.setattr("app.v2.api.send_reaction_to_owner", lambda *pair: sent.append(pair))
    _client(["author"]).post("/api/v2/auditions/score", json={**BODY, "score": 4, "reason": "voice"})
    assert sent == [("b1", "Саяху", "Гленто Гарсин")]
    sent.clear()
    _client(["author"]).post("/api/v2/auditions/score", json={**BODY, "score": 4})   # голая оценка — без письма
    assert sent == []


def test_reason_and_own_words_go_in_one_request_and_one_notice(monkeypatch):
    calls: list = []
    sent: list = []
    _patch(monkeypatch, calls)
    monkeypatch.setattr("app.v2.api.set_comment", lambda db, **kw: calls.append(("comment", kw["comment"])) or {})
    monkeypatch.setattr("app.v2.api.send_reaction_to_owner", lambda *pair: sent.append(pair))
    _client(["author"]).post("/api/v2/auditions/score", json={**BODY, "score": 4, "reason": "voice", "comment": "звонче"})
    assert ("comment", "звонче") in calls and len(sent) == 1

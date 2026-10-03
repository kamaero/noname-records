from fastapi.testclient import TestClient
from app.main import app


def test_v2_stress_routes_registered_and_guarded():
    client = TestClient(app)
    r1 = client.get("/api/v2/books/some-id/stress-queue")
    r2 = client.post("/api/v2/books/some-id/stress-term", json={"word": "x", "stressed": "x́"})
    assert r1.status_code in (401, 403)
    assert r2.status_code in (401, 403)

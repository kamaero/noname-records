from fastapi.testclient import TestClient

from app.main import app


def test_csp_allows_telegram_login_widget():
    """The Telegram Login Widget invokes its data-onauth callback via eval(),
    so script-src must include 'unsafe-eval' (plus the telegram hosts) or login
    silently fails after the popup authorizes."""
    resp = TestClient(app).get("/app/login")
    csp = resp.headers.get("content-security-policy", "")
    assert "'unsafe-eval'" in csp
    assert "https://telegram.org" in csp
    assert "https://oauth.telegram.org" in csp

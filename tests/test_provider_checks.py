import pytest
import requests

from app.services import provider_checks as pc


class Resp:
    def __init__(self, status, payload=None, text=""):
        self.status_code, self._payload, self.text = status, payload, text

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class Http:
    def __init__(self, resp=None, exc=None):
        self.resp, self.exc, self.calls = resp, exc, []

    def get(self, url, **kw):
        self.calls.append(("GET", url, kw))
        if self.exc:
            raise self.exc
        return self.resp

    post = get


@pytest.mark.parametrize("provider", ["deepseek", "claude", "openai", "routerai", "openrouter", "elevenlabs"])
@pytest.mark.parametrize("status,expected", [(200, "ok"), (401, "bad_key"), (403, "bad_key"), (402, "no_money"),
                                             (500, "unreachable")])
def test_check_maps_answers_to_words(provider, status, expected):
    result = pc.check_key(provider, "k", http=Http(Resp(status, {})))
    assert result.status == expected and result.detail


def test_a_timeout_is_unreachable_not_a_crash():
    result = pc.check_key("deepseek", "k", http=Http(exc=requests.Timeout()))
    assert result.status == "unreachable"


def test_the_key_never_lands_in_the_detail():
    result = pc.check_key("claude", "sk-ant-secret-value", http=Http(Resp(401, None, "invalid sk-ant-secret-value")))
    assert "secret-value" not in result.detail


def test_deepseek_balance():
    payload = {"is_available": True, "balance_infos": [{"currency": "USD", "total_balance": "12.50"}]}
    assert pc.balance("deepseek", "k", http=Http(Resp(200, payload))) == \
        {"provider": "deepseek", "available": True, "amount": 12.5, "unit": "$", "detail": ""}


def test_a_balance_that_is_not_json_says_no_data():
    got = pc.balance("openrouter", "k", http=Http(Resp(200, None, "<html>")))
    assert got["available"] is False and got["amount"] is None


def test_claude_and_openai_have_no_balance_api():
    assert pc.balance("claude", "k", http=Http(Resp(200, {})))["detail"] == "провайдер не сообщает баланс"


def test_routerai_balance_reads_the_same_field_as_the_run_meter():
    got = pc.balance("routerai", "k", http=Http(Resp(200, {"data": {"credits": 512.3}})))
    assert (got["available"], got["amount"], got["unit"]) == (True, 512.3, "₽")

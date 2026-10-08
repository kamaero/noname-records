"""Провайдер записи трат — по адресу, который студия сама задала, а не только по слову в адресе."""
from app.services import spend


def test_a_proxied_provider_is_recognised_by_its_configured_address(monkeypatch):
    monkeypatch.setattr(spend.settings, "deepseek_base_url", "https://llm-proxy.studio.local/ds/v1")
    monkeypatch.setattr(spend.settings, "claude_base_url", "https://llm-proxy.studio.local/cl/v1/")
    assert spend.provider_from_url("https://llm-proxy.studio.local/ds/v1") == "deepseek"
    assert spend.provider_from_url("https://llm-proxy.studio.local/cl/v1") == "claude"


def test_the_longest_configured_address_wins(monkeypatch):
    monkeypatch.setattr(spend.settings, "deepseek_base_url", "https://proxy.local/v1")
    monkeypatch.setattr(spend.settings, "routerai_base_url", "https://proxy.local/v1/router")
    assert spend.provider_from_url("https://proxy.local/v1/router") == "routerai"


def test_known_words_still_work_and_unknown_stays_other():
    assert spend.provider_from_url("https://api.deepseek.com/anthropic/v1") == "deepseek"
    assert spend.provider_from_url("https://example.org/v1") == "other"

"""Провайдер может повторить ключ в тексте ошибки — ключ не должен дойти до журнала, базы и экрана."""
import pytest

from app.pipeline import llm_client
from app.services import elevenlabs_client, provider_keys

KEY = "sk" + "-reflected-secret-" + "0123456789abcdef"  # склеен: в репозитории не похож на настоящий ключ


class Resp:
    def __init__(self, status, text):
        self.status_code, self.text, self.headers = status, text, {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def iter_lines(self, **kw):
        return iter(())

    def json(self):
        return {}


@pytest.fixture(autouse=True)
def site_key(monkeypatch):
    monkeypatch.setattr(provider_keys, "provider_key", lambda name: KEY if name == "openai" else "")
    monkeypatch.setattr(provider_keys, "_known_keys", lambda: {KEY})


def _no_fragment(text: str) -> bool:
    return not any(KEY[i:i + 5] in text for i in range(len(KEY) - 4))


def test_redact_hides_known_keys_and_token_lookalikes():
    out = provider_keys.redact(f"bad key {KEY} and Bearer sk-other-unknown-token-9999999999")
    assert _no_fragment(out) and "sk-other-unknown" not in out and "bad key" in out


@pytest.mark.parametrize("mode", ["openai", "anthropic"])
def test_an_echoed_key_never_reaches_the_llm_error(monkeypatch, mode):
    monkeypatch.setattr(llm_client.requests, "post", lambda *a, **kw: Resp(400, f'{{"error":"invalid key {KEY}"}}'))
    with pytest.raises(RuntimeError) as err:
        llm_client.call_chat("https://x", KEY, "m", "s", "u", mode=mode)
    assert _no_fragment(str(err.value))


def test_an_echoed_key_never_reaches_the_audio_error():
    def transport(url, headers, body, timeout):
        return 400, {}, f"bad request for {KEY}".encode()
    with pytest.raises(elevenlabs_client.ElevenLabsError) as err:
        elevenlabs_client.compose_music("calm", 60, api_key=KEY, transport=transport)
    assert _no_fragment(str(err.value))

"""Клиент ElevenLabs Music: тело запроса, классификация ошибок, song_id, ключ не течёт."""
import http.client
import json
import socket
import urllib.error

import pytest

from app.services import elevenlabs_client
from app.services.elevenlabs_client import (
    BadKey,
    ElevenLabsError,
    ElevenLabsInterrupted,
    ElevenLabsUnavailable,
    QuotaExhausted,
    compose_music,
)

SECRET_KEY = "sk-supersecret-do-not-leak"


def _fake_transport(status, headers=None, body=b"", capture=None):
    def transport(url, req_headers, req_body, timeout):
        if capture is not None:
            capture["url"] = url
            capture["headers"] = req_headers
            capture["body"] = req_body
            capture["timeout"] = timeout
        return status, headers or {}, body
    return transport


def test_request_body_and_headers_are_correct():
    capture = {}
    transport = _fake_transport(200, {"song-id": "abc123"}, b"MP3BYTES", capture=capture)

    audio, song_id = compose_music("calm strings", 240, api_key=SECRET_KEY, transport=transport)

    assert audio == b"MP3BYTES"
    assert song_id == "abc123"
    assert "output_format=mp3_44100_192" in capture["url"]
    assert capture["headers"]["xi-api-key"] == SECRET_KEY
    payload = json.loads(capture["body"])
    assert payload == {
        "prompt": "calm strings",
        "music_length_ms": 240000,
        "force_instrumental": True,
        "model_id": "music_v2",
    }


def test_song_id_header_lookup_is_case_insensitive_and_defaults_empty():
    transport = _fake_transport(200, {"X-Song-Id": "xyz"}, b"BYTES")
    _audio, song_id = compose_music("p", 200, api_key=SECRET_KEY, transport=transport)
    assert song_id == "xyz"

    transport_no_header = _fake_transport(200, {"content-type": "audio/mpeg"}, b"BYTES")
    _audio, song_id = compose_music("p", 200, api_key=SECRET_KEY, transport=transport_no_header)
    assert song_id == ""


@pytest.mark.parametrize("status", [402, 429])
def test_quota_statuses_raise_quota_exhausted(status):
    transport = _fake_transport(status, {}, b"quota exceeded")
    with pytest.raises(QuotaExhausted):
        compose_music("p", 200, api_key=SECRET_KEY, transport=transport)


def test_401_with_quota_wording_is_quota_exhausted():
    transport = _fake_transport(401, {}, b'{"detail": "quota exceeded for this key"}')
    with pytest.raises(QuotaExhausted):
        compose_music("p", 200, api_key=SECRET_KEY, transport=transport)


@pytest.mark.parametrize("status", [401, 403])
def test_401_403_without_quota_wording_is_a_bad_key(status):
    transport = _fake_transport(status, {}, b'{"detail": "invalid api key"}')
    with pytest.raises(BadKey) as excinfo:
        compose_music("p", 200, api_key=SECRET_KEY, transport=transport)
    assert not isinstance(excinfo.value, QuotaExhausted)
    assert excinfo.value.status == status
    assert SECRET_KEY not in str(excinfo.value)


def test_503_raises_unavailable():
    transport = _fake_transport(503, {}, b"service unavailable")
    with pytest.raises(ElevenLabsUnavailable):
        compose_music("p", 200, api_key=SECRET_KEY, transport=transport)


def test_an_empty_200_is_an_error_not_a_track():
    transport = _fake_transport(200, {"song-id": "s"}, b"")
    with pytest.raises(ElevenLabsError) as excinfo:
        compose_music("p", 200, api_key=SECRET_KEY, transport=transport)
    assert "пустой ответ" in str(excinfo.value)
    assert not isinstance(excinfo.value, (ElevenLabsUnavailable, ElevenLabsInterrupted))


def test_400_raises_plain_error_with_status():
    transport = _fake_transport(400, {}, b"bad prompt")
    with pytest.raises(ElevenLabsError) as excinfo:
        compose_music("p", 200, api_key=SECRET_KEY, transport=transport)
    assert excinfo.value.status == 400


@pytest.mark.parametrize("exc", [ConnectionRefusedError("refused"), socket.gaierror(-2, "Name or service not known")])
def test_a_failure_before_sending_is_unavailable(exc):
    def transport(url, headers, body, timeout):
        raise exc

    with pytest.raises(ElevenLabsUnavailable) as excinfo:
        compose_music("p", 200, api_key=SECRET_KEY, transport=transport)
    assert excinfo.value.sent is False


@pytest.mark.parametrize("exc", [TimeoutError("timed out"), ConnectionResetError("reset"),
                                 http.client.IncompleteRead(b"ID3", 1000), ConnectionError("boom")])
def test_a_failure_after_sending_is_interrupted_not_unavailable(exc):
    """Запрос мог дойти до ElevenLabs и быть оплачен — повторять нельзя."""
    def transport(url, headers, body, timeout):
        raise exc

    with pytest.raises(ElevenLabsInterrupted) as excinfo:
        compose_music("p", 200, api_key=SECRET_KEY, transport=transport)
    assert not isinstance(excinfo.value, ElevenLabsUnavailable)


class _Response:
    def __init__(self, status=200, body=b"MP3", read_error=None):
        self.status = status
        self.headers = http.client.HTTPMessage()
        self._body = body
        self._error = read_error

    def read(self):
        if self._error is not None:
            raise self._error
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _urlopen(outcome):
    def urlopen(request, timeout):
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome
    return urlopen


@pytest.mark.elevenlabs_transport
class TestUrllibTransport:
    """Настоящий транспорт по умолчанию — с подменённым `urlopen`: сеть не трогается."""

    def _send(self, monkeypatch, outcome):
        monkeypatch.setattr("urllib.request.urlopen", _urlopen(outcome))
        return compose_music("p", 200, api_key=SECRET_KEY)

    @pytest.mark.parametrize("reason", [socket.gaierror(-2, "dns"), ConnectionRefusedError("refused")])
    def test_dns_and_refused_are_unavailable_before_sending(self, monkeypatch, reason):
        with pytest.raises(ElevenLabsUnavailable) as excinfo:
            self._send(monkeypatch, urllib.error.URLError(reason))
        assert excinfo.value.sent is False

    def test_a_timeout_waiting_for_the_answer_is_interrupted(self, monkeypatch):
        with pytest.raises(ElevenLabsInterrupted):
            self._send(monkeypatch, urllib.error.URLError(TimeoutError("timed out")))

    def test_a_bare_socket_timeout_is_interrupted(self, monkeypatch):
        with pytest.raises(ElevenLabsInterrupted):
            self._send(monkeypatch, TimeoutError("timed out"))

    def test_a_reset_while_reading_the_body_is_interrupted(self, monkeypatch):
        with pytest.raises(ElevenLabsInterrupted):
            self._send(monkeypatch, _Response(read_error=ConnectionResetError("reset")))

    def test_a_short_body_is_interrupted(self, monkeypatch):
        with pytest.raises(ElevenLabsInterrupted):
            self._send(monkeypatch, _Response(read_error=http.client.IncompleteRead(b"ID3", 999)))

    def test_a_good_answer_is_returned(self, monkeypatch):
        audio, song_id = self._send(monkeypatch, _Response(body=b"MP3BYTES"))
        assert (audio, song_id) == (b"MP3BYTES", "")

    def test_an_http_error_is_classified_by_status(self, monkeypatch):
        error = urllib.error.HTTPError(elevenlabs_client.MUSIC_URL, 503, "busy", http.client.HTTPMessage(), None)
        with pytest.raises(ElevenLabsUnavailable) as excinfo:
            self._send(monkeypatch, error)
        assert excinfo.value.sent is True


def test_the_default_transport_is_blocked_in_tests():
    """Сторож в conftest: без явного согласия теста настоящий транспорт не вызывается."""
    with pytest.raises(pytest.fail.Exception):
        compose_music("p", 200, api_key=SECRET_KEY)


def test_api_key_never_leaks_into_exception_text():
    transport = _fake_transport(400, {}, b"bad request")
    with pytest.raises(ElevenLabsError) as excinfo:
        compose_music("p", 200, api_key=SECRET_KEY, transport=transport)
    assert SECRET_KEY not in str(excinfo.value)
    assert SECRET_KEY not in repr(excinfo.value)


def test_api_key_never_leaks_on_quota_error():
    transport = _fake_transport(402, {}, b"quota exceeded")
    with pytest.raises(QuotaExhausted) as excinfo:
        compose_music("p", 200, api_key=SECRET_KEY, transport=transport)
    assert SECRET_KEY not in str(excinfo.value)


def test_missing_api_key_raises_without_calling_transport():
    called = {"v": False}

    def transport(url, headers, body, timeout):
        called["v"] = True
        return 200, {}, b""

    with pytest.raises(ElevenLabsError):
        compose_music("p", 200, api_key="", transport=transport)
    assert called["v"] is False


def test_the_audio_model_comes_from_its_step(monkeypatch):
    from app.services import step_models
    monkeypatch.setattr(step_models, "step_model", lambda key: ("elevenlabs", "music_v3"))
    capture = {}
    compose_music("calm", 240, api_key=SECRET_KEY,
                  transport=_fake_transport(200, {"song-id": "x"}, b"MP3", capture=capture))
    assert json.loads(capture["body"])["model_id"] == "music_v3"

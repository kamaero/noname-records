"""Отправить запись на распознавание — и не упереться в предел на размер файла.

У whisper-1 файл не длиннее 25 мегабайт, а дубли у нас по восемьдесят: пятнадцать
минут несжатого WAV. Резать файл на куски — значит резать по живому слову и потом
сшивать тайминги; сжать его в моно 16 кГц дешевле и надёжнее. Модель всё равно
понижает частоту до шестнадцати килогерц, так что мы ничего не теряем — те же
пятнадцать минут занимают около четырёх мегабайт вместо восьмидесяти.

Тайминги — то, ради чего всё: без них не расставить ни маркеры в Audition, ни клипы в
сессии, поэтому просим `verbose_json` и пословную разбивку.
"""
import shutil
import struct
import wave

import pytest

from app.services.asr_transcribe import (
    AsrError,
    MAX_UPLOAD_BYTES,
    compress_for_asr,
    parse_transcription,
    transcribe_file,
)

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg не установлен")


def _wav(path, *, seconds=3.0, rate=44100, channels=2):
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(struct.pack("<h", 0) * int(rate * seconds) * channels)
    return str(path)


class TestMakingItFit:
    @needs_ffmpeg
    def test_the_file_gets_much_smaller(self, tmp_path):
        source = _wav(tmp_path / "take.wav", seconds=10)
        target = compress_for_asr(source, str(tmp_path / "take.mp3"))

        import os
        assert os.path.getsize(target) < os.path.getsize(source) / 5

    @needs_ffmpeg
    def test_it_comes_out_mono_at_the_rate_the_model_listens_at(self, tmp_path):
        from app.services.audio_probe import probe_audio_file

        target = compress_for_asr(_wav(tmp_path / "take.wav"), str(tmp_path / "take.mp3"))
        info = probe_audio_file(target)

        assert info["channels"] == 1
        assert info["sample_rate"] == 16000

    @needs_ffmpeg
    def test_a_file_ffmpeg_cannot_read_says_so(self, tmp_path):
        broken = tmp_path / "broken.wav"
        broken.write_bytes(b"not audio at all")

        with pytest.raises(AsrError, match="compress_failed"):
            compress_for_asr(str(broken), str(tmp_path / "out.mp3"))


class TestReadingTheAnswer:
    def test_segments_and_words_come_through(self):
        result = parse_transcription({
            "text": "Я не пойду туда",
            "segments": [{"start": 0.0, "end": 2.5, "text": " Я не пойду туда"}],
            "words": [{"word": "Я", "start": 0.1, "end": 0.2}, {"word": "не", "start": 0.3, "end": 0.5}],
        })

        assert result["text"] == "Я не пойду туда"
        assert result["segments"][0]["text"] == "Я не пойду туда"
        assert result["segments"][0]["words"] == [
            {"word": "Я", "start": 0.1, "end": 0.2},
            {"word": "не", "start": 0.3, "end": 0.5},
        ]

    def test_words_are_hung_on_the_segment_they_fall_into(self):
        result = parse_transcription({
            "text": "раз два",
            "segments": [{"start": 0.0, "end": 1.0, "text": "раз"}, {"start": 1.0, "end": 2.0, "text": "два"}],
            "words": [{"word": "раз", "start": 0.2, "end": 0.6}, {"word": "два", "start": 1.2, "end": 1.6}],
        })

        assert [w["word"] for w in result["segments"][0]["words"]] == ["раз"]
        assert [w["word"] for w in result["segments"][1]["words"]] == ["два"]

    def test_an_answer_without_word_times_is_still_an_answer(self):
        """Пословных отметок может не быть; сегментных достаточно, чтобы выровнять."""
        result = parse_transcription({"text": "раз два", "segments": [{"start": 0.0, "end": 2.0, "text": "раз два"}]})

        assert result["segments"][0]["words"] == []

    def test_an_empty_answer_is_not_a_crash(self):
        assert parse_transcription({}) == {"text": "", "segments": []}


class TestSendingIt:
    def test_it_asks_for_the_timings_we_need(self, tmp_path, monkeypatch):
        sent = {}

        class _Response:
            status_code = 200

            @staticmethod
            def json():
                return {"text": "ок", "segments": []}

        def fake_post(url, headers=None, files=None, data=None, timeout=None):
            sent.update({"url": url, "data": data, "headers": headers})
            return _Response()

        monkeypatch.setattr("app.services.asr_transcribe.requests.post", fake_post)
        path = tmp_path / "a.mp3"
        path.write_bytes(b"x" * 10)

        transcribe_file(str(path), api_key="k", model="whisper-1", language="ru")

        assert sent["url"].endswith("/audio/transcriptions")
        assert sent["data"]["response_format"] == "verbose_json"
        assert "word" in sent["data"]["timestamp_granularities[]"]
        assert sent["headers"]["Authorization"] == "Bearer k"

    def test_a_file_too_large_even_compressed_is_refused_before_the_call(self, tmp_path, monkeypatch):
        def explode(*args, **kwargs):
            raise AssertionError("до сети дойти не должно")

        monkeypatch.setattr("app.services.asr_transcribe.requests.post", explode)
        big = tmp_path / "big.mp3"
        big.write_bytes(b"x" * (MAX_UPLOAD_BYTES + 1))

        with pytest.raises(AsrError, match="file_too_large"):
            transcribe_file(str(big), api_key="k", model="whisper-1", language="ru")

    def test_without_a_key_we_do_not_pretend(self, tmp_path):
        path = tmp_path / "a.mp3"
        path.write_bytes(b"x")

        with pytest.raises(AsrError, match="no_api_key"):
            transcribe_file(str(path), api_key="", model="whisper-1", language="ru")

    def test_a_refusal_from_the_service_is_reported(self, tmp_path, monkeypatch):
        class _Response:
            status_code = 429
            text = "rate limited"

        monkeypatch.setattr("app.services.asr_transcribe.requests.post", lambda *a, **k: _Response())
        path = tmp_path / "a.mp3"
        path.write_bytes(b"x")

        with pytest.raises(AsrError, match="asr_http_429"):
            transcribe_file(str(path), api_key="k", model="whisper-1", language="ru")


class TestWhereItIsSent:
    """Эндпоинт настраивается: RouterAI совместим с OpenAI по формату, но живёт по
    другому адресу и берёт свои модели. Один код, три поставщика."""

    def _ok(self):
        class _R:
            status_code = 200

            @staticmethod
            def json():
                return {"text": "ок", "segments": []}

        return _R()

    def test_by_default_it_goes_to_openai(self, tmp_path, monkeypatch):
        sent = {}
        monkeypatch.setattr("app.services.asr_transcribe.requests.post",
                            lambda url, **kw: sent.update({"url": url}) or self._ok())
        path = tmp_path / "a.mp3"
        path.write_bytes(b"x")

        transcribe_file(str(path), api_key="k", model="whisper-1")

        assert sent["url"].startswith("https://api.openai.com/")

    def test_another_base_takes_it_elsewhere(self, tmp_path, monkeypatch):
        sent = {}
        monkeypatch.setattr("app.services.asr_transcribe.requests.post",
                            lambda url, **kw: sent.update({"url": url}) or self._ok())
        path = tmp_path / "a.mp3"
        path.write_bytes(b"x")

        transcribe_file(str(path), api_key="k", model="microsoft/mai-transcribe-2",
                        base_url="https://routerai.ru/api/v1")

        assert sent["url"] == "https://routerai.ru/api/v1/audio/transcriptions"

    def test_a_trailing_slash_does_not_double_up(self, tmp_path, monkeypatch):
        sent = {}
        monkeypatch.setattr("app.services.asr_transcribe.requests.post",
                            lambda url, **kw: sent.update({"url": url}) or self._ok())
        path = tmp_path / "a.mp3"
        path.write_bytes(b"x")

        transcribe_file(str(path), api_key="k", model="m", base_url="https://routerai.ru/api/v1/")

        assert "v1//" not in sent["url"]

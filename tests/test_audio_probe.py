"""Что за файл нам прислали — спрашиваем у самого файла, а не у его имени.

До сих пор о загруженном мы знали только размер в байтах. Размер не отличает
пятнадцать минут речи от пятнадцати минут тишины, не говорит, что диктор писал в
стерео на 22 кГц, и не даёт длительности — а она нужна и смете, и описи главы, и
любой будущей сборке в монтажке.

`ffprobe` отвечает на это за треть секунды даже на файле в восемьдесят мегабайт, так
что спрашивать можно прямо при загрузке. Ответ — предупреждение, а не запрет: студия
решает сама, что делать с файлом, записанным не так.
"""
import shutil
import struct
import wave

import pytest

from app.services.audio_probe import probe_audio_file, probe_warnings

pytestmark = pytest.mark.skipif(shutil.which("ffprobe") is None, reason="ffprobe не установлен")


def _wav(path, *, seconds=1.0, rate=44100, channels=1, width=2):
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(width)
        handle.setframerate(rate)
        frames = int(rate * seconds)
        handle.writeframes(struct.pack("<h", 0) * frames * channels)
    return str(path)


class TestWhatTheFileSays:
    def test_it_tells_how_long_the_take_is(self, tmp_path):
        info = probe_audio_file(_wav(tmp_path / "a.wav", seconds=2.5))

        assert info["duration_seconds"] == pytest.approx(2.5, abs=0.05)

    def test_and_how_it_was_recorded(self, tmp_path):
        info = probe_audio_file(_wav(tmp_path / "a.wav", rate=48000, channels=1))

        assert info["sample_rate"] == 48000
        assert info["channels"] == 1
        assert info["codec"].startswith("pcm")

    def test_a_file_that_is_not_audio_at_all(self, tmp_path):
        broken = tmp_path / "broken.wav"
        broken.write_bytes(b"not audio")

        assert probe_audio_file(str(broken)) == {}

    def test_a_file_that_is_not_there(self, tmp_path):
        assert probe_audio_file(str(tmp_path / "missing.wav")) == {}


class TestWhatIsWorthSaying:
    def test_a_proper_take_says_nothing(self, tmp_path):
        assert probe_warnings(probe_audio_file(_wav(tmp_path / "a.wav", seconds=30))) == []

    def test_a_rate_below_the_studio_standard(self, tmp_path):
        info = probe_audio_file(_wav(tmp_path / "a.wav", seconds=30, rate=22050))

        assert "low_sample_rate" in probe_warnings(info)

    def test_stereo_for_a_single_voice(self, tmp_path):
        """Два канала одного голоса — вдвое больше байтов и ни грамма пользы."""
        info = probe_audio_file(_wav(tmp_path / "a.wav", seconds=30, channels=2))

        assert "stereo" in probe_warnings(info)

    def test_a_file_with_almost_nothing_in_it(self, tmp_path):
        info = probe_audio_file(_wav(tmp_path / "a.wav", seconds=0.4))

        assert "too_short" in probe_warnings(info)

    def test_nothing_is_said_about_a_file_we_could_not_read(self):
        assert probe_warnings({}) == []

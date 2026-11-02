"""Название .wav не должно превращать MP3 в дубль и запускать платное ASR."""
import subprocess

import pytest

from tests.test_bot_takes_process import Run, _verify_queue, db, lines, whole
from app.models import AudioFile, BotUpload
from app.services.take_format import REQUIRED


def test_queued_mp3_is_rejected_before_download(db, tmp_path):
    db.get(BotUpload, "up1").file_name = "Yumkuba_vse.mp3"
    db.commit()
    run = Run(db, tmp_path, tmp_path / "does-not-exist.mp3")
    assert run() == "failed"
    assert run.calls == 0 and run.dictor == [("41", REQUIRED)]
    assert db.query(AudioFile).count() == 0 and db.get(BotUpload, "up1").state == "failed"


@pytest.mark.parametrize("container", ["mp3", "wav"])
def test_mp3_renamed_to_wav_or_wrapped_in_wav_is_rejected(db, tmp_path, whole, container):
    disguised = tmp_path / "renamed.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(whole), "-t", "0.1",
                    "-c:a", "libmp3lame", "-f", container, str(disguised)], check=True, timeout=20)
    run = Run(db, tmp_path, disguised)
    assert run() == "failed"
    assert run.calls == 0 and run.dictor == [("41", REQUIRED)]
    row = db.get(BotUpload, "up1")
    assert row.state == "failed" and row.stored_key == ""
    assert db.query(AudioFile).count() == 0


def test_real_flac_is_processed_as_takes(db, tmp_path, whole):
    source = tmp_path / "Yumkuba_vse.flac"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(whole), "-c:a", "flac", str(source)],
                   check=True, timeout=20)
    db.get(BotUpload, "up1").file_name = source.name
    db.commit()
    run = Run(db, tmp_path, source)
    assert run() == "done" and run.calls == 1
    takes = db.query(AudioFile).all()
    assert len(takes) == 6 and all(item.codec == "flac" and item.kind == "take" for item in takes)

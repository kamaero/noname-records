"""Длинная запись распознаётся частями; упавший прогон не платит за готовое дважды."""
import json
import wave

import pytest

from app.services import take_split


def _wav(path, pieces, rate=8000):
    """pieces — [(секунды, звук?)]: тон или тишина."""
    frames = bytearray()
    for seconds, loud in pieces:
        for i in range(int(seconds * rate)):
            v = (12000 if (i // 20) % 2 else -12000) if loud else 0
            frames += int(v).to_bytes(2, "little", signed=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))


def _fake(calls, fail_on=None):
    def transcribe_part(path):
        calls.append(path)
        n = len(calls)
        if fail_on == n:
            raise RuntimeError("азур упал")
        return {"text": f"часть{n}", "segments": [
            {"start": 0.1, "end": 0.4, "text": f"часть{n}",
             "words": [{"word": f"часть{n}", "start": 0.1, "end": 0.4}]},
        ]}
    return transcribe_part


def _long(tmp_path):
    src = tmp_path / "a.wav"
    _wav(src, [(0.9, True), (0.2, False), (0.9, True), (0.2, False), (0.3, True)])
    return src


def test_short_file_is_one_call(tmp_path):
    src = tmp_path / "a.wav"
    _wav(src, [(1, True)])
    calls = []
    out = take_split.transcribe_whole(
        str(src), transcribe_part=_fake(calls), workdir=str(tmp_path / "w"), duration=1.0)
    assert calls == [str(src)]
    assert out["text"] == "часть1"


def test_long_file_parts_are_shifted(tmp_path, monkeypatch):
    monkeypatch.setattr(take_split, "PART_SECONDS", 1)
    src = _long(tmp_path)
    calls = []
    out = take_split.transcribe_whole(
        str(src), transcribe_part=_fake(calls), workdir=str(tmp_path / "w"), duration=2.5)
    assert len(calls) == 3
    assert out["text"] == "часть1 часть2 часть3"
    starts = [s["words"][0]["start"] for s in out["segments"]]
    assert starts[0] == pytest.approx(0.1)
    assert 0.9 < starts[1] < 1.4 and starts[2] > starts[1] + 0.5
    assert out["segments"][1]["start"] == pytest.approx(starts[1])
    assert out["segments"][1]["end"] == pytest.approx(starts[1] + 0.3)


def test_retry_does_not_pay_twice(tmp_path, monkeypatch):
    monkeypatch.setattr(take_split, "PART_SECONDS", 1)
    src = _long(tmp_path)
    work = str(tmp_path / "w")
    calls = []
    with pytest.raises(RuntimeError):
        take_split.transcribe_whole(
            str(src), transcribe_part=_fake(calls, fail_on=2), workdir=work, duration=2.5)
    assert len(calls) == 2
    calls2 = []
    out = take_split.transcribe_whole(
        str(src), transcribe_part=_fake(calls2), workdir=work, duration=2.5)
    assert len(calls2) == 2  # вторая и третья; первая взята из сохранённого
    assert out["text"].startswith("часть1 ")


def test_saved_part_with_other_boundaries_is_not_reused(tmp_path, monkeypatch):
    monkeypatch.setattr(take_split, "PART_SECONDS", 1)
    src = _long(tmp_path)
    work = tmp_path / "w"
    work.mkdir()
    (work / "part-0.json").write_text(json.dumps(
        {"start": 0.0, "end": 7.0, "result": {"text": "старое", "segments": []}}))
    calls = []
    take_split.transcribe_whole(
        str(src), transcribe_part=_fake(calls), workdir=str(work), duration=2.5)
    assert len(calls) == 3

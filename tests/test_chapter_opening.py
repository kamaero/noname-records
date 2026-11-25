"""Начало рабочей сессии и её звуковые маркеры в сэмплах Audition."""
from types import SimpleNamespace

import pytest
# Разбираем только XML сборщика через defusedxml.
from defusedxml import ElementTree as ET

from app.services import chapter_delivery as delivery
from app.services.audition_session import SessionClip
from app.services.chapter_opening import place_opening
from app.services.chapter_titles import TITLE_ROLE


def test_opening_keeps_title_attempts_and_moves_body_and_gaps_together():
    replicas = [
        {"role": TITLE_ROLE, "segment_id": "title", "text": "Глава пятая"},
        {"role": "Рассказчик", "segment_id": "body", "text": "Ветер стих."},
    ]
    title = SessionClip("название", "title.wav", 0, source_in=.5, source_out=2.5, segment_id="title")
    retry = SessionClip("подход", "title.wav", 2.35, source_in=4, source_out=5)
    body = SessionClip("рассказ", "body.wav", 3.7, source_in=12, source_out=14, segment_id="body")
    clips = [(TITLE_ROLE, title), (TITLE_ROLE, retry), ("Рассказчик", body)]
    starts = {"title": 0., "body": 3.7}
    gaps = [{"role": "Голос", "segment_id": "gap", "start": 6.05, "duration": 1.}]

    body_start = place_opening(replicas, clips, starts, gaps, chars_per_second=15, gap_seconds=.35)

    assert title.start == 10
    assert retry.start == pytest.approx(12.35)
    assert body_start == body.start == pytest.approx(retry.start + retry.length + 2.5)
    assert (body.source_in, body.source_out) == (12, 14)
    assert starts == {"title": 10., "body": body.start}
    assert gaps[0]["start"] == pytest.approx(body.start + 2.35)


@pytest.mark.parametrize("rate", [44100, 48000])
def test_working_session_serializes_opening_fade_and_aligned_voices(monkeypatch, rate):
    title = SessionClip("название", "title.wav", 10, source_out=2, segment_id="title")
    voice = SessionClip("текст", "body.wav", 14.5, source_out=10, segment_id="body")
    roles = [{"role": TITLE_ROLE}, {"role": "Рассказчик"}, {"role": "Другой"}]
    raw = SimpleNamespace(id="raw", canonical_filename="raw.wav", stored_key="raw.wav",
                          location="local", duration_seconds=30, asr_binding_json="")
    layout = _layout(({"roles": roles}, None, {"Другой": [raw]},
                      [(TITLE_ROLE, title), ("Рассказчик", voice)],
                      {"title": 10., "body": 14.5}, 24.5))
    monkeypatch.setattr(delivery, "_script_layout", lambda *a, **k: layout)
    monkeypatch.setattr(delivery, "_chapter_heading", lambda status: "Глава")
    monkeypatch.setattr(delivery, "_spans_on_timeline",
                        lambda *a: {"first": (10., 10.), "next": (20., 4.5)})
    monkeypatch.setattr(delivery, "session_markers", lambda *a: ([], 0))
    monkeypatch.setattr(delivery.audio_storage, "resolve_path", lambda key, **k: key)
    _ambient(monkeypatch)

    xml, _ = delivery.build_chapter_session_with_stats(
        object(), "chapter", sample_rate=rate, relative=True, working_layout=True, include_audit=False)
    tree = ET.fromstring(xml)
    tracks = {t.findtext("trackParameters/name"): t for t in tree.findall(".//audioTrack")}
    for name in delivery.AMBIENT_TRACKS:
        first, following = tracks[name].findall("audioClip")
        assert int(first.get("startPoint")) == 3 * rate
        assert int(first.find("fadeIn").get("endPoint")) == 7 * rate
        assert int(first.get("endPoint")) == 20 * rate
        assert float(first.find('component[@id="clipGain"]/parameter').get("parameterValue")) == pytest.approx(10 ** (-12/20))
        assert int(following.get("startPoint")) == 20 * rate
        assert following.find("fadeIn").get("endPoint") == "0"
    assert int(tracks[_title_name].find("audioClip").get("startPoint")) == 10 * rate
    assert int(tracks["Рассказчик"].find("audioClip").get("startPoint")) == 14.5 * rate
    raw_track = tracks["Другой"]
    assert int(raw_track.find("audioClip").get("startPoint")) == 14.5 * rate
    assert raw_track.find('trackAudioParameters/component[@id="trackMute"]') is not None

_title_name = TITLE_ROLE


def _layout(values):
    return delivery._ChapterLayout(values, 14.5)


def _ambient(monkeypatch):
    from app.services import ambient_engine, ambient_pairs, role_identity

    items = {marker: {variant: SimpleNamespace(id=marker+variant,
                    canonical_filename=f"{marker}-{variant}.wav", stored_key=f"{marker}-{variant}.wav",
                    location="local", duration_seconds=30) for variant in ("A", "B")}
             for marker in ("first", "next")}
    monkeypatch.setattr(role_identity, "chapter_roles", lambda *a: SimpleNamespace(
        is_narrator=lambda name: name == "Рассказчик"))
    monkeypatch.setattr(ambient_pairs, "current_pairs", lambda *a: items)
    monkeypatch.setattr(ambient_pairs, "schema_ready", lambda *a: True)

    class Rows:
        def filter(self, *args):
            return self

        def order_by(self, *args):
            return self

        def all(self):
            return [(SimpleNamespace(marker_id=marker, variant=variant), item)
                    for marker, pair in items.items() for variant, item in pair.items()]

    monkeypatch.setattr(ambient_engine, "done_tracks", lambda *a: Rows())

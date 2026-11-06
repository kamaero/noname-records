"""Таймкод исходника не выдаётся за позицию проблемного слова в монтаже."""
import json
import uuid
# Парсим только XML, созданный приложением из синтетических тестовых данных.
from xml.etree import ElementTree as ET

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import AsrAudit, AsrJob, AudioFile, ScriptBook, ScriptChapter
from app.services import chapter_delivery
from app.services.audition_session import SessionClip, SessionTrack
from app.services.narrator_audit_markers import narrator_audit_session_markers
from app.time_utils import utcnow_naive
from app.v2.models import V2Attribution, V2Segment


@pytest.fixture
def studio(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        book = ScriptBook(title="Проба", source_filename="x.txt", source_format="txt")
        db.add(book)
        db.flush()
        chapter = ScriptChapter(book_id=book.id, chapter_index=1, chapter_title="Глава 1")
        db.add(chapter)
        db.flush()
        for ordinal, role, text in [(0, "Герой", "Он ответил мне."),
                                    (1, "Рассказчик", "Впереди сиял красный огонь.")]:
            segment = V2Segment(id=f"{chapter.id}:{ordinal:05d}", chapter_id=chapter.id,
                                book_id=book.id, ordinal=ordinal, kind="paragraph", text=text)
            db.add(segment)
            db.flush()
            db.add(V2Attribution(id=str(uuid.uuid4()), segment_id=segment.id, version=1, speaker=role,
                                 span_start=0, span_end=len(text), source="llm"))
        take = AudioFile(book_code="П", chapter="Глава 1", role="Рассказчик", kind="take",
                         mime_type="audio/wav", size_bytes=100,
                         stored_key="narrator.wav", canonical_filename="narrator.wav",
                         original_filename="narrator.wav", duration_seconds=200)
        hero = AudioFile(book_code="П", chapter="Глава 1", role="Герой", kind="take",
                         mime_type="audio/wav", size_bytes=100,
                         stored_key="hero.wav", canonical_filename="hero.wav",
                         original_filename="hero.wav", duration_seconds=20)
        db.add_all([take, hero])
        take.test_chapter_id = chapter.id
        db.flush()
        for audio, start, end in [(hero, 10, 14), (take, 100, 110)]:
            db.add(AsrJob(audio_file_id=audio.id, chapter_id=chapter.id, status="done",
                         alignment_json=json.dumps({"lines": [{"matched": True, "start": start, "end": end}]})))
        flag = {"index": 0, "segment_id": f"{chapter.id}:00001", "text": "Впереди сиял красный огонь.",
                "status": "partial", "missing_words": ["красный"], "confirmed": True,
                "anchor": {"start": 100, "end": 110},
                "word_anchors": [{"word": "красный", "start": 104, "end": 104.5,
                                  "approximate": True, "source_audio_file_id": take.id}]}
        audit = AsrAudit(chapter_id=chapter.id, audio_file_id=take.id, status="done",
                         findings_json=json.dumps({"flags": [flag]}))
        db.add(audit)
        db.flush()
        from app.services import asr_audit_lifecycle
        # Актуальность сборки проверяет lifecycle отдельно; здесь проверяется
        # преобразование уже разрешённого результата в настоящий XML с клипами.
        monkeypatch.setattr(asr_audit_lifecycle, "current_audit", lambda _db, _cid: audit, raising=False)
        yield db, chapter, take, audit, flag


def _set_flag(db, audit, flag):
    audit.findings_json = json.dumps({"flags": [flag]})
    db.flush()


def _tracks(take):
    return [SessionTrack(name="Рассказчик", clips=[
        SessionClip(name="реплика", path="narrator.wav", audio_file_id=take.id,
                        segment_id=f"{take.test_chapter_id}:00001", role_index=0, role="Рассказчик",
                    start=20, source_in=100, source_out=110),
    ])]


def test_real_session_maps_word_anchor_through_cut_and_script_order(studio):
    db, chapter, take, audit, flag = studio
    layout = chapter_delivery._script_layout(db, chapter.id)
    narrator_clip = next(clip for role, clip in layout[3] if role == "Рассказчик")
    xml, skipped = chapter_delivery.build_chapter_session_with_stats(db, chapter.id, sample_rate=1000)
    root = ET.fromstring(xml)
    metadata = ET.fromstring(root.find(".//xmpMetadata").text)
    ns = {"dm": "http://ns.adobe.com/xmp/1.0/DynamicMedia/"}
    expected = round((narrator_clip.start + 104 - narrator_clip.source_in) * 1000)
    assert int(metadata.find(".//dm:startTime", ns).text) == expected
    assert expected != 104000
    assert "красный" in xml and "приблизительно, у соседнего слова" in xml
    assert skipped == 0
    without = chapter_delivery.build_chapter_session(db, chapter.id, include_audit=False)
    assert "РАССКАЗЧИК · пропуск" not in without
    again = chapter_delivery.build_chapter_session(db, chapter.id, sample_rate=1000)
    assert chapter_delivery.session_fingerprint(xml) == chapter_delivery.session_fingerprint(again)


@pytest.mark.parametrize("change", ["unconfirmed", "old_source", "script_changed", "outside_cut", "no_anchor", "nan"])
def test_unsafe_findings_do_not_become_misleading_markers(studio, change):
    db, chapter, take, audit, flag = studio
    if change == "unconfirmed":
        flag["confirmed"] = False
    elif change == "old_source":
        audit.audio_file_id = "other-file"
        flag["word_anchors"][0]["source_audio_file_id"] = "other-file"
    elif change == "script_changed":
        flag["text"] = "Сценарий раньше был другим."
    elif change == "outside_cut":
        flag["word_anchors"][0].update(start=90, end=91)
    elif change == "no_anchor":
        flag.update(anchor=None, word_anchors=[])
    else:
        flag["word_anchors"][0]["start"] = float("nan")
    _set_flag(db, audit, flag)
    markers, skipped = narrator_audit_session_markers(db, chapter.id, _tracks(take))
    assert markers == []
    assert skipped == (0 if change == "unconfirmed" else 1)


def test_each_finding_uses_its_own_source_file_and_cut(studio):
    db, chapter, take, audit, flag = studio
    flag["audio_file_id"] = "fix-source"
    flag["word_anchors"] = []
    flag["anchor"] = {"start": 104, "end": 104.5, "approximate": True}
    _set_flag(db, audit, flag)
    tracks = _tracks(take)
    tracks[0].clips.append(SessionClip(name="исправление", path="fix.wav", audio_file_id="fix-source",
                                      segment_id=flag["segment_id"], role_index=0,
                                      start=70, source_in=100, source_out=110))
    markers, skipped = narrator_audit_session_markers(db, chapter.id, tracks)
    assert [marker.start for marker in markers] == [74]
    assert skipped == 0


def test_word_anchors_can_point_to_different_files_within_one_finding(studio):
    db, chapter, take, audit, flag = studio
    flag["word_anchors"][0]["source_audio_file_id"] = take.id
    flag["word_anchors"].append({"word": "огонь", "start": 4, "end": 4.5,
                                  "approximate": True, "source_audio_file_id": "fix-source"})
    _set_flag(db, audit, flag)
    tracks = _tracks(take)
    tracks[0].clips.append(SessionClip(name="исправление", path="fix.wav", audio_file_id="fix-source",
                                      segment_id=flag["segment_id"], role_index=0,
                                      start=70, source_in=0, source_out=10))
    markers, skipped = narrator_audit_session_markers(db, chapter.id, tracks)
    assert [marker.start for marker in markers] == [24, 74]
    assert skipped == 0


def test_unmapped_synthetic_time_cannot_accidentally_match_a_source_cut(studio):
    db, chapter, take, audit, flag = studio
    flag["source_windows"] = [{"source_audio_file_id": take.id}]
    flag["word_anchors"][0].pop("source_audio_file_id")
    _set_flag(db, audit, flag)
    assert narrator_audit_session_markers(db, chapter.id, _tracks(take)) == ([], 1)


def test_line_window_fallback_is_always_approximate(studio):
    db, chapter, take, audit, flag = studio
    flag["word_anchors"] = []
    flag["anchor"] = {"start": 102, "end": 105, "approximate": False}
    _set_flag(db, audit, flag)
    markers, skipped = narrator_audit_session_markers(db, chapter.id, _tracks(take))
    assert markers[0].start == 22 and markers[0].duration == 0
    assert "приблизительно" in markers[0].name
    assert skipped == 0


def test_exact_word_anchor_preserves_duration(studio):
    db, chapter, take, audit, flag = studio
    flag["word_anchors"][0]["approximate"] = False
    _set_flag(db, audit, flag)
    markers, _ = narrator_audit_session_markers(db, chapter.id, _tracks(take))
    assert markers[0].start == 24 and markers[0].duration == .5
    assert "точное место" in markers[0].name


def test_same_source_cut_can_be_placed_more_than_once(studio):
    db, chapter, take, audit, flag = studio
    tracks = _tracks(take)
    tracks[0].clips.append(SessionClip(name="ещё подход", path="narrator.wav", audio_file_id=take.id,
                                      segment_id=flag["segment_id"], role_index=0,
                                      start=40, source_in=100, source_out=110))
    markers, skipped = narrator_audit_session_markers(db, chapter.id, tracks)
    assert [marker.start for marker in markers] == [24, 44]
    assert len({marker.guid for marker in markers}) == 2
    assert skipped == 0


@pytest.mark.parametrize("other", ["segment", "role_index", "role"])
def test_overlapping_neighbor_cannot_receive_a_marker_for_another_replica(studio, other):
    db, chapter, take, audit, flag = studio
    tracks = _tracks(take)
    neighbor = SessionClip(name="соседняя реплика", path="narrator.wav", audio_file_id=take.id,
                           segment_id=flag["segment_id"], role_index=0, role="Рассказчик",
                           start=40, source_in=103, source_out=111)
    if other == "segment":
        neighbor.segment_id = f"{chapter.id}:00002"
    elif other == "role_index":
        neighbor.role_index = 1
    else:
        neighbor.role = "Автор"
    tracks[0].clips.append(neighbor)
    markers, skipped = narrator_audit_session_markers(db, chapter.id, tracks)
    assert [marker.start for marker in markers] == [24]
    assert skipped == 0


@pytest.mark.parametrize("field,value", [("role", "Автор"), ("role_index", 1), ("role_index", None)])
def test_explicit_finding_address_must_match_the_current_script(studio, field, value):
    db, chapter, take, audit, flag = studio
    flag[field] = value
    _set_flag(db, audit, flag)
    assert narrator_audit_session_markers(db, chapter.id, _tracks(take)) == ([], 1)


def test_legacy_clip_without_index_is_allowed_only_for_a_unique_script_piece(studio):
    db, chapter, take, audit, flag = studio
    tracks = _tracks(take)
    tracks[0].clips[0].role_index = None
    markers, skipped = narrator_audit_session_markers(db, chapter.id, tracks)
    assert [marker.start for marker in markers] == [24]
    assert skipped == 0


def test_legacy_clip_without_segment_cannot_be_guessed_from_timing(studio):
    db, chapter, take, audit, flag = studio
    tracks = _tracks(take)
    tracks[0].clips[0].segment_id = ""
    assert narrator_audit_session_markers(db, chapter.id, tracks) == ([], 1)


@pytest.mark.parametrize("legacy", [False, True])
def test_two_narrator_spans_in_one_segment_require_the_correct_piece_index(studio, legacy):
    db, chapter, take, audit, flag = studio
    original = flag["text"]
    split = original.index("красный")
    first = db.query(V2Attribution).filter(V2Attribution.segment_id == flag["segment_id"]).one()
    first.span_end = split
    db.add(V2Attribution(id=str(uuid.uuid4()), segment_id=flag["segment_id"], version=1,
                         speaker="Рассказчик", span_start=split, span_end=len(original), source="llm"))
    flag.update(index=1, text=original[split:])
    _set_flag(db, audit, flag)
    tracks = _tracks(take)
    tracks[0].clips.append(SessionClip(name="вторая часть", path="narrator.wav", audio_file_id=take.id,
                                      segment_id=flag["segment_id"], role_index=1,
                                      start=40, source_in=100, source_out=110))
    if legacy:
        for clip in tracks[0].clips:
            clip.role_index = None
    markers, skipped = narrator_audit_session_markers(db, chapter.id, tracks)
    assert [marker.start for marker in markers] == ([] if legacy else [44])
    assert skipped == (1 if legacy else 0)

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import AudioFile, AsrJob, ScriptBook, ScriptChapter
from app.services.asr_acceptance import approve_heard
from app.services.asr_replay import realign_job
from app.services.asr_run import run_asr_for_take
from app.v2.models import V2Attribution, V2Segment


@pytest.fixture
def studio(tmp_path):
    engine = create_engine("sqlite:///" + str(tmp_path / "studio.db"))
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as db:
        db.add(ScriptBook(id="book", title="Тест", source_filename="source.txt", source_format="txt"))
        db.add(ScriptChapter(id="chapter", book_id="book", chapter_index=1,
                             chapter_title="Глава 1", status="published"))
        for i, text in enumerate(["Стой здесь.", "Р-р-р!.."]):
            db.add(V2Segment(id=f"seg{i}", book_id="book", chapter_id="chapter",
                             ordinal=i, kind="paragraph", text=text))
            db.add(V2Attribution(id=f"attr{i}", segment_id=f"seg{i}", version=1,
                                 speaker="Страж", span_start=0, span_end=len(text), source="llm"))
        db.commit()
    yield factory
    engine.dispose()


def make_audio(db):
    audio = AudioFile(id="audio", book_code="Т",
                      chapter="Глава 1", role="Страж", actor_name="Актёр",
                      kind="take", original_filename="take.wav", canonical_filename="take.wav",
                      stored_key="take.wav", mime_type="audio/wav", size_bytes=100,
                      md5="1" * 32, duration_seconds=5, location="local")
    db.add(audio)
    db.flush()
    return audio


def accepted(db, audio):
    heard = {"text": "Не двигайся.", "segments": []}
    result = approve_heard(db, audio, heard, [
        {"line_index": 0, "kind": "variant", "observed_text": "Не двигайся.",
         "takes": [{"start": .1, "end": 1.0}]},
        {"line_index": 1, "kind": "vocalization", "observed_text": "[рык]",
         "takes": [{"start": 2.0, "end": 3.0}, {"start": 3.5, "end": 4.5}]},
    ], reviewed_by="operator", reason="Вариант и вокализация приняты после прослушивания.")
    assert heard == {"text": "Не двигайся.", "segments": []}
    return result


def test_owner_decision_survives_restart_and_replay_without_fabricating_asr(studio):
    with studio() as db:
        audio = make_audio(db)
        approved = accepted(db, audio)
        job = run_asr_for_take(db, audio.id, transcribe=lambda _: approved, notify=False)
        identity = job.id
        result = json.loads(job.alignment_json)
        assert result["coverage"] == 1 and result["missing"] == []
        assert [len(line["takes"]) for line in result["lines"]] == [1, 2]
        assert all(line["accepted_by_owner"] for line in result["lines"])
        assert all(line["score"] == 0 for line in result["lines"])
        assert json.loads(job.heard_json)["text"] == "Не двигайся."
        assert json.loads(job.heard_json)["segments"] == []
        saved = job.alignment_json
        db.commit()
    with studio() as db:
        report = realign_job(db, identity)
        assert report["after"]["coverage"] == 1
        assert db.get(AsrJob, identity).alignment_json == saved


@pytest.mark.parametrize("changed", ["md5", "size_bytes", "duration_seconds", "text", "speaker"])
def test_owner_decision_is_not_reused_for_changed_source_or_script(studio, changed):
    with studio() as db:
        audio = make_audio(db)
        job = run_asr_for_take(db, audio.id, transcribe=lambda _: accepted(db, audio), notify=False)
        if changed == "text":
            db.get(V2Segment, "seg0").text = "Другой текст."
            db.get(V2Attribution, "attr0").span_end = len("Другой текст.")
        elif changed == "speaker":
            db.get(V2Attribution, "attr0").speaker = "Другой"
        else:
            setattr(audio, changed, {"md5": "2" * 32, "size_bytes": 101, "duration_seconds": 6}[changed])
        db.flush()
        realign_job(db, job.id)
        result = json.loads(job.alignment_json)
        assert result["owner_acceptance_stale"]
        assert result["matched"] == 0


@pytest.mark.parametrize("takes", [
    [{"start": -1, "end": 1}], [{"start": 1, "end": 6}],
    [{"start": 1, "end": float("nan")}], [{"start": 2, "end": 1}],
    [{"start": True, "end": 2}], [{"start": 0, "end": 2}, {"start": 1, "end": 3}],
])
def test_invalid_or_overlapping_intervals_are_rejected_before_acceptance(studio, takes):
    with studio() as db:
        audio = make_audio(db)
        with pytest.raises(ValueError):
            approve_heard(db, audio, {"text": "", "segments": []}, [
                {"line_index": 0, "kind": "variant", "observed_text": "Принятый вариант", "takes": takes}
            ], reviewed_by="operator", reason="Подтверждение")


def test_changed_manual_metadata_does_not_bypass_replay(studio):
    with studio() as db:
        audio = make_audio(db)
        approved = accepted(db, audio)
        approved["owner_acceptance"]["entries"][0]["takes"][0]["end"] = 10
        job = run_asr_for_take(db, audio.id, transcribe=lambda _: approved, notify=False)
        result = json.loads(job.alignment_json)
        assert result["owner_acceptance_stale"] and result["matched"] == 0

def test_approved_variant_and_vocalization_are_placed_and_not_reported_as_weak(studio, monkeypatch):
    from app.services import chapter_delivery as delivery
    from app.services.chapter_coverage_report import build_chapter_report

    monkeypatch.setattr(delivery, "_with_breath", lambda take, *_: (take["start"], take["end"]))
    with studio() as db:
        item = make_audio(db)
        run_asr_for_take(db, item.id, transcribe=lambda _: accepted(db, item), notify=False)
        replicas = [{"role": "Страж", "role_index": i, "text": text}
                    for i, text in enumerate(["Стой здесь.", "Р-р-р!.."])]
        alignments = delivery._lines_by_audio(db, [item.id])
        clips = delivery._place_by_script(replicas, {"Страж": [item]}, alignments,
                                         relative=True, files_by_id={item.id: item})
        assert len(clips) == 3
        assert [clip.role_index for _role, clip in clips] == [0, 1, 1]
        report = build_chapter_report(db, "chapter")
        assert report["missing"] == [] and report["weak"] == []

def test_english_author_words_use_book_language_during_acceptance_and_replay(studio):
    from app.services.asr_run import expected_lines

    with studio() as db:
        db.get(ScriptBook, "book").language = "en"
        text = '"Stay here," she said.'
        db.get(V2Segment, "seg0").text = text
        db.get(V2Attribution, "attr0").span_end = len(text)
        db.flush()
        item = make_audio(db)
        reviewed = approve_heard(db, item, {"text": "Wait.", "segments": []}, [
            {"line_index": 0, "kind": "variant", "observed_text": "Wait.",
             "takes": [{"start": .1, "end": 1.0}]}
        ], reviewed_by="operator", reason="Accepted after listening.")
        job = run_asr_for_take(db, item.id, transcribe=lambda _: reviewed, notify=False)
        identity = job.id
        assert "she said" not in expected_lines(db, "chapter", "Страж")[0]
        assert json.loads(job.alignment_json)["lines"][0]["accepted_by_owner"]
        db.commit()
    with studio() as db:
        realign_job(db, identity)
        result = json.loads(db.get(AsrJob, identity).alignment_json)
        assert result["lines"][0]["accepted_by_owner"]
        assert not result.get("owner_acceptance_stale")

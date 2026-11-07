"""Оплаченный ответ другого профиля сохраняется, но не выдаётся за актуальный."""
import json
import re
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import AmbientTrack, AudioFile, BackgroundRun, Character, ConsiliumFinding, ScriptBook, ScriptChapter
from app.services import ambient_engine, consilium_engine, sound_engine
from app.v2.models import V2Attribution, V2Segment


@pytest.fixture
def factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'profiles.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as db:
        db.add(ScriptBook(id="book", title="Source title", source_filename="source.txt", source_format="txt", language="ru"))
        db.add(ScriptChapter(id="chapter", book_id="book", chapter_index=1, chapter_title="Source heading"))
        db.add(Character(id="actor-role", book_id="book", name="Aster", aliases="", race=""))
        text = '“Hello,” Aster said.'
        db.add(V2Segment(id="segment", book_id="book", chapter_id="chapter", ordinal=0, text=text, kind="paragraph"))
        db.add(V2Attribution(id="attribution", segment_id="segment", version=1, speaker="Aster", source="operator",
                             span_start=0, span_end=len(text)))
        db.commit()
    yield factory
    engine.dispose()


def language(factory, profile):
    with factory() as db:
        db.get(ScriptBook, "book").language = profile
        db.commit()


def background(factory, kind):
    with factory() as db:
        row = BackgroundRun(id=str(uuid.uuid4()), run_key=f"{kind}:book", status="running", meta_json="{}")
        db.add(row)
        db.commit()
        return row.id


@pytest.mark.parametrize("original,changed", [("ru", "en"), ("en", "ru")])
def test_consilium_uses_profile_specific_paid_cache_and_keeps_old_artifacts(factory, tmp_path, original, changed):
    language(factory, original)
    with factory() as db:
        state = consilium_engine.load_book_state(db, "book")
    chapter = state.chapters[0]
    paths = []
    for reader in ("opus", "sol"):
        consilium_engine.save_answers("book", chapter, reader, "fake-model", {0: "Aster"}, True, root=str(tmp_path))
        path = Path(consilium_engine.artifact_path("book", 1, reader, str(tmp_path), language=original))
        if original == "ru":
            data = json.loads(path.read_text())
            data.pop("language")
            path.write_text(json.dumps(data))
        paths.append((path, path.read_bytes()))
    language(factory, changed)
    calls = []
    def reader(model, system, user, schema):
        calls.append((system, user))
        return {"lines": [{"id": "#00000", "speaker": "Aster", "confidence": 1.0}]}
    with factory() as db:
        estimate = consilium_engine.estimate(db, "book", root=str(tmp_path))
    assert estimate["chapters_to_read"]["recheck"] == estimate["profile_stale_chapters"] == 1
    assert calls == [] and all(path.read_bytes() == data for path, data in paths)
    result = consilium_engine.run_consilium(session_factory=factory, book_id="book", run_id=background(factory, "consilium"),
                 mode="recheck", ask=reader, read_credits=lambda: None, notify=lambda _: None, root=str(tmp_path))
    assert result["status"] == "done" and len(calls) == 2
    assert all(("English book" in system) == (changed == "en") for system, _ in calls)
    assert all(path.read_bytes() == data for path, data in paths)
    language(factory, original)
    def forbidden(*args, **kwargs):
        pytest.fail("reused cache must not charge again")
    again = consilium_engine.run_consilium(session_factory=factory, book_id="book", run_id=background(factory, "consilium"),
                 mode="recheck", ask=forbidden, read_credits=lambda: None, notify=lambda _: None, root=str(tmp_path))
    assert again["status"] == "done" and again["chapters_incomplete"] == 0


@pytest.mark.parametrize("original,changed", [("ru", "en"), ("en", "ru")])
def test_sound_cache_is_profile_specific_without_silent_paid_rereads(factory, tmp_path, original, changed):
    language(factory, original)
    with factory() as db:
        chapter = sound_engine.book_chapters(db, "book")[0]
    answer = {"scenes": [], "transitions": [], "sounds": []}
    sound_engine.save_sidecar("book", 1, chapter[2], {"complete": True, "answer": answer}, str(tmp_path), language=original)
    path = Path(sound_engine.sidecar_path("book", 1, str(tmp_path), language=original))
    if original == "ru":
        data = json.loads(path.read_text())
        data.pop("language")
        path.write_text(json.dumps(data))
    prior = path.read_bytes()
    language(factory, changed)
    calls = []
    with factory() as db:
        plan = sound_engine.estimate(db, "book", root=str(tmp_path))
    assert plan["chapters_to_read"]["rest"] == plan["profile_stale_chapters"] == 1 and calls == []
    def ask(model, system, user, schema):
        calls.append((system, user))
        return answer
    result = sound_engine.run_sound(session_factory=factory, book_id="book", run_id=background(factory, "sound"),
                  mode="rest", ask=ask, read_credits=lambda: None, notify=lambda _: None, root=str(tmp_path))
    assert result["status"] == "done" and len(calls) == 1
    assert ("CHAPTER:" in calls[0][1]) == (changed == "en")
    assert path.read_bytes() == prior
    language(factory, original)
    def forbidden(*args, **kwargs):
        pytest.fail("reused cache must not charge again")
    result = sound_engine.run_sound(session_factory=factory, book_id="book", run_id=background(factory, "sound"),
                  mode="rest", ask=forbidden, read_credits=lambda: None, notify=lambda _: None, root=str(tmp_path))
    assert result["chapters_skipped"] == 1


@pytest.mark.parametrize("renamed", ["Beryl", "Narrator"])
def test_typed_rename_resolves_saved_role_refs_and_invalidates_old_name_prompt(factory, tmp_path, renamed):
    with factory() as db:
        book = db.get(ScriptBook, "book")
        book.language, book.role_identity_mode = "en", "typed_v1"
        row = db.get(V2Attribution, "attribution")
        row.role_ref = "character:actor-role"
        row.span_end = 8
        db.add(V2Attribution(id="narration", segment_id="segment", version=1, speaker="Рассказчик",
                            role_ref="narrator:system", span_start=8, span_end=len('“Hello,” Aster said.'), source="operator"))
        db.commit()
        before = consilium_engine.load_book_state(db, "book")
    for reader in ("opus", "sol"):
        consilium_engine.save_answers("book", before.chapters[0], reader, "fake", {0: "Aster"}, True, root=str(tmp_path))
    with factory() as db:
        db.get(Character, "actor-role").name = renamed
        db.commit()
        state = consilium_engine.load_book_state(db, "book")
        assert state.chapters[0].current == state.chapters[0].speakers == {0: renamed}
        assert state.chapters[0].cast_lines == [f"- {renamed}"]
        assert state.chapters[0].narration[0] == ' Aster said.'
        assert db.get(V2Attribution, "attribution").speaker == "Aster"
    assert consilium_engine.load_answers("book", state.chapters[0], "opus", root=str(tmp_path)) is None
    answers = {"opus": {(1, 0): renamed}, "sol": {(1, 0): renamed}}
    assert consilium_engine.findings_for(state, answers) == {}
    calls = []
    def ask(model, system, user, schema):
        calls.append(user)
        return {"lines": [{"id": "#00000", "speaker": renamed, "confidence": 1.0}]}
    result = consilium_engine.run_consilium(session_factory=factory, book_id="book", run_id=background(factory, "consilium"),
                  mode="recheck", ask=ask, read_credits=lambda: None, notify=lambda _: None, root=str(tmp_path))
    assert result["status"] == "done" and result["findings"] == 0 and len(calls) == 2
    assert all(f"- {renamed}" in prompt for prompt in calls)
    with factory() as db:
        assert db.query(ConsiliumFinding).count() == 0
        assert db.get(V2Attribution, "attribution").role_ref == "character:actor-role"
        assert db.get(V2Segment, "segment").text == '“Hello,” Aster said.'


@pytest.mark.parametrize("original,changed", [("ru", "en"), ("en", "ru")])
def test_ambient_profile_stale_plan_preserves_audio_and_never_autopays(factory, monkeypatch, original, changed):
    language(factory, original)
    item = {"marker_id": "scene", "ordinal": 0, "file_name": "scene.mp3", "span": (0, 10), "seconds": 10,
            "scene": {"place": {"name": "Garden", "description": ""}, "time_of_day": "", "weather": "",
                      "ambience": "", "mood": "", "music_queries": []}}
    monkeypatch.setattr(ambient_engine, "_chapter_scenes", lambda db, cid: [item])
    monkeypatch.setattr(ambient_engine, "_ensure_room", lambda size: None)
    calls, audio_calls = [], []
    def ask(model, system, user, schema):
        calls.append((system, user))
        return {"prompt": "quiet instrumental strings", "summary": "strings"}
    def compose(prompt, seconds):
        audio_calls.append(prompt)
        return b"paid-audio", "fake-song"
    def store(db, chapter, book, **kwargs):
        audio = AudioFile(id=str(uuid.uuid4()), kind="ambient", original_filename="scene.mp3",
                          stored_key=str(uuid.uuid4()) + ".mp3", chapter="1", role="Эмбиент", mime_type="audio/mpeg", size_bytes=10)
        db.add(audio)
        db.flush()
        return audio
    monkeypatch.setattr(ambient_engine, "_store_track", store)
    def generate():
        return ambient_engine.run_ambient(session_factory=factory, chapter_id="chapter", run_id=background(factory, "ambient"),
                    ask=ask, compose=compose, notify=lambda _: None)
    assert generate()["status"] == "done" and len(audio_calls) == 1
    with factory() as db:
        first = db.query(AmbientTrack).one()
        first_id, audio_id = first.id, first.audio_file_id
        assert first.language_snapshot == original and ambient_engine.ambient_plan(db, "chapter") == []
    language(factory, changed)
    with factory() as db:
        pending = ambient_engine.ambient_plan(db, "chapter")
        assert len(pending) == 1 and pending[0]["profile_stale"] is True
        assert db.get(AmbientTrack, first_id).status == "done" and db.get(AudioFile, audio_id) is not None
    assert len(audio_calls) == len(calls) == 1
    assert generate()["status"] == "done" and len(audio_calls) == 2
    with factory() as db:
        assert db.get(AmbientTrack, first_id).status == "replaced"
        assert db.get(AudioFile, audio_id) is not None
        assert db.query(AmbientTrack).filter_by(status="done").one().language_snapshot == changed
    language(factory, original)
    with factory() as db:
        assert ambient_engine.ambient_plan(db, "chapter")[0]["profile_stale"] is True
    assert len(audio_calls) == 2

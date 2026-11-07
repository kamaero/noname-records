"""Язык книги проходит до моделей и бесплатных проверок без запуска русских слоёв."""
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Character, ScriptBook, ScriptChapter, ScriptJob
from app.pipeline.attribution_evidence import classify_span, find_name_hits, speech_cue
from app.services import char_extraction
from app.v2 import pipeline
from app.v2.attribute import NARRATOR
from app.v2.models import V2Attribution, V2Run, V2StressMark
from app.v2.reader import effective_attributions
from app.v2.segmenter import segment_chapter
from app.v2.store import load_chapter_segments, store_attributions, store_chapter_segments
from app.v2.stress_ops import stress_queue


@pytest.fixture
def english_book():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        book = ScriptBook(id="en-book", title="The Clock", source_filename="clock.txt", source_format="txt", language="en")
        chapter = ScriptChapter(id="en-ch1", book_id=book.id, chapter_index=1, chapter_title="Chapter 1")
        db.add_all([book, chapter, Character(id="alice", book_id=book.id, name="Alice"),
                    Character(id="bob", book_id=book.id, name="Bob")])
        db.commit()
        yield db, book, chapter
    engine.dispose()


def _store_text(db, book, chapter, text):
    chapter.source_text = text
    store_chapter_segments(db, book_id=book.id, chapter_id=chapter.id,
                           segments=segment_chapter(text, chapter_id=chapter.id))
    db.commit()
    return load_chapter_segments(db, chapter_id=chapter.id)


def test_extraction_uses_english_book_context_and_preserves_names(english_book, monkeypatch):
    db, book, chapter = english_book
    captured = []

    def fake_chat(*args, **kwargs):
        captured.append((args[3], args[4]))
        return {"content": json.dumps({"characters": [{"canonical_name": "Renée O'Neil", "aliases": [],
                "race": "", "temperament": "Quiet.", "appears_in_chapters": [1]}],
                "book_synopsis": "A clock goes missing."})}

    monkeypatch.setattr(char_extraction, "call_chat", fake_chat)
    monkeypatch.setattr(char_extraction, "_resolve_provider", lambda _: ("fake", "unused", "openai"))
    prompt = char_extraction._char_extraction_system_prompt(db, book)
    parsed = char_extraction._run_single_char_extraction_request(
        db=db, book=book, job=ScriptJob(provider="fake", model="fake"), system_prompt=prompt,
        payload_text='“Where is it?” Renée asked.', batch_label="whole-book", chapter_numbers=[1])
    assert "English fiction" in captured[0][0]
    assert "Language: English" in captured[0][1]
    assert "Renée" in parsed["characters"][0]["canonical_name"]
    book.language = "ru"
    assert "русскоязычной прозе" in char_extraction._char_extraction_system_prompt(db, book)


def test_english_attribution_keeps_original_spans_and_manual_paragraph(english_book):
    db, book, chapter = english_book
    text = '“Go,” Alice said, “before dawn.”\n\n“I will stay,” Bob said.'
    segments = _store_text(db, book, chapter, text)
    manual = segments[1]
    store_attributions(db, [{"unit_id": manual.id, "span_start": 0, "span_end": len(manual.text),
                             "speaker": "Bob", "confidence": 1, "source": "operator"}])
    db.commit()
    calls = []

    def llm(system, user, schema):
        calls.append((system, user))
        assert "English book" in system and "ANNOTATE:" in user
        assert manual.text not in user
        return {"content": json.dumps({"items": [{"id": "#00000", "speaker": "Alice", "confidence": 0.95,
            "parts": [{"speaker": "Alice", "quote": '“Go,”'},
                      {"speaker": NARRATOR, "quote": ' Alice said, '},
                      {"speaker": "Alice", "quote": '“before dawn.”'}]}]})}

    result = pipeline.attribute_chapter_rows(db, book, chapter, db.query(Character).all(),
                                            llm=llm, provider="fake", model="fake")
    db.commit()
    assert result["problems"] == [] and len(calls) == 1
    rows = effective_attributions(db, [s.id for s in segments])
    assert [(r.span_start, r.span_end, r.speaker) for r in rows[segments[0].id]] == [
        (0, 6, "Alice"), (6, 18, NARRATOR), (18, 32, "Alice")]
    assert db.query(V2Attribution).filter_by(segment_id=manual.id).count() == 1
    assert rows[manual.id][0].source == "operator"
    assert "".join(segments[0].text[r.span_start:r.span_end] for r in rows[segments[0].id]) == segments[0].text
    assert [s.text for s in load_chapter_segments(db, chapter_id=chapter.id)] == [s.text for s in segments]


def test_low_confidence_review_uses_same_english_context():
    from app.v2.run import attribute_chapter
    from app.v2.units import Unit

    calls = []
    unit = Unit(id="u", chapter="ch", ordinal=1, text='“Who is there?” Alice asked.')

    def llm(system, user, schema):
        calls.append((system, user))
        return {"content": json.dumps({"items": [{"id": "#00001", "speaker": "Alice",
                                                   "confidence": 0.5 if len(calls) == 1 else 0.95}]})}

    records, problems, stats = attribute_chapter([unit], cast={"Alice": "Alice"}, cast_lines=["- Alice"],
                                                llm=llm, language="en")
    assert len(calls) == 2 and calls[0][0] == calls[1][0]
    assert "WINDOW" in calls[1][1] and "→ REVIEW" in calls[1][1]
    assert records[0]["speaker"] == "Alice" and records[0]["source"] == "llm_review"


def test_english_stress_skips_models_and_preserves_manual_marks(english_book, monkeypatch):
    from app.v2.homograph_ops import homograph_words, homograph_places
    db, book, chapter = english_book
    segment, context_segment = _store_text(db, book, chapter, "A Russian word: замок.\n\nA second Russian word: замок.")
    db.add(V2StressMark(id="manual", segment_id=segment.id, word_start=16, word_end=21,
                        vowel_offset=1, version=1, source="operator"))
    context_start = context_segment.text.index("замок")
    db.add(V2StressMark(id="old-context", segment_id=context_segment.id, word_start=context_start,
                        word_end=context_start + 5, vowel_offset=1, version=1, source="context"))
    db.commit()

    def forbidden(*args, **kwargs):
        pytest.fail("English books must not load Russian stress resources")

    monkeypatch.setattr(pipeline, "_ruaccent_layer", forbidden)
    monkeypatch.setattr(pipeline.Resolver, "default", forbidden)
    ctx = pipeline.RunContext()
    resolver = ctx.get_resolver(db, book)
    assert resolver.context is None
    result = pipeline.step_stress(db, book, V2Run(book_id=book.id), ctx)
    assert result["reason"] == "not_applicable_for_english"
    assert pipeline.stress_chapter_rows(db, chapter, resolver)["marks"] == 0
    queue = stress_queue(db, book.id, dict_lookup=forbidden)
    assert queue["reason"] == "not_applicable_for_english" and queue["unresolved"] == []
    assert homograph_words(db, book.id, dict_lookup=forbidden) == []
    assert homograph_places(db, book.id, "замок", dict_lookup=forbidden) == []
    assert db.get(V2StressMark, "manual").source == "operator"
    assert db.query(V2StressMark).count() == 2


@pytest.mark.parametrize("tag,speaker,proved", [
    ("Alice said.", "Alice", True), ("said Alice.", "Alice", True),
    ("Alice quietly asked Bob.", "Alice", True), ("Alice said to Bob.", "Bob", False),
    ("Alice told Bob.", "Bob", False), ("Alice and Bob said.", "Bob", False),
    ("Alice said Bob was late.", "Bob", False), ("Alice's father said.", "Alice", False),
    ("Renée O'Neil whispered.", "Renée O'Neil", True),
])
def test_english_cues_require_named_subject_not_addressee(tag, speaker, proved):
    names = {"alice": "Alice", "bob": "Bob", "renée o'neil": "Renée O'Neil"}
    assert bool(speech_cue(tag, speaker, names, language="en")) is proved


def test_english_evidence_is_explicit_and_no_russian_stemming():
    names = {"alice": "Alice", "alicehurst": "Alicehurst"}
    found = classify_span(speaker="Alice", span_text='“Go,” Alice said.', narration=[],
                          paragraph=1, names=names, language="en")
    assert found.kind == "cue_named" and found.quote == "Alice said."
    assert classify_span(speaker="Alice", span_text='“Go,” Alice said.', narration=[],
                         paragraph=1, names=names).kind == "unsupported"
    assert find_name_hits("Alicia said.", names, language="en") == []


def test_evidence_inventory_and_gate_read_stored_book_language(english_book):
    from app.pipeline.attribution_gate import check_gate
    from app.pipeline.attribution_inventory import build_inventory

    db, book, chapter = english_book
    text = '“Go,” Alice said.'
    segment = _store_text(db, book, chapter, text)[0]
    store_attributions(db, [{"unit_id": segment.id, "span_start": 0, "span_end": len(text),
                             "speaker": "Alice", "source": "llm"}])
    db.commit()
    assert build_inventory(db, book_id=book.id)["totals"]["cue_named"] == 1
    store_attributions(db, [{"unit_id": segment.id, "span_start": 0, "span_end": len(text),
                             "speaker": "Bob", "source": "operator"}])
    db.commit()
    gate = check_gate(db, book_id=book.id)
    assert gate["violations"] and gate["violations"][0].kind == "cue_named"


@pytest.fixture
def english_cli_db(tmp_path):
    path = tmp_path / "english.db"
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as db:
        book = ScriptBook(id="cli-book", title="Clock", source_filename="clock.txt", source_format="txt", language="en")
        chapter = ScriptChapter(id="cli-ch1", book_id=book.id, chapter_index=1, chapter_title="Chapter 1")
        db.add_all([book, chapter, Character(id="cli-alice", book_id=book.id, name="Alice")])
        db.commit()
        segments = _store_text(db, book, chapter, '“Come in,” Alice said.\n\nA preserved manual paragraph.')
        store_attributions(db, [{"unit_id": segments[1].id, "span_start": 0, "span_end": len(segments[1].text),
                                 "speaker": NARRATOR, "confidence": 1, "source": "operator"}])
        db.add(V2StressMark(id="cli-manual-stress", segment_id=segments[1].id, word_start=0, word_end=1,
                            vowel_offset=0, version=1, source="operator"))
        db.commit()
    yield path, factory
    engine.dispose()


def test_stress_cli_skips_english_before_loading_any_russian_resources(english_cli_db, monkeypatch, capsys):
    import sys
    from scripts.v2 import stress_book
    from app.v2 import stress_ruaccent

    path, factory = english_cli_db

    def forbidden(*args, **kwargs):
        pytest.fail("English CLI must not open Russian dictionaries or load RUAccent")

    monkeypatch.setattr(sys, "argv", ["stress_book.py", "cli-book", "--db", str(path)])
    monkeypatch.setattr(stress_book, "load_author_map", forbidden)
    monkeypatch.setattr(stress_book.Resolver, "default", forbidden)
    monkeypatch.setattr(stress_ruaccent, "context_layer", forbidden)
    assert stress_book.main() == 0
    assert "does not apply to English" in capsys.readouterr().out
    with factory() as db:
        assert db.query(V2StressMark).count() == 1


def test_attribution_cli_selects_english_and_keeps_manual_rows(english_cli_db, monkeypatch):
    import sys
    from scripts.v2 import attribute_book
    from app.v2 import llm as llm_module

    path, factory = english_cli_db
    calls = []

    def fake_llm(system, user, schema):
        calls.append((system, user))
        assert "English book" in system and "ANNOTATE:" in user
        assert "preserved manual paragraph" not in user
        return {"content": json.dumps({"items": [{"id": "#00000", "speaker": "Alice", "confidence": 0.95,
            "parts": [{"speaker": "Alice", "quote": '“Come in,”'},
                      {"speaker": NARRATOR, "quote": ' Alice said.'}]}]})}

    monkeypatch.setattr(llm_module, "make_llm", lambda *a, **kw: fake_llm)
    monkeypatch.setattr(sys, "argv", ["attribute_book.py", "cli-book", "--db", str(path), "--workers", "1"])
    assert attribute_book.main() == 0 and len(calls) == 1
    with factory() as db:
        manual = db.query(V2Attribution).filter_by(segment_id="cli-ch1:00001").all()
        assert len(manual) == 1 and manual[0].source == "operator"
        result = effective_attributions(db, ["cli-ch1:00000"])["cli-ch1:00000"]
        assert [row.speaker for row in result] == ["Alice", NARRATOR]


def test_english_progress_explains_skip_without_hiding_existing_marks(english_book):
    from app.v2.progress import book_progress

    db, book, chapter = english_book
    segment = _store_text(db, book, chapter, "An English chapter.")[0]
    steps = {row["key"]: row for row in book_progress(db, book.id)["steps"]}
    assert steps["stress"]["state"] == "skipped"
    assert "does not apply to English" in steps["stress"]["detail"]
    db.add(V2StressMark(id="progress-mark", segment_id=segment.id, word_start=0, word_end=2,
                        vowel_offset=0, source="operator", version=1))
    db.commit()
    steps = {row["key"]: row for row in book_progress(db, book.id)["steps"]}
    assert steps["stress"]["state"] == "done"


def test_public_progress_labels_and_hints_are_english_for_russian_books_too(english_book):
    import re
    from app.constants import STATUS_LABELS, WORKSPACE_TABS, ChapterStatus, status_label
    from app.services.book_progress import collect_books_progress
    from app.v2.progress import book_progress

    db, book, chapter = english_book
    book.language = "ru"
    book.status = "author_review"
    db.commit()
    payload = book_progress(db, book.id)
    for step in payload["steps"]:
        assert not re.search("[А-Яа-яЁё]", step["label"] + step["detail"])
    summary = collect_books_progress(db, [book])[book.id]
    assert summary["action_hint"] == "The book is ready for review."
    assert summary["effective_status_label"] == "Author review"
    assert status_label(ChapterStatus.FAILED.value) == "Failed"
    assert ChapterStatus.FAILED.value == "failed"
    assert all(not re.search("[А-Яа-яЁё]", label) for label in STATUS_LABELS.values())
    assert all(not re.search("[А-Яа-яЁё]", row["label"]) for row in WORKSPACE_TABS.values())

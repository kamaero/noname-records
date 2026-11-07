"""Локализация не должна менять имена, коды ошибок и требования к доказательствам."""
import re
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import BackgroundRun, Character, ConsiliumFinding, ProviderKey, ScriptBook, ScriptChapter
from app.pipeline.consilium_prompts import NARRATOR
from app.pipeline.consilium_run import arbitrate_place, read_chapter
from app.services import provider_keys
from app.services.audition_scores import lane_labels
from app.services.audit import audit_action_label
from app.v2.models import V2Attribution, V2Segment
from app.v2.role_export import Chapter, Line, Piece, RoleExport, chapter_heading, render_text


@pytest.fixture
def database():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    yield factory
    engine.dispose()


def test_legacy_provider_check_details_are_localized_without_rewriting_records(database):
    with database() as db:
        row = ProviderKey(provider="deepseek", ciphertext="", last4="", check_status="bad_key",
                          check_detail="Ключ не подходит — проверьте, что скопирован целиком.")
        db.add(row)
        db.commit()
        info = provider_keys.key_info(db, "deepseek")
        assert info["check_status"] == "bad_key" and info["check_detail"].startswith("The key was rejected")
        assert row.check_detail == "Ключ не подходит — проверьте, что скопирован целиком."
        row.check_detail = "Custom diagnostic: account access is pending"
        db.commit()
        assert provider_keys.key_info(db, "deepseek")["check_detail"] == row.check_detail


def test_public_labels_are_english_and_keep_machine_keys(database):
    from app.services.step_models import steps_view
    with database() as db:
        book = ScriptBook(title="Source title", source_filename="x.txt", source_format="txt")
        db.add(book)
        db.commit()
        assert lane_labels(db, book) == {"author": "Author", "studio": "Studio"}
        steps = steps_view(db)
        assert {step["step"] for step in steps} >= {"attribution", "characters", "consilium_arbiter", "asr"}
        for step in steps:
            assert not re.search("[А-Яа-яЁё]", step["title"])
            for option in step["options"]:
                assert not re.search("[А-Яа-яЁё]", option["label"] + option["note"] + option["price"])
    assert audit_action_label("login_password") == "Password sign-in"
    assert audit_action_label("future_action_code") == "future_action_code"


def test_english_review_reader_preserves_paragraphs_and_internal_narrator():
    captured = []
    paragraphs = [(0, '“Go,” Alice said.'), (1, "I remember the rain.")]

    def ask(model, system, user, schema):
        captured.append((system, user))
        return {"lines": [{"id": "#00000", "speaker": "Alice", "confidence": 0.9},
                          {"id": "#00001", "speaker": NARRATOR, "confidence": 0.9}]}

    answers, complete = read_chapter(model="fake", cast_lines=["- Alice"], paragraphs=paragraphs,
                                      ask=ask, pause=0, language="en")
    assert complete and answers == {0: "Alice", 1: NARRATOR}
    assert "English book" in captured[0][0] and "CHAPTER CAST" in captured[0][1]
    assert all(text in captured[0][1] for _, text in paragraphs)
    assert '"Рассказчик"' in captured[0][0]


@pytest.mark.parametrize("speaker,kind,quote,proven", [
    ("Alice", "cue_named", "Alice said.", True),
    ("Alice", "cue_named", "Alice whispered.", False),
    ("Alice", "context_only", "Alice said.", False),
    (NARRATOR, "cue_named", "Alice said.", False),
])
def test_english_arbiter_still_requires_verbatim_proof(speaker, kind, quote, proven):
    captured = []

    def ask(model, system, user, schema):
        captured.append((system, user))
        return {"speaker": speaker, "evidence_kind": kind, "evidence_para": 0,
                "evidence_quote": quote, "reason": "An author tag identifies the speaker."}

    verdict = arbitrate_place(chapter_index=1, ordinal=0, current="Bob", opus="Alice", sol="Alice",
        texts={0: '“Go,” Alice said.'}, speakers={0: "Bob"}, races={}, ask=ask,
        canon=lambda name: name if name in {"Alice", "Bob", NARRATOR} else "", model="fake", language="en")
    assert verdict["proven"] is proven
    assert verdict["verdict"] == ("change" if proven else "undecidable")
    assert "English multi-voice" in captured[0][0] and "DISPUTED PARAGRAPH" in captured[0][1]


def test_partial_english_review_does_not_remove_an_unanswered_finding(database, tmp_path):
    from app.services.consilium_engine import run_consilium
    with database() as db:
        db.add(ScriptBook(id="b", title="The clock", source_filename="clock.txt", source_format="txt", language="en"))
        db.add(ScriptChapter(id="ch", book_id="b", chapter_index=1, chapter_title="Chapter 1"))
        db.add(Character(id="alice", book_id="b", name="Alice"))
        for ordinal, text in enumerate(('“Go,” Alice said.', '“Stay,” Alice said.')):
            segment = f"ch:{ordinal:05d}"
            db.add(V2Segment(id=segment, book_id="b", chapter_id="ch", ordinal=ordinal, kind="paragraph", text=text))
            db.add(V2Attribution(id=f"a{ordinal}", segment_id=segment, span_start=0, span_end=len(text),
                                 speaker="Alice", source="llm", version=1))
        db.add(ConsiliumFinding(id="existing", book_id="b", chapter_index=1, ordinal=1, segment_id="ch:00001",
                                span_start=0, span_end=19, current_speaker="Alice", readers_speaker=NARRATOR,
                                reader_opus=NARRATOR, reader_sol=NARRATOR, kind="wrong_voice", status="new"))
        db.add(BackgroundRun(id="run", job_kind="consilium", entity_type="book", entity_id="b", status="running",
                             started_at=datetime(2026, 1, 1), heartbeat_at=datetime(2026, 1, 1)))
        db.commit()
    calls = []

    def incomplete(model, system, user, schema):
        calls.append(system)
        assert "English book" in system
        return {"lines": [{"id": "#00000", "speaker": "Alice", "confidence": 0.9}]}

    result = run_consilium(session_factory=database, book_id="b", run_id="run", mode="reread", ask=incomplete,
                            read_credits=lambda: 10000.0, notify=lambda text: None,
                            root=str(tmp_path), arbiter_workers=1)
    assert result["status"] == "done" and result["chapters_incomplete"] == 1 and result["gone"] == 0
    assert calls
    with database() as db:
        assert db.get(ConsiliumFinding, "existing").status == "new"
        assert db.get(ScriptBook, "b").title == "The clock"


def test_english_role_export_retains_source_text_and_existing_chapter_titles():
    source = '“Don’t change it,” Alice said.'
    export = RoleExport(book_title="The clock", role="Alice", actor="Alex Smith", description="", facts="", note="",
                        chapters=[Chapter(index=1, title="Chapter 1. The door", filename="CLOCK_Ch01_Alice.wav",
                                          lines=[Line(pieces=[Piece(source, True)], cue_speaker="", cue_text="")])])
    text = render_text(export)
    assert "Actor: Alex Smith" in text and "Recording file:" in text
    assert source in text and "Chapter 1. Chapter 1" not in text
    assert export.role == "Alice" and export.chapters[0].lines[0].pieces[0].text == source
    assert chapter_heading(export.chapters[0]) == "Chapter 1. The door"


def test_new_english_imports_use_english_fallback_titles_and_leave_existing_books(database, monkeypatch):
    from app.services.book_import import enqueue_book, split_into_chapters, _score_heading_line
    from app.models import ScriptLog
    monkeypatch.setattr("app.services.book_import._persist_book_source_snapshot", lambda *a, **kw: None)
    monkeypatch.setattr("app.services.author_detect.detect_author_id", lambda *a, **kw: "")
    assert split_into_chapters("An unheaded story.", language="en") == [("Chapter 1", "An unheaded story.")]
    assert split_into_chapters("An unheaded story.")[0][0] == "Глава 1"
    assert _score_heading_line("II", True, True, language="en")[2] == "Chapter II"
    assert _score_heading_line("2", True, True, language="en")[2] == "Chapter 2"
    with database() as db:
        db.add(ScriptBook(id="old", title="Старая книга", source_filename="old.txt", source_format="txt", language="ru"))
        db.add(ScriptChapter(id="old-ch", book_id="old", chapter_index=1, chapter_title="Глава 1"))
        db.commit()
        created = enqueue_book(db, "new.txt", b"An unheaded story.", language="en", title="New book")
        db.commit()
        chapters = db.query(ScriptChapter).filter_by(book_id=created.id).all()
        assert len(chapters) == 1 and chapters[0].chapter_title == "Chapter 1"
        assert chapters[0].source_text == "An unheaded story."
        assert db.get(ScriptChapter, "old-ch").chapter_title == "Глава 1"
        assert db.get(ScriptBook, "old").title == "Старая книга"
        logs = db.query(ScriptLog).filter_by(book_id=created.id).all()
        assert logs and all(not re.search("[А-Яа-яЁё]", row.message) for row in logs)


def test_english_long_chapter_parts_are_named_without_translating_source():
    from app.pipeline.text_splitter import _explode_large_chapters
    source = "First sentence. " * 40
    parts = _explode_large_chapters([("Chapter 1", source)], max_chars=120, language="en")
    assert len(parts) > 1 and all(title.startswith("Chapter 1 / part ") for title, _ in parts)
    assert " ".join(" ".join(part for _, part in parts).split()) == " ".join(source.split())


def test_english_access_roster_does_not_reveal_existing_passwords_or_change_accounts(database):
    from app.models import User, UserRole
    from app.services.passwords import hash_password
    from app.services.user_admin import export_roster
    hashed = hash_password("test-existing-password")
    with database() as db:
        db.add(User(id="reader", login="reader", display_name="Alex Smith", password_hash=hashed, is_active="true"))
        db.add(UserRole(user_id="reader", role="dictor"))
        db.commit()
        roster = export_roster(db)
        assert "name\tlogin\tpassword\ttelegram\troles\tsign-in" in roster
        assert "Alex Smith\treader\t— set previously" in roster
        assert "test-existing-password" not in roster and hashed not in roster
        assert db.get(User, "reader").password_hash == hashed
        fresh = export_roster(db, issued=[{"id": "reader", "password": "test-new-password"}])
        assert "test-new-password" in fresh
        assert db.get(User, "reader").password_hash == hashed


def test_telegram_action_labels_keep_deadline_callback_contract():
    from app.services.bot_out import invite_markup

    buttons = [button for row in invite_markup("source-role-id")["inline_keyboard"] for button in row]
    assert [button["text"] for button in buttons] == ["✅ Accept", "✖️ Decline", "⏳ Need more time"]
    assert [button["callback_data"] for button in buttons] == [
        "dl:y:source-role-id", "dl:n:source-role-id", "dl:t:source-role-id",
    ]


def test_pipeline_failures_are_english_and_preserve_raw_model_content(monkeypatch, tmp_path):
    from app.services.char_extraction import _parse_char_extraction_payload
    from app.v2 import llm

    raw_content = "Исходный ответ модели"
    with pytest.raises(RuntimeError) as failure:
        _parse_char_extraction_payload(raw_content)
    assert "could not parse JSON" in str(failure.value)
    assert "Content: " + raw_content in str(failure.value)
    monkeypatch.setattr(llm, "PROMPT_PATH", tmp_path / "missing.txt")
    with pytest.raises(FileNotFoundError, match="Attribution system prompt not found"):
        llm.load_system_prompt("en")
    monkeypatch.setattr(llm, "_resolve_provider", lambda provider: ("", "unused", "openai"))
    with pytest.raises(RuntimeError, match="No API key for provider 'test-provider'"):
        llm.make_llm("test-provider", "test-model")


def test_sound_summary_is_english_and_preserves_source_title(database):
    from app.services.sound_engine import _notify

    with database() as db:
        book = ScriptBook(id="sound-summary", title="Название книги", source_filename="x.txt", source_format="txt")
        db.add(book)
        db.commit()
    messages = []
    _notify(database, messages.append, "sound-summary", {
        "status": "done", "chapters_done": 2, "markers": 3, "places_new": 4,
        "pairs": 5, "chapters_incomplete": 0, "spent_rub": 6,
    })
    assert len(messages) == 1 and "Название книги" in messages[0]
    assert "new markers 3, new locations 4" in messages[0]
    assert "spent ≈ 6 ₽" in messages[0]

"""Сводка не теряет роли на длинных страницах и не открывается диктору."""
from datetime import datetime
from html.parser import HTMLParser

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import AudioFile, Character, ScriptBook, ScriptChapter, TelegramAuthAccount, User, UserRole
from app.services import bot_book_summary as summary
from app.services.audio_uploads import derive_book_code
from app.services.bot_dialog import handle_update

NOW = datetime(2026, 11, 17)


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, autoflush=False)() as session:
        session.add(ScriptBook(id="b1", title="Example book", source_filename="x", source_format="txt"))
        for n in range(1, 4):
            session.add(ScriptChapter(id=f"ch{n}", book_id="b1", chapter_index=n, chapter_title=f"Chapter {n}"))
        for name, actor in [("Done", "Voice A"), ("Partial", "Voice A"), ("Zero", "Voice A"),
                            ("Free", ""), ("Tentative", "Voice B?"), ("Silent", "Voice A")]:
            session.add(Character(id=name, book_id="b1", name=name, actor_name=actor,
                                  appears_in="" if name == "Silent" else "1,2,3"))
        session.commit()
        for name, chapters in [("Done", [1, 2, 3]), ("Partial", [1])]:
            for chapter in chapters:
                take(session, name, chapter)
        yield session
    engine.dispose()


def take(db, role, chapter, actor="Voice A", kind="take", **kwargs):
    item = AudioFile(book_code=derive_book_code("Example book"), role=role, chapter=f"Chapter {chapter}",
                     actor_name=actor, kind=kind, original_filename="x.wav", stored_key=f"{role}/{chapter}/{actor}/{kind}",
                     mime_type="audio/wav", size_bytes=1, **kwargs)
    db.add(item)
    db.commit()
    return item


def buttons(markup):
    return [button for row in markup["inline_keyboard"] for button in row]


def test_groups_agree_with_chapters_and_ignore_auditions_other_actors_and_duplicates(db):
    take(db, "Partial", 2, kind="audition")
    take(db, "Partial", 2, actor="Voice B")
    take(db, "Partial", 1)
    rows = summary.progress_rows(db, "b1")
    assert len(rows) == 5
    partial = next(row for row in rows if row["name"] == "Partial")
    assert partial["recorded_chapters"] == 1
    assert partial["missing_chapters"] == [2, 3]
    groups = summary.groups(rows)
    assert {key: [row["name"] for row in values] for key, values in groups.items()} == {
        "u": ["Free", "Tentative"], "z": ["Zero"], "p": ["Partial"]}
    assert sum(row["complete"] for row in rows) == 1


def test_book_picker_links_to_summary_and_each_group(db):
    text, markup = summary.reply(db, "b")
    assert "Example book" in str(markup)
    assert buttons(markup)[0]["callback_data"] == "ss:book:b1"
    text, markup = summary.reply(db, "book:b1")
    assert "1/5" in text
    assert [button["callback_data"] for button in buttons(markup)] == [
        "ss:roles:b1:u:0", "ss:roles:b1:z:0", "ss:roles:b1:p:0", "ss:b"]
    text, markup = summary.reply(db, "roles:b1:p:0")
    assert "Partial" in text and "Voice A" in text and "1/3" in text and "2–3" in text
    text, _ = summary.reply(db, "roles:b1:u:0")
    assert "Free" in text and "Tentative" in text and "Voice B" in text


def test_next_request_reflects_new_take_and_recast(db):
    take(db, "Partial", 2)
    take(db, "Partial", 3)
    assert "<b>Partial</b>" not in summary.reply(db, "roles:b1:p:0")[0]
    db.get(Character, "Done").actor_name = "Voice B"
    db.commit()
    assert "Done" in summary.reply(db, "roles:b1:z:0")[0]


def test_pages_include_every_role_with_safe_html_and_telegram_limits(db):
    db.get(ScriptBook, "b1").title = "Example book <&> " + "😀" * 180
    for n in range(85):
        db.add(Character(id=f"extra{n}", book_id="b1", name=f"Extra {n:03} <&> " + "😀" * 180,
                         actor_name="Voice B?", appears_in="1,2,3"))
    db.commit()
    seen, key, pages = [], "roles:b1:u:0", 0
    while key:
        text, markup = summary.reply(db, key)
        pages += 1
        assert len(text.encode("utf-16-le")) // 2 <= 4096
        assert "<&>" not in text
        assert all(len(button["callback_data"].encode()) <= 64 for button in buttons(markup))
        class Parser(HTMLParser):
            def handle_starttag(self, tag, attrs):
                assert tag == "b" and not attrs
        Parser().feed(text)
        for n in range(85):
            if f"Extra {n:03}" in text:
                seen.append(n)
        next_buttons = [b for b in buttons(markup) if b["callback_data"].startswith("ss:roles:")
                        and int(b["callback_data"].split(":")[-1]) == pages]
        key = next_buttons[0]["callback_data"][3:] if next_buttons else ""
    assert sorted(seen) == list(range(85))
    assert pages > 1


@pytest.mark.parametrize("key", ["bad", "book:", "book:b1:extra", "roles:b1:x:0", "roles:b1:p:-1",
                                "roles:b1:p:abc", "roles:b1:p:10000000"])
def test_invalid_callbacks_are_ignored(db, key):
    assert summary.reply(db, key) == ("", None)


def test_stale_books_and_old_page_number_are_safe(db):
    assert "1/1" in summary.reply(db, "roles:b1:p:999999")[0]
    db.get(ScriptBook, "b1").voiced_at = NOW
    db.commit()
    text, markup = summary.reply(db, "book:b1")
    assert text and buttons(markup)[0]["callback_data"] == "ss:b"
    assert not buttons(summary.reply(db, "b")[1])


def test_chapter_ranges_are_sorted_deduplicated():
    assert summary.chapter_ranges([60, 1, 3, 2, 3, 7, 8]) == "1–3, 7–8, 60"


@pytest.mark.parametrize("role,allowed", [("admin", True), ("author", True), ("agent", True), ("dictor", False)])
@pytest.mark.parametrize("key", ["b", "book:b1", "roles:b1:p:0"])
def test_new_navigation_rechecks_staff_rights(db, role, allowed, key):
    db.add(User(id="user", login="test", password_hash="x", display_name="Voice A", is_active="true"))
    db.add(UserRole(user_id="user", role=role))
    db.add(TelegramAuthAccount(telegram_user_id="50", user_id="user", role=role,
                              access_scope="full", is_active="true"))
    db.commit()
    update = {"callback_query": {"id": "test", "from": {"id": 50}, "data": "ss:" + key,
                                 "message": {"message_id": 1, "chat": {"id": 50, "type": "private"}}}}
    out = handle_update(db, update, now=NOW)
    assert out[0].method == "answerCallbackQuery"
    assert [item.method for item in out[1:]] == (["sendMessage"] if allowed else [])
    if allowed:
        assert out[1].payload["reply_markup"]["inline_keyboard"]

def test_typed_book_uses_explicit_role_and_book_identity(db):
    db.get(ScriptBook, "b1").role_identity_mode = "typed_v1"
    db.commit()
    take(db, "Different label", 1, role_book_id="b1", role_ref="character:Partial")
    take(db, "Partial", 2, role_book_id="other-book", role_ref="character:Partial")
    rows = {row["name"]: row for row in summary.progress_rows(db, "b1")}
    assert rows["Partial"]["recorded_chapters"] == 1
    assert rows["Partial"]["missing_chapters"] == [2, 3]
    assert rows["Done"]["recorded_chapters"] == 0



def test_asr_progress_distinguishes_typed_character_named_narrator(db):
    from app.models import BookBudget
    from app.services.role_recording_progress import book_recording_progress
    from app.services.role_identity import NARRATOR_REF, TITLE_REF
    from app.v2.models import V2Attribution, V2Segment

    db.get(ScriptBook, "b1").role_identity_mode = "typed_v1"
    db.get(ScriptChapter, "ch1").released_at = NOW
    db.get(Character, "Done").name = "Narrator"
    db.add(BookBudget(book_id="b1", narrator_actor_name="Voice A"))
    for ordinal, speaker, role_ref in (
        (1, "Former character name", "character:Done"),
        (2, "Рассказчик", NARRATOR_REF),
        (3, "Partial", "character:Partial"),
    ):
        segment_id = f"segment-{ordinal}"
        db.add(V2Segment(id=segment_id, book_id="b1", chapter_id="ch1", ordinal=ordinal,
                         kind="paragraph", text="Speech.", char_start=0, char_end=7))
        db.add(V2Attribution(id=f"attribution-{ordinal}", segment_id=segment_id, span_start=0, span_end=7,
                             speaker=speaker, role_ref=role_ref, confidence=1, source="operator", version=1))
    db.commit()
    take(db, "Narrator", 1, role_book_id="b1", role_ref="character:Done")
    take(db, "Narrator", 1, role_book_id="b1", role_ref=NARRATOR_REF)
    take(db, "Chapter title", 1, role_book_id="b1", role_ref=TITLE_REF)
    assert book_recording_progress(db, "b1")["summary"] == {
        "chapters_total": 3,
        "chapters_released": 1,
        "narrator_recorded_chapters": 1,
        "characters_recorded": 1,
        "characters_total": 2,
    }

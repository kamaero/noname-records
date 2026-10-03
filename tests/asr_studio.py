"""Глава с дублями для тестов сверки: сегмент на реплику, дубль через настоящий приём.

Дубль проходит `run_asr_for_take` с подменённым распознаванием — сверка, `heard_json`
и `alignment_json` получаются теми же, что на проде, а не собранными руками.
"""
import uuid

from app.time_utils import utcnow_naive
from app.v2.models import V2Attribution, V2Segment

BOOK_ID = "b1"
CHAPTER_TITLE = "Глава 11. Дорога"


def heard(*texts, gap: float = 0.5):
    """Подмена распознавания: каждый текст — свой сегмент со словными таймингами."""
    segments, clock = [], 0.0
    for text in texts:
        words, cursor = [], clock
        for word in text.split():
            words.append({"word": word, "start": cursor, "end": cursor + 0.4})
            cursor += 0.5
        segments.append({"text": text, "start": clock, "end": cursor, "words": words})
        clock = cursor + gap
    return lambda path: {"text": " ".join(texts), "segments": segments}


def make_chapter(db, lines, cast):
    """`lines` — [(роль, текст)] по порядку главы; `cast` — {роль: актёр в касте}."""
    from app.models import Character, ScriptBook, ScriptChapter

    chapter_id = "ch-11"
    if db.get(ScriptBook, BOOK_ID) is None:
        db.add(ScriptBook(id=BOOK_ID, title="Крылья полумрака", source_filename="k.txt",
                          source_format="txt", created_at=utcnow_naive()))
    db.add(ScriptChapter(id=chapter_id, book_id=BOOK_ID, chapter_index=11,
                         chapter_title=CHAPTER_TITLE, status="published"))
    for role, actor in cast.items():
        db.add(Character(book_id=BOOK_ID, name=role, actor_name=actor))
    for ordinal, (role, text) in enumerate(lines):
        segment_id = f"{chapter_id}:{ordinal:05d}"
        db.add(V2Segment(id=segment_id, book_id=BOOK_ID, chapter_id=chapter_id,
                         ordinal=ordinal, kind="paragraph", text=text))
        db.add(V2Attribution(id=str(uuid.uuid4()), segment_id=segment_id, version=1, speaker=role,
                             span_start=0, span_end=len(text), source="llm", created_at=utcnow_naive()))
    db.flush()
    return chapter_id


def move_line(db, chapter_id, ordinal, to_role):
    """Новая версия абзаца с другой ролью — как её пишет `reassign_segment`."""
    segment_id = f"{chapter_id}:{ordinal:05d}"
    segment = db.get(V2Segment, segment_id)
    latest = max(row.version for row in db.query(V2Attribution).filter(V2Attribution.segment_id == segment_id))
    db.add(V2Attribution(id=str(uuid.uuid4()), segment_id=segment_id, version=latest + 1, speaker=to_role,
                         span_start=0, span_end=len(segment.text), source="operator", created_at=utcnow_naive()))
    db.flush()


def add_take(db, chapter_id, *, role, actor, said, take_id=None):
    """Дубль роли, в котором прозвучало `said`, — через настоящий приём, без писем."""
    from app.models import AudioFile
    from app.services.asr_run import run_asr_for_take

    take_id = take_id or f"take-{role}-{uuid.uuid4().hex[:6]}"
    db.add(AudioFile(id=take_id, book_code="КП", original_filename=f"{role}.wav", stored_key=f"k/{take_id}.wav",
                     canonical_filename=f"KP_Ch11_{role}.wav", mime_type="audio/wav", size_bytes=10,
                     chapter=CHAPTER_TITLE, role=role, actor_name=actor, kind="take",
                     uploaded_at=utcnow_naive()))
    db.flush()
    return run_asr_for_take(db, take_id, transcribe=heard(*said), notify=False)

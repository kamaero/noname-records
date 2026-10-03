"""What the actor's reader gets for a chapter.

Only the latest version of an annotation counts; older rows stay in the tables as
history and must not leak into the payload. Colours and actors come from the
book's cast, the narrator is always first in the legend, and a chapter with no
v2 rows at all still returns a well-formed empty payload.
"""
import uuid

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import BookBudget, Character, ScriptBook, ScriptChapter
from app.v2.models import V2Attribution, V2Segment, V2StressMark
from app.v2.reader import (
    NARRATOR,
    UNSURE,
    build_book_chapters,
    build_chapter_payload,
    effective_attributions,
    effective_stress,
)

BOOK = "book-1"
CH1 = "ch-1"
CH2 = "ch-2"
SEG_A = f"{CH1}:00000"
SEG_B = f"{CH1}:00001"


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)


def _attr(segment_id, start, end, speaker, version, confidence=0.9, source="llm"):
    return V2Attribution(
        id=str(uuid.uuid4()), segment_id=segment_id, span_start=start, span_end=end,
        speaker=speaker, confidence=confidence, source=source, version=version,
    )


def _seed(db):
    db.add(ScriptBook(id=BOOK, title="Крылья", display_title="Крылья полумрака", source_filename="k.txt", source_format="txt"))
    db.add(ScriptChapter(id=CH1, book_id=BOOK, chapter_index=1, chapter_title="Глава 1"))
    db.add(ScriptChapter(id=CH2, book_id=BOOK, chapter_index=2, chapter_title="Глава 2"))
    db.add(Character(id="c1", book_id=BOOK, name="Дгарнин", actor_name="Иван Петров"))
    db.add(Character(id="c2", book_id=BOOK, name="Пупип", actor_name="Пётр Иванов"))
    db.add(BookBudget(id="b1", book_id=BOOK, narrator_actor_name="Голос Автора"))

    text_a = "Дгарнин сидел в изгибе ветвей."
    text_b = "- Нас стало слишком много, - сказал Дгарнин."
    db.add(V2Segment(id=SEG_A, book_id=BOOK, chapter_id=CH1, ordinal=0, kind="paragraph", text=text_a, char_start=0, char_end=len(text_a)))
    db.add(V2Segment(id=SEG_B, book_id=BOOK, chapter_id=CH1, ordinal=1, kind="paragraph", text=text_b, char_start=0, char_end=len(text_b)))

    # Segment A: version 1 said Пупип, version 2 corrected to the narrator.
    db.add(_attr(SEG_A, 0, len(text_a), "Пупип", 1))
    db.add(_attr(SEG_A, 0, len(text_a), NARRATOR, 2, confidence=0.99, source="operator"))
    # Segment B: one version, two spans (line + narrator tag), written out of order.
    db.add(_attr(SEG_B, 28, len(text_b), NARRATOR, 1))
    db.add(_attr(SEG_B, 0, 28, "Дгарнин", 1, confidence=0.95))

    # Stress: v1 marked the wrong vowel, v2 fixed it; B has a single mark.
    db.add(V2StressMark(id="s1", segment_id=SEG_A, word_start=0, word_end=7, vowel_offset=1, source="dict", version=1))
    db.add(V2StressMark(id="s2", segment_id=SEG_A, word_start=0, word_end=7, vowel_offset=4, source="operator", version=2))
    db.add(V2StressMark(id="s3", segment_id=SEG_B, word_start=36, word_end=43, vowel_offset=4, source="dict", version=1))
    db.commit()


def test_effective_rows_keep_only_the_latest_version():
    SessionLocal = _session()
    with SessionLocal() as db:
        _seed(db)
        attrs = effective_attributions(db, [SEG_A, SEG_B])
        assert [row.speaker for row in attrs[SEG_A]] == [NARRATOR]
        assert attrs[SEG_A][0].version == 2
        assert [(row.span_start, row.speaker) for row in attrs[SEG_B]] == [(0, "Дгарнин"), (28, NARRATOR)]

        stress = effective_stress(db, [SEG_A, SEG_B])
        assert [(row.vowel_offset, row.version) for row in stress[SEG_A]] == [(4, 2)]
        assert [row.word_start for row in stress[SEG_B]] == [36]
        assert effective_attributions(db, []) == {}


def test_chapter_payload_assembles_segments_cast_and_chapters():
    SessionLocal = _session()
    with SessionLocal() as db:
        _seed(db)
        payload = build_chapter_payload(db, CH1)

    assert payload["book"] == {"id": BOOK, "title": "Крылья полумрака"}
    assert payload["chapter"] == {
        "id": CH1, "index": 1, "title": "Глава 1", "segments_count": 2, "attributed": True,
        "approved": False, "status": "queued",
    }
    assert [(c["index"], c["has_v2"]) for c in payload["chapters"]] == [(1, True), (2, False)]

    segments = payload["segments"]
    assert [s["id"] for s in segments] == [SEG_A, SEG_B]
    assert segments[0]["spans"] == [
        {"start": 0, "end": 30, "speaker": NARRATOR, "confidence": 0.99, "source": "operator"},
    ]
    assert [s["speaker"] for s in segments[1]["spans"]] == ["Дгарнин", NARRATOR]
    assert segments[0]["stress"] == [{"start": 0, "end": 7, "vowel": 4, "source": "operator", "rare": True}]
    assert segments[1]["stress"] == [{"start": 36, "end": 43, "vowel": 4, "source": "dict", "rare": True}]
    assert payload["can_edit"] is False

    cast = payload["cast"]
    assert [c["name"] for c in cast] == [NARRATOR, "Дгарнин"]
    assert cast[0]["actor"] == "Голос Автора"
    assert cast[0]["lines"] == 2
    assert {k: cast[1][k] for k in ("name", "actor", "lines")} == {"name": "Дгарнин", "actor": "Иван Петров", "lines": 1}
    assert cast[1]["character_id"] == "c1"
    assert cast[0]["character_id"] == ""  # no Character row for the narrator in this seed
    for entry in cast:
        assert entry["color"].startswith("#") and entry["text_color"].startswith("#")
        assert entry["weight"] and entry["font_style"] in ("normal", "italic")
    assert cast[0]["color"] != cast[1]["color"]


def test_remark_candidates_travel_with_the_segment_payload():
    # `remark_candidates` (see app/v2/remark_candidates.py) rides in the payload the
    # same way stress already does: computed server-side from the segment's own
    # effective spans, the reader only draws what it is given.
    SessionLocal = _session()
    with SessionLocal() as db:
        _seed(db)
        text = "- Болота… - впервые за вечер отозвался Бдеукс. Он молчал."
        seg = f"{CH1}:00003"
        db.add(V2Segment(id=seg, book_id=BOOK, chapter_id=CH1, ordinal=3, text=text, char_end=len(text)))
        db.add(_attr(seg, 0, len(text), "Бдеукс", 1))

        # A paragraph already split into character + narrator (as the automatic pass
        # leaves them): the narrator half is packed with its own inner dashes, and
        # `remark_candidates` must not search inside it at all — not even find zero.
        split_seg = f"{CH1}:00004"
        split_text = "- Нас стало слишком много, - сказал Дгарнин - и другие тоже - согласился он."
        split_at = split_text.index(" - сказал")
        db.add(V2Segment(id=split_seg, book_id=BOOK, chapter_id=CH1, ordinal=4, text=split_text, char_end=len(split_text)))
        db.add(_attr(split_seg, 0, split_at, "Дгарнин", 1))
        db.add(_attr(split_seg, split_at, len(split_text), NARRATOR, 1))
        db.commit()
        payload = build_chapter_payload(db, CH1)

    segments = {s["id"]: s for s in payload["segments"]}
    result = segments[seg]["remark_candidates"]
    assert len(result) == 1
    piece = text[result[0]["start"]:result[0]["end"]]
    assert piece.strip() == "- впервые за вечер отозвался Бдеукс. Он молчал."

    assert segments[split_seg]["remark_candidates"] == []


def test_unsure_goes_last_and_unknown_speakers_still_get_a_style():
    SessionLocal = _session()
    with SessionLocal() as db:
        _seed(db)
        text = "Кто-то крикнул из темноты."
        seg = f"{CH1}:00002"
        db.add(V2Segment(id=seg, book_id=BOOK, chapter_id=CH1, ordinal=2, text=text, char_end=len(text)))
        db.add(_attr(seg, 0, 10, UNSURE, 1, confidence=0.0))
        db.add(_attr(seg, 10, len(text), "Незнакомец", 1))
        db.commit()
        payload = build_chapter_payload(db, CH1)

    names = [c["name"] for c in payload["cast"]]
    assert names[0] == NARRATOR and names[-1] == UNSURE
    assert "Незнакомец" in names
    stranger = next(c for c in payload["cast"] if c["name"] == "Незнакомец")
    assert stranger["actor"] == "" and stranger["color"].startswith("#")
    assert stranger["character_id"] == ""


def test_can_edit_is_passed_through_from_the_caller():
    SessionLocal = _session()
    with SessionLocal() as db:
        _seed(db)
        assert build_chapter_payload(db, CH1, can_edit=True)["can_edit"] is True


def test_chapter_without_v2_rows_is_empty_but_well_formed():
    SessionLocal = _session()
    with SessionLocal() as db:
        _seed(db)
        payload = build_chapter_payload(db, CH2)
        assert payload["chapter"]["segments_count"] == 0
        assert payload["chapter"]["attributed"] is False
        assert payload["segments"] == []
        assert [c["name"] for c in payload["cast"]] == [NARRATOR]
        assert build_chapter_payload(db, "nope") is None


def test_book_chapters_listing():
    SessionLocal = _session()
    with SessionLocal() as db:
        _seed(db)
        listing = build_book_chapters(db, BOOK)
        assert listing["book"]["title"] == "Крылья полумрака"
        assert [(c["id"], c["has_v2"]) for c in listing["chapters"]] == [(CH1, True), (CH2, False)]
        assert build_book_chapters(db, "nope") is None

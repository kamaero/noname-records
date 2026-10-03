import os
import sys
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import app.services.char_extraction as sp
from app.db import Base
from app.models import Character, ScriptBook
from app.pipeline.book_parser import _display_title_from_filename
from app.services.char_extraction import _build_char_extraction_batches, _merge_extracted_characters


def test_merge_extracted_characters_merges_alias_identity() -> None:
    items = [
        {
            "char_map_id": "map-1",
            "canonical_name": "Дгарнин",
            "aliases": ["Пресвитер"],
            "race": "Фархеррим",
            "temperament": "Холодный стратег",
            "appears_in_chapters": [1],
        },
        {
            "char_map_id": "map-2",
            "canonical_name": "Пресвитер",
            "aliases": [],
            "race": "Демон",
            "temperament": "Стратег и наблюдатель",
            "appears_in_chapters": [2, "3"],
        },
    ]

    merged = _merge_extracted_characters(items)

    assert len(merged) == 1
    assert merged[0]["char_map_id"] == "map-1"
    assert merged[0]["canonical_name"] == "Дгарнин"
    assert "Пресвитер" in merged[0]["aliases"]
    assert merged[0]["appears_in_chapters"] == [1, 2, 3]
    # Берём более "длинное" описание при merge.
    assert merged[0]["temperament"] == "Стратег и наблюдатель"


def test_build_char_extraction_batches_splits_by_limit() -> None:
    chapters = [
        SimpleNamespace(chapter_title="Глава 1", source_text=("а" * 70)),
        SimpleNamespace(chapter_title="Глава 2", source_text=("б" * 70)),
        SimpleNamespace(chapter_title="Глава 3", source_text=("в" * 20)),
    ]

    batches = _build_char_extraction_batches(chapters, max_chars=180)

    assert len(batches) == 2
    assert len(batches[0]) == 1
    assert len(batches[1]) == 2


def test_run_char_extraction_falls_back_to_batches(monkeypatch) -> None:
    chapters = [
        SimpleNamespace(chapter_index=1, chapter_title="Глава 1", source_text=("а" * 80)),
        SimpleNamespace(chapter_index=2, chapter_title="Глава 2", source_text=("б" * 80)),
    ]

    class FakeQuery:
        def __init__(self, rows):
            self._rows = rows

        def filter(self, *args, **kwargs):
            return self

        def order_by(self, *args, **kwargs):
            return self

        def all(self):
            return self._rows

    class FakeDB:
        def query(self, *args, **kwargs):
            return FakeQuery(chapters)

    calls: list[str] = []
    merged_payload: dict[str, list[dict]] = {"value": []}

    def fake_run_single(**kwargs):
        label = str(kwargs.get("batch_label") or "")
        calls.append(label)
        if label == "whole-book":
            raise RuntimeError("simulate whole-book parse failure")
        idx = int(kwargs.get("chapter_numbers")[0])
        return {
            "characters": [
                {
                    "canonical_name": "Дгарнин",
                    "aliases": ["Пресвитер"],
                    "appears_in_chapters": [idx],
                }
            ],
            "book_synopsis": f"Синопсис для главы {idx}",
        }

    def fake_bulk_import(db, book_id, characters):
        merged_payload["value"] = characters
        return len(characters)

    monkeypatch.setattr(sp, "_load_prompt_template", lambda *args, **kwargs: "prompt")
    monkeypatch.setattr(sp, "_run_single_char_extraction_request", fake_run_single)
    monkeypatch.setattr(sp, "_bulk_import_characters", fake_bulk_import)
    monkeypatch.setattr(sp.settings, "char_extraction_whole_book_max_chars", 1000000)
    monkeypatch.setattr(sp.settings, "char_extraction_part_chars", 140)

    book = SimpleNamespace(id="book-1", title="Тестовая книга")
    job = SimpleNamespace(provider="deepseek", model="deepseek-chat")
    imported = sp._run_char_extraction(FakeDB(), book, job)

    assert imported == 1
    assert calls[0] == "whole-book"
    assert "batch-1" in calls and "batch-2" in calls
    assert len(merged_payload["value"]) == 1
    assert merged_payload["value"][0]["appears_in_chapters"] == [1, 2]


def test_parse_extracted_characters_tolerates_wrapped_json() -> None:
    payload = """
    Вот результат:
    ```json
    {
      "characters": [
        {
          "canonical_name": "Астрид Дегатти",
          "aliases": ["Астрид"],
          "appears_in_chapters": [1]
        }
      ]
    }
    ```
    """.strip()

    parsed = sp._parse_char_extraction_payload(payload)["characters"]

    assert parsed == [
        {
            "canonical_name": "Астрид Дегатти",
            "aliases": ["Астрид"],
            "appears_in_chapters": [1],
        }
    ]


def test_parse_char_extraction_payload_extracts_synopsis() -> None:
    payload = """
    {
      "book_synopsis": "Тёмное фэнтези о семье магов и цене силы.",
      "characters": [
        {
          "canonical_name": "Астрид Дегатти",
          "aliases": ["Астрид"],
          "appears_in_chapters": [1]
        }
      ]
    }
    """.strip()

    parsed = sp._parse_char_extraction_payload(payload)

    assert parsed["book_synopsis"] == "Тёмное фэнтези о семье магов и цене силы."
    assert parsed["characters"][0]["canonical_name"] == "Астрид Дегатти"


def test_merge_extracted_characters_filters_obvious_alias_noise() -> None:
    merged = _merge_extracted_characters(
        [
            {
                "char_map_id": "map-astrid",
                "canonical_name": "Астрид Дегатти",
                "aliases": [
                    "Астрид",
                    "Дегатти",
                    "Астрит Дегатти",
                    "дорогая",
                    "мама",
                    "Астрид Блистательная",
                    "самая великолепная девочка во всей мириаде миров",
                    "Бэмби",
                ],
                "appears_in_chapters": [1, 2],
            }
        ]
    )

    assert merged[0]["aliases"] == ["Астрид", "Астрит Дегатти", "Бэмби", "Дегатти"]


def test_filter_extracted_aliases_drops_generic_lowercase_forms() -> None:
    aliases = sp._filter_extracted_aliases(
        "Йоханнес",
        ["дружище", "госпожа", "доктор Йоханнес", "Йоханнес", "Мапна"],
    )

    assert aliases == ["доктор Йоханнес", "Мапна"]


def test_merge_extracted_characters_skips_non_speaker_like_entities() -> None:
    merged = _merge_extracted_characters(
        [
            {
                "char_map_id": "map-good",
                "canonical_name": "Астрид Дегатти",
                "aliases": ["Астрид"],
                "appears_in_chapters": [1],
            },
            {
                "char_map_id": "map-bad",
                "canonical_name": "банка с соком",
                "aliases": ["сок"],
                "appears_in_chapters": [2],
            },
        ]
    )

    assert len(merged) == 1
    assert merged[0]["canonical_name"] == "Астрид Дегатти"


def test_display_title_from_filename_keeps_soft_sign_in_semya() -> None:
    title = _display_title_from_filename("belozerovi_skazki_volshebnikov_2.fb2")
    assert title == 'Белозёровы - "Сказки волшебников 2"'


def test_run_char_extraction_batch_with_fallback_splits_failed_batch(monkeypatch) -> None:
    calls: list[str] = []

    def fake_run_single(**kwargs):
        label = str(kwargs.get("batch_label") or "")
        chapter_numbers = list(kwargs.get("chapter_numbers") or [])
        calls.append(f"{label}:{chapter_numbers}")
        if len(chapter_numbers) > 1:
            raise RuntimeError("malformed json")
        idx = int(chapter_numbers[0])
        return {
            "characters": [{"canonical_name": f"Персонаж {idx}", "aliases": [], "appears_in_chapters": [idx]}],
            "book_synopsis": f"Синопсис {idx}",
        }

    monkeypatch.setattr(sp, "_run_single_char_extraction_request", fake_run_single)

    chapters = [
        SimpleNamespace(chapter_index=1, chapter_title="Глава 1", source_text="a"),
        SimpleNamespace(chapter_index=2, chapter_title="Глава 2", source_text="b"),
        SimpleNamespace(chapter_index=3, chapter_title="Глава 3", source_text="c"),
    ]
    book = SimpleNamespace(id="book-1", title="Тест")
    job = SimpleNamespace(provider="deepseek", model="deepseek-chat")

    parsed = sp._run_char_extraction_batch_with_fallback(
        db=SimpleNamespace(),
        book=book,
        job=job,
        system_prompt="prompt",
        chapters=chapters,
        batch_label="batch-1",
    )

    assert [item["canonical_name"] for item in parsed["characters"]] == ["Персонаж 1", "Персонаж 2", "Персонаж 3"]
    assert parsed["book_synopsis"] == "Синопсис 1"
    assert calls == [
        "batch-1:[1, 2, 3]",
        "batch-1.a:[1]",
        "batch-1.b:[2, 3]",
        "batch-1.b.a:[2]",
        "batch-1.b.b:[3]",
    ]


def test_run_char_extraction_populates_empty_book_annotation(monkeypatch) -> None:
    chapters = [
        SimpleNamespace(chapter_index=1, chapter_title="Глава 1", source_text=("а" * 80)),
    ]

    class FakeQuery:
        def __init__(self, rows):
            self._rows = rows

        def filter(self, *args, **kwargs):
            return self

        def order_by(self, *args, **kwargs):
            return self

        def all(self):
            return self._rows

    class FakeDB:
        def query(self, *args, **kwargs):
            return FakeQuery(chapters)

    merged_payload: dict[str, list[dict]] = {"value": []}

    monkeypatch.setattr(sp, "_load_prompt_template", lambda *args, **kwargs: "prompt")
    monkeypatch.setattr(
        sp,
        "_run_single_char_extraction_request",
        lambda **kwargs: {
            "characters": [{"canonical_name": "Дгарнин", "aliases": ["Пресвитер"], "appears_in_chapters": [1]}],
            "book_synopsis": "Тёмное фэнтези о семье магов, демонах и цене власти.",
        },
    )
    def fake_bulk_import(db, book_id, characters):
        merged_payload["value"] = characters
        return len(characters)

    monkeypatch.setattr(sp, "_bulk_import_characters", fake_bulk_import)
    monkeypatch.setattr(sp.settings, "char_extraction_whole_book_max_chars", 1000000)
    monkeypatch.setattr(sp.settings, "char_extraction_part_chars", 140)

    book = SimpleNamespace(id="book-1", title="Тестовая книга", book_annotation="")
    job = SimpleNamespace(provider="deepseek", model="deepseek-chat")
    sp._run_char_extraction(FakeDB(), book, job)

    assert book.book_annotation == "Тёмное фэнтези о семье магов, демонах и цене власти."


def test_parse_char_extraction_payload_reads_narrative_voice() -> None:
    payload = """
    {
      "book_synopsis": "Аннотация",
      "narrative_voice": {
        "mode": "character_pov",
        "owner_name": "Рэдрик Шухарт",
        "confidence": 0.84,
        "reason": "Почти вся повествовательная ткань идёт от первого лица главного героя."
      },
      "characters": [
        {
          "canonical_name": "Рэдрик Шухарт",
          "aliases": ["Рэд"],
          "appears_in_chapters": [1, 2, 3]
        }
      ]
    }
    """.strip()

    parsed = sp._parse_char_extraction_payload(payload)

    assert parsed["narrative_voice"]["mode"] == "character_pov"
    assert parsed["narrative_voice"]["owner_name"] == "Рэдрик Шухарт"
    assert float(parsed["narrative_voice"]["confidence"]) == 0.84


def test_apply_narrative_voice_to_book_maps_owner_to_character() -> None:
    engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(bind=engine)

    with SessionLocal() as db:
        book = ScriptBook(
            id="book-pov",
            title="Пикник на обочине",
            source_filename="book.fb2",
            source_format="fb2",
            chapter_count=1,
            total_chars=1000,
        )
        db.add(book)
        db.add(
            Character(
                book_id=book.id,
                char_map_id="char-red",
                name="Рэдрик Шухарт",
                aliases="Рэд,Шухарт",
            )
        )
        db.commit()

        sp._apply_narrative_voice_to_book(
            db,
            book,
            {
                "mode": "character_pov",
                "owner_name": "Рэд",
                "confidence": 0.79,
                "reason": "POV от лица Рэдрика",
            },
        )
        db.commit()

        refreshed_book = db.query(ScriptBook).filter(ScriptBook.id == book.id).first()
        refreshed_char = db.query(Character).filter(Character.book_id == book.id).first()

    assert refreshed_book is not None
    assert refreshed_book.narrative_owner_name == "Рэдрик Шухарт"
    assert float(refreshed_book.narrative_owner_confidence or 0.0) == 0.79
    assert refreshed_char is not None
    assert refreshed_char.narrator_role == "Рассказчик"

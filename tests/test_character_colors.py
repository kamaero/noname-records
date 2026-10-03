from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Character, ScriptBook
from app.services.character_colors import build_character_style_maps
from app.services.character_colors import inherit_author_character_styles
from app.services.character_colors import normalize_character_key


def make_character(name: str, **overrides):
    defaults = {
        "id": name,
        "name": name,
        "char_map_id": "",
        "character_color": "",
        "character_text_color": "",
        "character_font_weight": "",
        "character_font_style": "",
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_build_character_style_maps_diversifies_typography_for_auto_palette() -> None:
    characters = [
        make_character("Лаванда"),
        make_character("Сирень"),
        make_character("Фиалка"),
        make_character("Вереск"),
    ]

    _, text_map, weight_map, style_map = build_character_style_maps(characters, [])
    signatures = {(text_map[item.name], weight_map[item.name], style_map[item.name]) for item in characters}

    assert len(signatures) >= 2


def test_build_character_style_maps_enriches_basic_manual_color_typography() -> None:
    character = make_character(
        "Сумеречница",
        character_color="#d9d2e9",
        character_text_color="#11161d",
        character_font_weight="700",
        character_font_style="normal",
    )

    _, text_map, weight_map, style_map = build_character_style_maps([character], [])

    assert (text_map["Сумеречница"], weight_map["Сумеречница"], style_map["Сумеречница"]) != ("#11161d", "700", "normal")


def test_inherit_author_character_styles_reuses_palette_between_books() -> None:
    engine = create_engine("sqlite:///:memory:")
    Session = sessionmaker(bind=engine)
    Base.metadata.create_all(bind=engine)

    with Session() as db:
        old_book = ScriptBook(
            id="book-old",
            title="Старая книга",
            source_filename="old.txt",
            source_format="txt",
            created_by_user_id="author-1",
        )
        new_book = ScriptBook(
            id="book-new",
            title="Новая книга",
            source_filename="new.txt",
            source_format="txt",
            created_by_user_id="author-1",
        )
        db.add_all([old_book, new_book])
        db.add(
            Character(
                id="char-old",
                book_id="book-old",
                name="Дгарнин",
                character_color="#d9d2e9",
                character_text_color="#5b1838",
                character_font_weight="700",
                character_font_style="italic",
            )
        )
        fresh_character = Character(
            id="char-new",
            book_id="book-new",
            name="Дгарнин",
            character_color="",
            character_text_color="",
            character_font_weight="",
            character_font_style="",
        )
        db.add(fresh_character)
        db.commit()

        changed = inherit_author_character_styles(db, book=new_book, characters=[fresh_character])

        assert changed is True
        assert fresh_character.character_color == "#d9d2e9"
        assert fresh_character.character_text_color == "#5b1838"
        assert fresh_character.character_font_weight == "700"
        assert fresh_character.character_font_style == "italic"


def test_normalize_strips_combining_acute_stress():
    # "Расска́зчик" with U+0301 combining acute on the second "а"
    assert normalize_character_key("Расска́зчик") == "Рассказчик"


def test_normalize_strips_combining_grave_stress():
    assert normalize_character_key("сло̀во") == "слово"


def test_normalize_preserves_yo_letter():
    # NFD decomposes Ё->Е+U+0308; we must NOT drop the diaeresis
    assert normalize_character_key("Алёша") == "Алёша"


def test_normalize_strips_precomposed_latin_accent():
    # "á" (U+00E1) decomposes to a+U+0301
    assert normalize_character_key("José") == "Jose"

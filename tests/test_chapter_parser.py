import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.services.book_import import split_into_chapters


def test_split_into_chapters_returns_list() -> None:
    result = split_into_chapters("Глава 1\nТекст главы.")
    assert isinstance(result, list)


def test_split_into_chapters_preserves_content() -> None:
    text = "Глава 1\nуникальноеслово1\n\nГлава 2\nуникальноеслово2"
    result = split_into_chapters(text)
    body = " ".join(chapter_body for _, chapter_body in result)
    assert "уникальноеслово1" in body
    assert "уникальноеслово2" in body


def test_split_into_chapters_handles_empty_text() -> None:
    result = split_into_chapters("")
    assert len(result) == 1
    assert result[0][0] == "Глава 1"


def test_split_into_chapters_uses_configurable_large_chapter_limit(monkeypatch) -> None:
    import app.services.book_import as sp

    text = "a" * 90000

    monkeypatch.setattr(sp.settings, "max_chapter_chars", 120000)
    result = split_into_chapters(text)

    assert len(result) == 1
    assert result[0][1] == text


def test_split_into_chapters_keeps_legacy_floor_for_tiny_config(monkeypatch) -> None:
    import app.services.book_import as sp

    text = "a" * 50000

    monkeypatch.setattr(sp.settings, "max_chapter_chars", 1000)
    result = split_into_chapters(text)

    assert len(result) == 2

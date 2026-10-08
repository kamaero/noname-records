"""Книга-пример: делится на две главы и несёт всё, что проверяет разметка."""
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.services.book_import import extract_book_text, split_into_chapters
from app.services.studio_settings import studio_settings

SAMPLE = Path(__file__).resolve().parents[1] / "app" / "samples" / "example_detective.txt"


def test_sample_splits_into_two_chapters():
    parsed = extract_book_text("example_detective.txt", SAMPLE.read_bytes())
    chapters = split_into_chapters(parsed.text)
    assert [title for title, _ in chapters] == ["Глава 1. Пропажа", "Глава 2. Фрамуга"]


def test_sample_carries_what_markup_checks():
    text = SAMPLE.read_text(encoding="utf-8")
    # псевдоним: один гость и по фамилии, и по имени-отчеству
    assert "Пыжов" in text and "Аркадий Семёнович" in text
    # слова автора внутри реплики
    assert "— сказала она раньше, чем её спросили. —" in text
    for word in ("фрамуга", "кашне", "щеколд", "балюстрад", "Мытарства"):
        assert word in text


def test_studio_settings_onboarding_defaults():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        row = studio_settings(db)
        assert row.sample_book_id == ""
        assert row.onboarding_no_limit is False and row.onboarding_hidden is False
        assert row.onboarding_result_seen_at is None

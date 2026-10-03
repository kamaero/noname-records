"""Ответы чтецов 2026-09-12 становятся артефактами движка — с отпечатком нынешнего текста."""
import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401
import app.v2.models  # noqa: F401
from app.db import Base
from tests.consilium_book import BOOK, build_book


def test_seed_marks_chapters_with_unanswered_paragraphs_incomplete(tmp_path):
    from app.services.consilium_engine import load_answers, load_book_state
    from scripts.consilium_seed_artifacts import seed

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    source = tmp_path / "full"
    source.mkdir()
    for reader in ("opus", "sol"):
        (source / f"ch01-{reader}.json").write_text(json.dumps(
            {"chapter": 1, "reader": reader, "answers": {"0": "Гамук", "1": "Тупуг", "2": "Рассказчик"}}), "utf-8")
        (source / f"ch02-{reader}.json").write_text(json.dumps(
            {"chapter": 2, "reader": reader, "answers": {"0": "Тупуг"}}), "utf-8")
    root = tmp_path / "reports"
    with sessionmaker(bind=engine)() as db:
        build_book(db)
        out = seed(db, book_id=BOOK, source_dir=str(source), root=str(root))
        state = load_book_state(db, BOOK)
    assert out == {"seeded": 2, "incomplete": 2, "skipped_existing": 0, "unmatched": 0}
    assert load_answers(BOOK, state.chapters[0], "opus", root=str(root)) == {0: "Гамук", 1: "Тупуг", 2: "Рассказчик"}
    assert load_answers(BOOK, state.chapters[1], "opus", root=str(root)) is None  # абзац 1 без ответа


def test_seed_counts_unmatched_chapters(tmp_path):
    """Источник содержит ответы для главы без разметки в книге — должны быть в unmatched."""
    from scripts.consilium_seed_artifacts import seed

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    source = tmp_path / "full"
    source.mkdir()
    # Ответы для глав 1 и 2 (которые есть в книге)
    for reader in ("opus", "sol"):
        (source / f"ch01-{reader}.json").write_text(json.dumps(
            {"chapter": 1, "reader": reader, "answers": {"0": "Гамук", "1": "Тупуг", "2": "Рассказчик"}}), "utf-8")
        (source / f"ch02-{reader}.json").write_text(json.dumps(
            {"chapter": 2, "reader": reader, "answers": {"0": "Тупуг"}}), "utf-8")
        # Ответы для главы 9 (которой нет в книге)
        (source / f"ch09-{reader}.json").write_text(json.dumps(
            {"chapter": 9, "reader": reader, "answers": {"0": "Какой-то персонаж"}}), "utf-8")
    root = tmp_path / "reports"
    with sessionmaker(bind=engine)() as db:
        build_book(db)
        out = seed(db, book_id=BOOK, source_dir=str(source), root=str(root))
    # ch09-opus.json и ch09-sol.json не должны быть обработаны, но учтены как unmatched
    assert out == {"seeded": 2, "incomplete": 2, "skipped_existing": 0, "unmatched": 2}

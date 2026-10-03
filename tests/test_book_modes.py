from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.api.book_actions import _normalize_validation_profile
from app.handler_dependency_runtime import _serialize_book_summary
from app.models import ScriptBook


def test_script_book_defaults_to_operator_only_standard_mode() -> None:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(engine)

    with SessionLocal() as db:
        db.add(
            ScriptBook(
                id="book-1",
                title="Book",
                source_filename="book.txt",
                source_format="txt",
            )
        )
        db.commit()
        book = db.query(ScriptBook).filter(ScriptBook.id == "book-1").one()

        assert book.pipeline_mode == "standard"
        assert book.validation_profile == "operator_only"


def test_serialize_book_summary_exposes_pipeline_axes() -> None:
    book = ScriptBook(
        id="book-1",
        title="Book",
        source_filename="book.txt",
        source_format="txt",
        pipeline_mode="fantasy-large-cast",
        validation_profile="author_assisted",
    )

    row = _serialize_book_summary(book, progress=None)

    assert row["pipeline_mode"] == "fantasy-large-cast"
    assert row["validation_profile"] == "author_assisted"


def test_book_mode_normalizers_keep_safe_defaults() -> None:
    assert _normalize_validation_profile("author-assisted") == "author_assisted"
    assert _normalize_validation_profile("surprise") == "operator_only"

"""The progress payload: counts say what exists, the run says what is happening now."""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Character, ScriptBook, ScriptChapter
from app.v2.models import V2Attribution, V2Run, V2Segment, V2StressMark
from app.v2.progress import STEP_KEYS, book_counts, book_progress

BOOK = "book-prog"


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)


def _seed(db, *, status="uploaded", mode="v2", attributed=2, stressed=1, cast=1, chapter_status="queued"):
    db.add(ScriptBook(id=BOOK, title="К", source_filename="k.txt", source_format="txt", status=status, pipeline_mode=mode))
    for index in (1, 2, 3):
        chapter_id = f"ch-{index}"
        db.add(ScriptChapter(id=chapter_id, book_id=BOOK, chapter_index=index, chapter_title=f"Глава {index}", status=chapter_status))
        for ordinal in (0, 1):
            segment_id = f"{chapter_id}:{ordinal:05d}"
            db.add(V2Segment(id=segment_id, book_id=BOOK, chapter_id=chapter_id, ordinal=ordinal, text="Текст сегмента.", char_end=15))
            if index <= attributed:
                db.add(V2Attribution(id=f"a-{segment_id}", segment_id=segment_id, span_end=15, speaker="Рассказчик", source="import", version=1))
            if index <= stressed:
                db.add(V2StressMark(id=f"s-{segment_id}", segment_id=segment_id, word_start=0, word_end=5, vowel_offset=1, version=1))
    for n in range(cast):
        db.add(Character(id=f"c{n}", book_id=BOOK, name=f"Герой {n}"))
    db.commit()


def _states(payload):
    return {step["key"]: step["state"] for step in payload["steps"]}


def test_missing_book_is_none_and_counts_are_exact():
    SessionLocal = _session()
    with SessionLocal() as db:
        assert book_progress(db, "nope") is None
        _seed(db)
        assert book_counts(db, BOOK) == {"chapters": 3, "segments": 6, "attributed_chapters": 2, "stressed_chapters": 1, "cast": 1}


def test_shape_without_a_run_comes_from_the_counts_and_the_status():
    SessionLocal = _session()
    with SessionLocal() as db:
        _seed(db, status="author_review")
        payload = book_progress(db, BOOK)
    assert set(payload) == {"book_id", "model", "mode", "status", "stop_requested", "run", "steps", "counts", "review", "auto_publish"}
    assert payload["auto_publish"] is False
    # the model a run would use, and the two the hub offers
    assert payload["model"]["key"] == "deepseek-v4-pro"
    assert {item["key"] for item in payload["model"]["choices"]} == {"deepseek-v4-pro", "muse-spark-1.3-contributor"}
    assert payload["model"]["last_run_cost_rub"] is None
    # dollars converted at the studio rate; the peak tariff is shown next to it
    assert payload["model"]["rub_in"] > 0 and payload["model"]["peak_rub_in"] == payload["model"]["rub_in"] * 2
    assert payload["mode"] == "v2" and payload["run"] is None and payload["stop_requested"] is False
    assert [s["key"] for s in payload["steps"]] == list(STEP_KEYS)
    assert [s["label"] for s in payload["steps"]] == ["Сегментация", "Персонажи", "Роли", "Ударения", "Проверка", "Публикация"]
    assert _states(payload) == {"segment": "done", "cast": "done", "attribute": "pending", "stress": "pending", "review": "pending", "publish": "pending"}
    assert payload["review"] == {"total": 3, "approved": 0, "published": 0, "attributed": 2}
    details = {s["key"]: s["detail"] for s in payload["steps"]}
    assert details["attribute"] == "глав 2/3" and details["stress"] == "глав 1/3" and details["segment"] == "сегментов 6"


def test_publish_is_done_only_when_published_and_review_before_it():
    SessionLocal = _session()
    with SessionLocal() as db:
        # a published book has published chapters; published counts as approved
        _seed(db, status="published_to_dictor", attributed=3, stressed=3, chapter_status="published")
        payload = book_progress(db, BOOK)
    assert _states(payload) == {k: "done" for k in STEP_KEYS}
    assert payload["review"]["approved"] == 3


def test_the_review_step_counts_chapters_the_author_marked():
    SessionLocal = _session()
    with SessionLocal() as db:
        _seed(db, status="author_review", attributed=3, stressed=3)
        db.get(ScriptChapter, "ch-1").status = "approved"
        db.commit()
        payload = book_progress(db, BOOK)

    step = next(s for s in payload["steps"] if s["key"] == "review")
    assert (step["state"], step["detail"]) == ("running", "проверено 1 из 3")
    assert payload["review"] == {"total": 3, "approved": 1, "published": 0, "attributed": 3}


def test_a_running_run_marks_its_step_and_the_ones_after_it():
    SessionLocal = _session()
    with SessionLocal() as db:
        _seed(db, status="processing", cast=0)
        db.add(V2Run(id="r1", book_id=BOOK, status="running", step="attribute", chapters_total=3, chapters_done=2,
                     calls=7, prompt_tokens=1000, completion_tokens=200))
        db.commit()
        payload = book_progress(db, BOOK)
    assert payload["run"]["status"] == "running" and payload["run"]["step"] == "attribute"
    assert payload["run"]["tokens"] == {"prompt": 1000, "completion": 200} and payload["run"]["calls"] == 7
    assert payload["run"]["chapters_done"] == 2 and payload["run"]["chapters_total"] == 3
    assert set(payload["run"]) >= {"status", "step", "chapters_total", "chapters_done", "tokens", "started_at", "updated_at", "error"}
    states = _states(payload)
    # cast went by with nobody found → skipped; attribute is in flight; stress waits
    assert states == {"segment": "done", "cast": "skipped", "attribute": "running", "stress": "pending", "review": "pending", "publish": "pending"}
    assert "идёт 2/3" in {s["key"]: s["detail"] for s in payload["steps"]}["attribute"]


def test_a_failed_run_marks_the_step_it_fell_on_with_the_error():
    SessionLocal = _session()
    with SessionLocal() as db:
        _seed(db, status="failed")
        db.add(V2Run(id="r1", book_id=BOOK, status="failed", step="stress", error="RuntimeError: словарь не найден"))
        db.commit()
        payload = book_progress(db, BOOK)
    states = _states(payload)
    assert states["stress"] == "failed" and states["attribute"] == "skipped" and states["segment"] == "done"
    assert {s["key"]: s["detail"] for s in payload["steps"]}["stress"] == "RuntimeError: словарь не найден"
    assert payload["run"]["error"] == "RuntimeError: словарь не найден"


def test_the_newest_run_is_the_one_shown():
    SessionLocal = _session()
    with SessionLocal() as db:
        _seed(db, status="stopped")
        db.add(V2Run(id="old", book_id=BOOK, status="failed", step="segment"))
        db.commit()
        db.add(V2Run(id="new", book_id=BOOK, status="stopped", step="attribute"))
        db.commit()
        payload = book_progress(db, BOOK)
    assert payload["run"]["id"] == "new" and payload["run"]["status"] == "stopped"
    assert _states(payload)["attribute"] == "pending"


def test_the_last_run_is_priced_at_the_tariff_it_ran_on():
    """A run's cost is read off its own token counts, not off a shape that moved."""
    from datetime import datetime

    from app.v2.models import V2Run

    SessionLocal = _session()
    with SessionLocal() as db:
        _seed(db, status="author_review")
        db.add(V2Run(
            id="run-1", book_id=BOOK, status="done", step="attribute",
            chapters_total=3, chapters_done=3, calls=10,
            prompt_tokens=1_000_000, completion_tokens=500_000,
            # Friday 17:30 UTC — outside DeepSeek's weekday peak windows
            started_at=datetime(2026, 9, 4, 17, 30), updated_at=datetime(2026, 9, 4, 18, 47),
        ))
        db.commit()
        payload = book_progress(db, BOOK)

    model = payload["model"]
    assert model["last_run_peak"] is False
    # 1M in at 57.15 ₽ + 0.5M out at 171.45 ₽
    assert model["last_run_cost_rub"] == round(57.15 + 85.725, 2)


def test_publishing_is_running_while_only_some_chapters_are_out():
    """A book published chapter by chapter is not «готово» until the last one goes."""
    SessionLocal = _session()
    with SessionLocal() as db:
        _seed(db, status="published_to_dictor", attributed=3, stressed=3, chapter_status="approved")
        db.get(ScriptChapter, "ch-1").status = "published"
        db.commit()
        payload = book_progress(db, BOOK)

    step = next(s for s in payload["steps"] if s["key"] == "publish")
    assert (step["state"], step["detail"]) == ("running", "опубликовано 1 из 3")

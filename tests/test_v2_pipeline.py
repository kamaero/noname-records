"""The v2 book pipeline: steps in order, a heartbeat per chapter, a stop between them.

The run row is the only bookkeeping: what a step already produced is what a rerun
skips, so a run that died can be started again and picks up where it was.
"""
import importlib.util
import uuid
import pathlib
from datetime import timedelta

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Character, LlmUsageLog, ScriptBook, ScriptChapter, ScriptJob, ScriptLog
from app.time_utils import utcnow_naive
from app.v2 import pipeline
from app.v2.models import V2Attribution, V2Run, V2Segment, V2StressMark
from app.v2.pipeline import (
    RunContext,
    active_run,
    create_queued_run,
    expire_stale_runs,
    is_v2_book,
    latest_run,
    run_book_pipeline,
    step_attribute,
    step_cast,
    step_segment,
    step_stress,
    walk_chapters,
)
from app.v2.stress import Resolver

BOOK = "book-pipe"
CHAPTERS = {
    "ch-p1": "Глава 1. Начало\n\nДгарнин сидел в изгибе ветвей.\n\n- Нас стало слишком много, - сказал Дгарнин.\n",
    "ch-p2": "Глава 2. Середина\n\nВетер шумел в кронах.\n",
    "ch-p3": "Глава 3. Конец\n\nНикто не ответил.\n\n- Да.\n",
}


def _factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)


def _seed(SessionLocal, *, characters=True, status="uploaded"):
    with SessionLocal() as db:
        db.add(ScriptBook(id=BOOK, title="Крылья", source_filename="k.txt", source_format="txt", status=status))
        for index, (chapter_id, text) in enumerate(CHAPTERS.items(), start=1):
            db.add(ScriptChapter(id=chapter_id, book_id=BOOK, chapter_index=index, chapter_title=text.splitlines()[0], source_text=text))
        if characters:
            db.add(Character(id="c1", book_id=BOOK, name="Дгарнин", aliases="Дгар", appears_in="1"))
        db.commit()


def _notify_recorder(monkeypatch):
    calls = []
    monkeypatch.setattr("app.services.telegram.notify_book_status_change",
                        lambda db, book, prev, new, detail="": calls.append((prev, new, detail)) or 0)
    return calls


# --- the run over fake steps -------------------------------------------------------------


def test_steps_run_in_pipeline_order_and_the_book_lands_in_author_review(monkeypatch):
    SessionLocal = _factory()
    _seed(SessionLocal)
    notified = _notify_recorder(monkeypatch)
    seen = []

    def fake(name):
        def step(db, book, run, ctx):
            seen.append((name, run.step, run.status))
            return {}
        return step

    run = run_book_pipeline(
        BOOK, steps=("stress", "segment", "attribute"), session_factory=SessionLocal,
        step_functions={k: fake(k) for k in pipeline.STEPS}, provider="p", model="m",
    )
    assert [s[0] for s in seen] == ["segment", "attribute", "stress"]
    assert all(s[1] == s[0] and s[2] == "running" for s in seen)
    assert run.status == "done" and run.step == "stress" and run.error == "" and run.finished_at is not None
    with SessionLocal() as db:
        book = db.get(ScriptBook, BOOK)
        assert (book.status, book.pipeline_mode, book.last_notified_status) == ("author_review", "v2", "")
        assert is_v2_book(book)
        assert latest_run(db, BOOK).id == run.id
        messages = [row.message for row in db.query(ScriptLog).filter(ScriptLog.book_id == BOOK).all()]
        assert any("старт прогона" in m and "segment, attribute, stress" in m for m in messages)
    assert notified == [("uploaded", "author_review", "Пайплайн v2 завершён: все главы размечены.")]


def test_unknown_step_and_unknown_book_are_refused():
    SessionLocal = _factory()
    _seed(SessionLocal)
    with pytest.raises(ValueError, match="неизвестные шаги"):
        run_book_pipeline(BOOK, steps=("segment", "polish"), session_factory=SessionLocal, step_functions={"segment": lambda *a: {}})
    with pytest.raises(ValueError, match="книги"):
        run_book_pipeline("nope", steps=("segment",), session_factory=SessionLocal, step_functions={"segment": lambda *a: {}})


def test_a_stop_between_chapters_ends_the_run_stopped_with_the_written_chapter_kept(monkeypatch):
    SessionLocal = _factory()
    _seed(SessionLocal)
    _notify_recorder(monkeypatch)
    heartbeats = []
    later = []

    def stopping_step(db, book, run, ctx):
        chapters = pipeline._chapters(db, book.id)

        def work(chapter):
            book.stop_requested = "true"  # committed by the heartbeat after this chapter
            return {"stats": {"calls": 1, "prompt_tokens": 10, "completion_tokens": 5}}

        return walk_chapters(db, book, run, ctx, chapters, work)

    run = run_book_pipeline(
        BOOK, steps=("segment", "stress"), session_factory=SessionLocal,
        step_functions={"segment": stopping_step, "stress": lambda db, b, r, c: later.append(1)},
        on_heartbeat=lambda: heartbeats.append(1),
    )
    assert run.status == "stopped" and run.step == "segment"
    assert (run.chapters_total, run.chapters_done, run.calls, run.prompt_tokens, run.completion_tokens) == (3, 1, 1, 10, 5)
    assert "остановлено перед главой 2" in run.error
    assert later == [] and len(heartbeats) >= 2
    with SessionLocal() as db:
        book = db.get(ScriptBook, BOOK)
        assert (book.status, book.stop_requested) == ("stopped", "false")


def test_a_failing_step_fails_the_run_and_the_book_and_says_why(monkeypatch):
    SessionLocal = _factory()
    _seed(SessionLocal)
    notified = _notify_recorder(monkeypatch)

    def boom(db, book, run, ctx):
        raise RuntimeError("модель не ответила")

    run = run_book_pipeline(BOOK, steps=("attribute",), session_factory=SessionLocal, step_functions={"attribute": boom})
    assert run.status == "failed" and run.step == "attribute"
    assert run.error == "RuntimeError: модель не ответила"
    with SessionLocal() as db:
        assert db.get(ScriptBook, BOOK).status == "failed"
    assert notified == [("uploaded", "failed", "v2 · Роли · RuntimeError: модель не ответила")]


def test_a_queued_run_made_by_the_api_is_adopted_by_the_worker():
    SessionLocal = _factory()
    _seed(SessionLocal)
    with SessionLocal() as db:
        queued = create_queued_run(db, BOOK)
        db.commit()
        queued_id = queued.id
    run = run_book_pipeline(BOOK, steps=("segment",), run_id=queued_id, session_factory=SessionLocal,
                            step_functions={"segment": lambda *a: {}})
    assert run.id == queued_id and run.status == "done" and run.started_at is not None
    with SessionLocal() as db:
        assert db.query(V2Run).count() == 1


# --- the real steps ----------------------------------------------------------------------


def _run_step(SessionLocal, step, *, ctx=None):
    with SessionLocal() as db:
        book = db.get(ScriptBook, BOOK)
        run = V2Run(id=f"r-{step.__name__}-{uuid.uuid4()}", book_id=BOOK, status="running", step=step.__name__.replace("step_", ""))
        db.add(run)
        db.commit()
        result = step(db, book, run, ctx or RunContext())
        db.commit()
        db.refresh(run)
        db.expunge(run)
        return result, run


def test_segment_step_cuts_every_chapter_once_and_again_only_when_forced():
    SessionLocal = _factory()
    _seed(SessionLocal)
    result, run = _run_step(SessionLocal, step_segment)
    assert (result["chapters"], result["skipped"], result["segments"]) == (3, 0, 8)
    assert (run.chapters_total, run.chapters_done) == (3, 3)
    with SessionLocal() as db:
        assert db.query(V2Segment).filter(V2Segment.book_id == BOOK).count() == 8

    result, _ = _run_step(SessionLocal, step_segment)
    assert (result["chapters"], result["skipped"]) == (0, 3)
    result, _ = _run_step(SessionLocal, step_segment, ctx=RunContext(force=True))
    assert (result["chapters"], result["skipped"]) == (3, 0)
    with SessionLocal() as db:
        assert db.query(V2Segment).filter(V2Segment.book_id == BOOK).count() == 8


def test_attribute_step_stores_versions_logs_usage_and_skips_done_chapters(monkeypatch):
    SessionLocal = _factory()
    _seed(SessionLocal)
    _run_step(SessionLocal, step_segment)
    asked = []

    def fake_attribute_chapter(units, *, cast, cast_lines, llm, review=True, on_batch=None):
        asked.append((len(units), sorted(cast), review))
        records = [u.as_record(span_start=0, span_end=len(u.text), speaker="Рассказчик", confidence=0.9) for u in units]
        return records, ["одна проблема"], {"calls": 1, "prompt_tokens": 100, "completion_tokens": 20}

    monkeypatch.setattr("app.v2.pipeline.attribute_chapter", fake_attribute_chapter)
    ctx = RunContext(llm=lambda *a: {}, provider="deepseek", model="v4", review=False)
    result, run = _run_step(SessionLocal, step_attribute, ctx=ctx)
    assert (result["chapters"], result["skipped"]) == (3, 0)
    assert result["stats"] == {"calls": 3, "prompt_tokens": 300, "completion_tokens": 60}
    assert result["problems"] == ["глава 1: одна проблема", "глава 2: одна проблема", "глава 3: одна проблема"]
    assert (run.calls, run.prompt_tokens, run.completion_tokens) == (3, 300, 60)
    # headings are never asked about; the cast narrows by appears_in
    assert asked == [(2, ["Дгар", "Дгарнин"], False), (1, [], False), (2, [], False)]
    with SessionLocal() as db:
        rows = db.query(V2Attribution).all()
        assert sorted(r.source for r in rows) == ["llm"] * 5 + ["rule"] * 3
        assert {r.speaker for r in rows} == {"Рассказчик"}
        usage = db.query(LlmUsageLog).filter(LlmUsageLog.book_id == BOOK).order_by(LlmUsageLog.chapter_index).all()
        assert [(u.stage, u.provider, u.model, u.chapter_index, u.prompt_tokens, u.total_tokens) for u in usage] == [
            ("v2_attribution", "deepseek", "v4", 1, 100, 120), ("v2_attribution", "deepseek", "v4", 2, 100, 120), ("v2_attribution", "deepseek", "v4", 3, 100, 120),
        ]

    # a rerun skips what is attributed — and never needs a model client for it
    result, _ = _run_step(SessionLocal, step_attribute, ctx=RunContext(provider="p", model="m"))
    assert (result["chapters"], result["skipped"]) == (0, 3) and len(asked) == 3
    result, _ = _run_step(SessionLocal, step_attribute, ctx=RunContext(llm=lambda *a: {}, force=True))
    assert (result["chapters"], result["skipped"]) == (3, 0)
    with SessionLocal() as db:
        assert db.query(V2Attribution).filter(V2Attribution.version == 2).count() == 8


def test_stress_step_uses_the_resolver_chain_and_skips_stressed_chapters():
    SessionLocal = _factory()
    _seed(SessionLocal)
    _run_step(SessionLocal, step_segment)
    # one author word per chapter, no dictionary: everything else stays unresolved
    resolver = Resolver(author={"дгарнин": 2, "ветвей": 4, "ветер": 1, "никто": 4}, context=lambda text: {})
    result, run = _run_step(SessionLocal, step_stress, ctx=RunContext(resolver=resolver))
    assert (result["chapters"], result["skipped"]) == (3, 0)
    assert result["marks"] == 5 and result["counts"]["author"] == 5 and result["counts"]["unresolved"] > 0
    assert (run.chapters_total, run.chapters_done) == (3, 3)
    with SessionLocal() as db:
        marks = db.query(V2StressMark).all()
        assert {(m.vowel_offset, m.source) for m in marks} == {(1, "author"), (2, "author"), (4, "author")}
    # a chapter that holds marks is done; a rerun skips all three
    result, _ = _run_step(SessionLocal, step_stress, ctx=RunContext(resolver=resolver))
    assert (result["chapters"], result["skipped"]) == (0, 3)
    with SessionLocal() as db:
        assert db.query(V2StressMark).count() == 5


def test_cast_step_reuses_an_existing_cast_and_calls_v1_only_when_there_is_none(monkeypatch):
    SessionLocal = _factory()
    _seed(SessionLocal, characters=True)
    called = []
    monkeypatch.setattr("app.services.char_extraction._run_char_extraction", lambda db, book, job: called.append(job.stage) or 4)
    result, run = _run_step(SessionLocal, step_cast)
    assert result == {"reused": 1, "imported": 0} and called == [] and (run.chapters_total, run.chapters_done) == (1, 1)

    SessionLocal = _factory()
    _seed(SessionLocal, characters=False)
    result, _ = _run_step(SessionLocal, step_cast)
    assert result == {"reused": 0, "imported": 4} and called == ["char_extraction"]
    with SessionLocal() as db:
        job = db.query(ScriptJob).filter(ScriptJob.book_id == BOOK).one()
        assert (job.stage, job.status, job.chapter_index) == ("char_extraction", "done", 0)


def test_cast_extraction_failure_is_reported_and_the_run_goes_on(monkeypatch):
    SessionLocal = _factory()
    _seed(SessionLocal, characters=False)
    _notify_recorder(monkeypatch)

    def boom(db, book, job):
        raise RuntimeError("нет ключа")

    monkeypatch.setattr("app.services.char_extraction._run_char_extraction", boom)
    run = run_book_pipeline(BOOK, steps=("cast", "segment"), session_factory=SessionLocal,
                            step_functions={"cast": step_cast, "segment": step_segment})
    assert run.status == "done"
    with SessionLocal() as db:
        job = db.query(ScriptJob).filter(ScriptJob.book_id == BOOK).one()
        assert job.status == "failed" and job.error_message == "RuntimeError: нет ключа"
        assert db.query(V2Segment).count() == 8
        warnings = [r.message for r in db.query(ScriptLog).filter(ScriptLog.level == "warning").all()]
        assert any("извлечение не удалось" in m for m in warnings)


# --- run rows the API reads --------------------------------------------------------------


def test_active_and_stale_runs():
    SessionLocal = _factory()
    _seed(SessionLocal)
    with SessionLocal() as db:
        assert active_run(db, BOOK) is None
        run = create_queued_run(db, BOOK)
        db.commit()
        assert active_run(db, BOOK).id == run.id
        assert expire_stale_runs(db, BOOK) == 0

        run.status = "running"
        run.updated_at = utcnow_naive() - timedelta(hours=3)
        db.commit()
        assert expire_stale_runs(db, BOOK) == 1
        db.commit()
        assert active_run(db, BOOK) is None
        assert latest_run(db, BOOK).status == "failed" and "heartbeat lost" in latest_run(db, BOOK).error


# --- revision 0009 -------------------------------------------------------------------------


def _load_revision():
    path = pathlib.Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0009_v2_runs.py"
    spec = importlib.util.spec_from_file_location("rev_0009_v2_runs", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_revision_0009_creates_v2_runs_once_and_tolerates_the_table_existing(tmp_path):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    module = _load_revision()
    assert (module.revision, module.down_revision) == ("0009_v2_runs", "0008_v2_segments")
    engine = create_engine(f"sqlite:///{tmp_path / 'm.db'}")

    def apply(fn):
        with engine.begin() as conn:
            with Operations.context(MigrationContext.configure(conn)):
                fn()

    apply(module.upgrade)
    apply(module.upgrade)  # guarded: a second run is a no-op
    columns = {c["name"] for c in inspect(engine).get_columns("v2_runs")}
    assert columns == {"id", "book_id", "status", "step", "chapters_total", "chapters_done", "calls",
                       "prompt_tokens", "completion_tokens", "error", "started_at", "updated_at", "finished_at"}
    assert {i["name"] for i in inspect(engine).get_indexes("v2_runs")} == {"ix_v2_runs_book", "ix_v2_runs_book_status"}
    apply(module.downgrade)
    assert "v2_runs" not in inspect(engine).get_table_names()
    apply(module.downgrade)  # and so is a second downgrade

    # a scratch copy that got the table from the model first is left alone
    V2Run.metadata.create_all(bind=engine, tables=[V2Run.__table__])
    apply(module.upgrade)
    assert "v2_runs" in inspect(engine).get_table_names()


def test_a_finished_run_lowers_a_stop_flag_nobody_lowered():
    """A stop asked for late, after the last chapter, must not outlive the run.

    Left standing, the flag made every screen say «останавливается» about a book that
    had finished hours ago, and the hub offered «продолжить» for a run that was done.
    """
    SessionLocal = _factory()
    _seed(SessionLocal)
    with SessionLocal() as db:
        db.get(ScriptBook, BOOK).stop_requested = "true"
        db.commit()

    run = run_book_pipeline(
        BOOK, steps=("segment",), session_factory=SessionLocal,
        step_functions={"segment": lambda db, book, r, ctx: {}},
    )

    assert run.status == "done"
    with SessionLocal() as db:
        book = db.get(ScriptBook, BOOK)
        assert (book.stop_requested, book.status) == ("false", "author_review")


def test_the_run_uses_the_model_the_book_was_set_to():
    """The hub's choice must reach the model, not just be stored next to it."""
    SessionLocal = _factory()
    _seed(SessionLocal)
    with SessionLocal() as db:
        book = db.get(ScriptBook, BOOK)
        book.llm_provider, book.llm_model = "routerai", "meta/muse-spark-1.3-contributor"
        db.commit()

    seen = {}

    def step(db, book, run, ctx):
        seen["provider"], seen["model"] = ctx.provider, ctx.model
        return {}

    run_book_pipeline(BOOK, steps=("segment",), session_factory=SessionLocal, step_functions={"segment": step})

    assert seen == {"provider": "routerai", "model": "meta/muse-spark-1.3-contributor"}


def test_a_catalogued_model_brings_its_own_reasoning_knob(monkeypatch):
    """Muse Spark cannot switch reasoning off; it must not be sent DeepSeek's knob."""
    from app.v2 import pipeline as pipeline_module

    captured = {}
    monkeypatch.setattr(
        "app.v2.llm.make_llm",
        lambda provider, model, *, extra_body=None: captured.update(provider=provider, model=model, extra_body=extra_body) or (lambda *a: {}),
    )

    ctx = pipeline_module.RunContext(provider="routerai", model="meta/muse-spark-1.3-contributor", thinking="off")
    ctx.get_llm()
    assert captured["extra_body"] == {"reasoning": {"effort": "low"}}

    ctx = pipeline_module.RunContext(provider="deepseek", model="deepseek-v4-pro", thinking="off")
    ctx.get_llm()
    assert captured["extra_body"] == {"thinking": {"type": "disabled"}}

"""Закрыли программу посреди разметки: после запуска книга свободна, новый прогон создаётся.

Пропуск готовых глав при повторе закреплён в test_v2_pipeline
(test_attribute_step_stores_versions_logs_usage_and_skips_done_chapters)."""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import ScriptBook
from app.seat import mark_interrupted_runs
from app.v2.pipeline import active_run, create_queued_run, latest_run


def test_after_an_interruption_the_book_is_free_for_a_new_run():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        db.add(ScriptBook(id="b1", title="К", source_filename="k.txt", source_format="txt", status="processing"))
        run = create_queued_run(db, "b1")
        run.status = "running"
        db.commit()
        assert active_run(db, "b1") is not None
        mark_interrupted_runs(db)
        assert active_run(db, "b1") is None
        assert latest_run(db, "b1").status == "failed" and db.get(ScriptBook, "b1").status == "stopped"
        second = create_queued_run(db, "b1")
        db.commit()
        assert active_run(db, "b1").id == second.id

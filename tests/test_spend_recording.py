"""Каждый платный вызов оставляет строку журнала: текстовые модели, распознавание, музыка."""
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import SpendEntry
from app.pipeline import llm_client
from app.services import spend


@pytest.fixture()
def factory(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    f = sessionmaker(bind=engine, autoflush=False)
    monkeypatch.setattr(spend, "SessionLocal", f)
    return f


class Resp:
    status_code = 200
    headers = {}

    def __init__(self, usage):
        self.payload = {"choices": [{"message": {"content": "{}"}}], **({"usage": usage} if usage else {})}
        self.text = "{}"

    def json(self):
        return self.payload


def _rows(factory):
    with factory() as db:
        return db.query(SpendEntry).all()


def _call(monkeypatch, usage, base="https://routerai.ru/api/v1", model="vendor/m"):
    monkeypatch.setattr(llm_client.requests, "post", lambda *a, **kw: Resp(usage))
    llm_client.call_chat(base, "k", model, "s", "u", mode="openai")


def test_a_call_inside_a_step_records_its_step_book_and_tokens(factory, monkeypatch):
    with spend.context("sound", book_id="b1", run_id="r1"):
        _call(monkeypatch, {"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150})
    [row] = _rows(factory)
    assert (row.step, row.book_id, row.run_id, row.provider, row.model) == ("sound", "b1", "r1", "routerai", "vendor/m")
    assert (row.unit, row.input_units, row.output_units) == ("tokens", 120, 30)


def test_a_call_outside_any_step_is_still_counted(factory, monkeypatch):
    _call(monkeypatch, {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})
    assert [r.step for r in _rows(factory)] == ["other"]


def test_an_answer_without_usage_is_unknown_not_free(factory, monkeypatch):
    _call(monkeypatch, None, base="https://api.deepseek.com", model="deepseek-v4-pro")
    [row] = _rows(factory)
    assert row.rub is None and row.price_known is False


def test_a_broken_ledger_does_not_break_the_call(monkeypatch):
    def boom():
        raise RuntimeError("база недоступна")
    monkeypatch.setattr(spend, "SessionLocal", boom)
    _call(monkeypatch, {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})  # не бросает


def test_a_worker_thread_sees_the_step_it_was_started_for(factory, monkeypatch):
    monkeypatch.setattr(llm_client.requests, "post", lambda *a, **kw: Resp({"prompt_tokens": 1, "completion_tokens": 1}))

    def work():
        with spend.context("consilium_reader_2", book_id="b2"):
            llm_client.call_chat("https://openrouter.ai/api/v1", "k", "x/y", "s", "u", mode="openai")
    with ThreadPoolExecutor(max_workers=1) as pool:  # поток другой; одна связь SQLite в памяти — не для двух писателей
        list(pool.map(lambda _: work(), range(2)))
    assert {(r.step, r.book_id, r.provider) for r in _rows(factory)} == {("consilium_reader_2", "b2", "openrouter")}


def test_record_call_writes_seconds_for_audio(factory):
    spend.record_call("openai", "whisper-1", unit="seconds", input_units=95, step="asr", book_id="b1", chapter_id="c1")
    [row] = _rows(factory)
    assert (row.step, row.unit, row.input_units, row.chapter_id) == ("asr", "seconds", 95, "c1")

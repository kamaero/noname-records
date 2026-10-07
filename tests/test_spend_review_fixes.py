"""Находки ревью 2б: учёт без дыр, лимит без двойной траты остатка, честные сметы."""
import json
import time
from datetime import datetime

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import SpendEntry
from app.pipeline import llm_client
from app.services import spend
from app.services.studio_settings import studio_settings


@pytest.fixture()
def factory(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    from app.v2.store import ensure_v2_tables

    ensure_v2_tables(engine)
    f = sessionmaker(bind=engine, autoflush=False)
    with f() as db:
        studio_settings(db).usd_rub_rate = 100.0
        db.commit()
    monkeypatch.setattr(spend, "SessionLocal", f)
    return f


def _rows(factory):
    with factory() as db:
        return db.query(SpendEntry).all()


# --- C1: чужая транзакция записи не теряет строку и не держит вызов ---------------------

def test_a_held_write_lock_neither_loses_the_row_nor_blocks_the_caller(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'studio.db'}"
    engine = create_engine(url, connect_args={"timeout": 5})
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        studio_settings(db).usd_rub_rate = 100.0
        db.commit()
    ledger = sessionmaker(bind=create_engine(url, connect_args={"timeout": 5}))
    monkeypatch.setattr(spend, "SessionLocal", ledger)
    monkeypatch.setattr(spend, "LOCK_WAIT_MS", 50)

    with sessionmaker(bind=engine)() as caller:
        caller.execute(text("create table hold (x integer)"))
        caller.execute(text("insert into hold values (1)"))  # транзакция записи открыта
        started = time.monotonic()
        spend.record_call("deepseek", "deepseek-v4-pro", unit="tokens", input_units=10, output_units=5, step="asr")
        assert time.monotonic() - started < 2  # не 30 секунд ожидания блокировки
        caller.commit()

    with ledger() as db:  # следующее обращение к журналу забирает отложенную строку
        summary = spend.month_summary(db, spend.month_key(datetime.utcnow()))
    assert summary["by_step"]["asr"]["calls"] == 1
    with ledger() as db:
        assert db.query(SpendEntry).count() == 1


# --- I1: два запуска не делят один остаток ---------------------------------------------

NOW = datetime(2026, 10, 10, 12)


def test_a_started_run_holds_its_estimate_until_it_spends_it(factory):
    with factory() as db:
        studio_settings(db).monthly_limit_rub = 100
        db.commit()
        first = spend.check_start(db, estimate_rub=80, unknown_price=False, override=False, is_admin=False,
                                  actor_uid="u", what="разметка книги А", now=NOW)
        second = spend.check_start(db, estimate_rub=80, unknown_price=False, override=False, is_admin=False,
                                   actor_uid="u", what="звук книги Б", now=NOW)
    assert first.allowed and not second.allowed
    assert second.left_rub == pytest.approx(20)


def test_spending_after_the_start_releases_the_hold_instead_of_counting_twice(factory):
    with factory() as db:
        studio_settings(db).monthly_limit_rub = 200
        db.commit()
        assert spend.check_start(db, estimate_rub=66, unknown_price=False, override=False, is_admin=False,
                                 actor_uid="u", what="А", now=NOW).allowed
        spend.record(db, step="attribution", provider="deepseek", model="deepseek-v4-pro", unit="tokens",
                     input_units=1_000_000, output_units=0, now=datetime(2026, 10, 10, 12, 30))  # 66 ₽, не пик
        db.commit()
        decision = spend.check_start(db, estimate_rub=100, unknown_price=False, override=False, is_admin=False,
                                     actor_uid="u", what="Б", now=datetime(2026, 10, 10, 13))
    assert decision.allowed and decision.left_rub == pytest.approx(134)


# --- I2/I3: каждая оплаченная генерация — строка, неполный usage — не ноль ----------------

class Resp:
    status_code = 200
    headers = {}
    text = "{}"

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


def _openai(monkeypatch, payload):
    monkeypatch.setattr(llm_client.requests, "post", lambda *a, **kw: Resp(payload))
    return llm_client.call_chat("https://api.deepseek.com/v1", "k", "deepseek-v4-pro", "s", "u", mode="openai")


def test_only_total_tokens_is_unknown_not_free(factory, monkeypatch):
    _openai(monkeypatch, {"choices": [{"message": {"content": "{}"}}], "usage": {"total_tokens": 150}})
    (row,) = _rows(factory)
    assert row.rub is None and row.price_known is False


def test_a_paid_answer_that_fails_to_parse_is_still_recorded(factory, monkeypatch):
    with pytest.raises(Exception):
        _openai(monkeypatch, {"choices": [{"message": {"content": None}}],
                              "usage": {"prompt_tokens": 1000, "completion_tokens": 200}})
    (row,) = _rows(factory)
    assert (row.input_units, row.output_units) == (1000, 200) and row.rub is not None


def _anthropic(monkeypatch, result):
    monkeypatch.setattr(llm_client, "_post_stream_with_retry", lambda *a, **kw: result)
    return llm_client.call_chat("https://api.anthropic.com", "k", "claude-sonnet-4-6", "s", "u", mode="anthropic",
                                json_schema={"type": "object"})


def test_a_truncated_anthropic_answer_records_its_tokens(factory, monkeypatch):
    with pytest.raises(RuntimeError, match="truncated"):
        _anthropic(monkeypatch, {"tool_inputs": [], "text": "", "stop_reason": "max_tokens",
                                 "usage": {"input_tokens": 3000, "output_tokens": 16000}})
    (row,) = _rows(factory)
    assert (row.input_units, row.output_units) == (3000, 16000)


def test_an_anthropic_answer_without_usage_is_unknown_not_a_crash(factory, monkeypatch):
    out = _anthropic(monkeypatch, {"tool_inputs": [{"a": 1}], "text": "", "stop_reason": "end_turn", "usage": {}})
    assert out["usage"]["estimated"] is True
    (row,) = _rows(factory)
    assert row.rub is None


def test_a_stream_broken_after_it_started_leaves_an_unknown_row(factory, monkeypatch):
    class Started:
        status_code = 200

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    calls = {"n": 0}

    def post(*a, **kw):
        calls["n"] += 1
        return Started()

    def consume(resp, parse, deadline):
        if calls["n"] == 1:
            raise llm_client.requests.exceptions.ChunkedEncodingError("Response ended prematurely")
        return {"tool_inputs": [{"a": 1}], "text": "", "stop_reason": "end_turn",
                "usage": {"input_tokens": 10, "output_tokens": 5}}

    monkeypatch.setattr(llm_client.requests, "post", post)
    monkeypatch.setattr(llm_client, "_consume_with_wall_deadline", consume)
    monkeypatch.setattr(llm_client.time, "sleep", lambda s: None)
    llm_client.call_chat("https://api.anthropic.com", "k", "claude-sonnet-4-6", "s", "u", mode="anthropic",
                         json_schema={"type": "object"})
    rows = sorted(_rows(factory), key=lambda r: r.input_units)
    assert len(rows) == 2
    assert rows[0].rub is None  # оборванная попытка: могла быть оплачена, цена неизвестна
    assert rows[1].input_units == 10


# --- I4: пиковый тариф DeepSeek ------------------------------------------------------------

def test_deepseek_in_its_peak_window_costs_double(factory):
    peak = datetime(2026, 10, 7, 7, 0)       # среда, 07:00 UTC — пик
    calm = datetime(2026, 10, 7, 12, 0)      # среда, 12:00 UTC — обычный тариф
    with factory() as db:
        a = spend.record(db, step="attribution", provider="deepseek", model="deepseek-v4-pro", unit="tokens",
                         input_units=1_000_000, output_units=0, now=peak)
        b = spend.record(db, step="attribution", provider="deepseek", model="deepseek-v4-pro", unit="tokens",
                         input_units=1_000_000, output_units=0, now=calm)
    assert a.rub == pytest.approx(2 * b.rub)


# --- I5/I6: сметы считают то, что движок сделает -----------------------------------------

def test_forced_cast_rerun_is_priced_even_when_characters_exist(factory):
    from app.models import Character, ScriptBook, ScriptChapter

    with factory() as db:
        book = ScriptBook(id="b1", title="Книга", source_filename="k.txt", source_format="txt")
        db.add(book)
        db.add(ScriptChapter(id="c1", book_id="b1", chapter_index=1, chapter_title="1", source_text="слово " * 10_000))
        db.add(Character(book_id="b1", name="Герой"))
        db.flush()
        plain, _ = spend.estimate_markup(db, book, ["cast"])
        forced, _ = spend.estimate_markup(db, book, ["cast"], force=True)
    assert plain == 0 and forced > 0


def test_ambient_estimate_includes_the_scene_text(factory, monkeypatch):
    from app.services import step_models

    monkeypatch.setattr(step_models, "step_model", lambda step: {"ambient_audio": ("elevenlabs", "music_v1"),
                                                                 "ambient_text": ("deepseek", "deepseek-v4-pro")}[step])
    with factory() as db:
        db.add(spend.ModelPrice(provider="elevenlabs", model="music_v1", unit="seconds", price_unit=10.0,
                                currency="RUB"))
        db.flush()
        music_only, _ = spend.estimate_ambient(db, [{"seconds": 60, "prompt": "готовый"}])
        with_text, unknown = spend.estimate_ambient(db, [{"seconds": 60}])
    assert music_only == pytest.approx(10.0)
    assert with_text > music_only and unknown is False


# --- I7: предупреждение о неизвестной цене доходит до ответа ---------------------------------

def test_a_usd_price_with_zero_rate_counts_as_unknown(factory, monkeypatch):
    from app.services import step_models

    monkeypatch.setattr(step_models, "step_model", lambda step: ("openai", "whisper-1"))
    with factory() as db:
        assert spend.unknown_for(db, ["asr"]) is False
        studio_settings(db).usd_rub_rate = 0
        db.flush()
        assert spend.unknown_for(db, ["asr"]) is True


def test_the_gate_hands_the_unknown_price_warning_to_the_response(factory):
    from types import SimpleNamespace

    from app.api import _spend_gate

    request = SimpleNamespace(query_params={}, state=SimpleNamespace())
    _spend_gate.session_roles = lambda r: ["admin"]
    _spend_gate.is_owner_telegram = lambda r: False
    _spend_gate.session_payload = lambda r: {"uid": "u"}
    with factory() as db:
        assert _spend_gate.limit_gate(db, request, estimate_rub=0, unknown_price=True, what="звук") is None
    body = _spend_gate.with_warning(request, {"ok": True})
    assert "не войдут в лимит" in body["spend_warning"]


# --- I8: неотправленное письмо не помечается отправленным ---------------------------------

def test_an_unsent_threshold_letter_is_tried_again(factory):
    now = datetime(2026, 10, 10, 12)
    with factory() as db:
        studio_settings(db).monthly_limit_rub = 70
        spend.record(db, step="attribution", provider="deepseek", model="deepseek-v4-pro", unit="tokens",
                     input_units=1_000_000, output_units=0, now=now)
        assert spend.warn_thresholds(db, now, lambda text: False) == 0
        sent = []
        assert spend.warn_thresholds(db, now, lambda text: sent.append(text) or True) == 1
    assert sent


# --- I11: сводка месяца не тянет все строки в память ----------------------------------------

def test_month_summary_loads_only_the_recent_rows(factory):
    now = datetime(2026, 10, 10, 12)
    loaded = []
    with factory() as db:
        for _ in range(120):
            spend.record(db, step="sound", provider="deepseek", model="deepseek-v4-pro", unit="tokens",
                         input_units=1000, output_units=0, now=now)
        db.commit()
        db.expunge_all()
        def on_load(target, ctx):
            loaded.append(1)

        event.listen(SpendEntry, "load", on_load)
        try:
            summary = spend.month_summary(db, "2026-10")
        finally:
            event.remove(SpendEntry, "load", on_load)
    assert summary["by_step"]["sound"]["calls"] == 120
    assert len(summary["recent"]) == 50
    assert len(loaded) <= 50


# --- пересмотр: книга в последних вызовах; бесплатный прогон при исчерпанном лимите ---------

def test_recent_calls_name_the_book(factory):
    from app.models import ScriptBook

    with factory() as db:
        db.add(ScriptBook(id="b1", title="Книга", display_title="Автор - «Книга»", source_filename="k.txt",
                          source_format="txt"))
        spend.record(db, step="sound", provider="deepseek", model="deepseek-v4-pro", unit="tokens",
                     input_units=10, output_units=1, book_id="b1", now=NOW)
        spend.record(db, step="sound", provider="deepseek", model="deepseek-v4-pro", unit="tokens",
                     input_units=10, output_units=1, book_id="gone", now=NOW)
        db.commit()
        recent = spend.month_summary(db, "2026-10")["recent"]
    titles = sorted(row["book_title"] for row in recent)
    assert titles == ["", "Автор - «Книга»"]


def test_a_free_run_starts_even_when_the_month_is_spent(factory):
    with factory() as db:
        studio_settings(db).monthly_limit_rub = 50
        spend.record(db, step="attribution", provider="deepseek", model="deepseek-v4-pro", unit="tokens",
                     input_units=1_000_000, output_units=0, now=NOW)  # 66 ₽ > 50
        db.commit()
        free = spend.check_start(db, estimate_rub=0, unknown_price=False, override=False, is_admin=False,
                                 actor_uid="u", what="ударения", now=NOW)
        unpriced = spend.check_start(db, estimate_rub=0, unknown_price=True, override=False, is_admin=False,
                                     actor_uid="u", what="модель без цены", now=NOW)
    assert free.allowed
    assert not unpriced.allowed  # нулевая смета из-за неизвестной цены — не бесплатно

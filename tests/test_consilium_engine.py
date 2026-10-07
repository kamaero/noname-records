"""Движок консилиума: артефакты, состояние книги, смета. База в памяти, каталог временный."""
import json
import time

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  — регистрация таблиц до create_all
import app.v2.models  # noqa: F401
from app.db import Base
from tests.consilium_book import BOOK, build_book


@pytest.fixture()
def session_factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


@pytest.fixture()
def book(session_factory):
    with session_factory() as db:
        build_book(db)
    return session_factory


def test_book_state_carries_markup_cast_and_where(book):
    from app.services.consilium_engine import load_book_state

    with book() as db:
        state = load_book_state(db, BOOK)
    first = state.chapters[0]
    assert [c.index for c in state.chapters] == [1, 2]
    assert first.current == {0: "Гамук", 1: "Тупуг", 2: "Рассказчик"}
    assert first.where[0] == ("c1:00000", 0, 7)
    assert first.cast_lines == ["- Гамук — демон", "- Тупуг — демон"]
    assert state.canon("гамук") == "Гамук" and state.canon("Рассказчик") == "Рассказчик"
    assert state.canon("Никто") == ""


def test_answers_survive_only_while_the_text_is_the_same(book, tmp_path):
    from app.services.consilium_engine import load_answers, load_book_state, save_answers

    with book() as db:
        state = load_book_state(db, BOOK)
    chapter = state.chapters[0]
    save_answers(BOOK, chapter, "opus", "m", {0: "Гамук", 1: "Тупуг", 2: "Рассказчик"}, True, root=str(tmp_path))
    assert load_answers(BOOK, chapter, "opus", root=str(tmp_path)) == {0: "Гамук", 1: "Тупуг", 2: "Рассказчик"}

    chapter.paragraphs[1] = (1, "— Куда же?")
    assert load_answers(BOOK, chapter, "opus", root=str(tmp_path)) is None

    save_answers(BOOK, chapter, "sol", "m", {0: "Гамук"}, False, root=str(tmp_path))
    assert load_answers(BOOK, chapter, "sol", root=str(tmp_path)) is None
    raw = json.loads(open(tmp_path / BOOK / "consilium" / "ch01-sol.json", encoding="utf-8").read())
    assert raw["complete"] is False and raw["reader"] == "sol" and raw["chapter"] == 1


def test_fingerprint_does_not_collide_across_the_number_text_boundary():
    from app.services.consilium_engine import text_fingerprint

    assert text_fingerprint([(1, "2x")]) != text_fingerprint([(12, "x")])


def test_findings_for_uses_the_same_rules_as_the_importer(book):
    from app.services.consilium_engine import findings_for, load_book_state

    with book() as db:
        state = load_book_state(db, BOOK)
    answers = {"opus": {(1, 0): "Гамук", (1, 1): "Гамук", (1, 2): "Рассказчик"},
               "sol": {(1, 0): "Гамук", (1, 1): "Гамук", (1, 2): "Рассказчик"}}
    found = findings_for(state, answers)
    assert list(found) == [(1, 1, 0, 7)]
    assert found[(1, 1, 0, 7)]["kind"] == "wrong_voice"
    assert found[(1, 1, 0, 7)]["segment_id"] == "c1:00001"


def _counting_name_search(monkeypatch):
    import app.services.consilium_engine as engine

    calls = []
    real = engine.find_name_in_narration

    def counted(parts, names):
        calls.append(list(parts))
        return real(parts, names)

    monkeypatch.setattr(engine, "find_name_in_narration", counted)
    return calls


def test_name_in_narration_is_searched_only_for_an_agreed_dispute(book, monkeypatch):
    from app.services.consilium_engine import findings_for, load_book_state

    calls = _counting_name_search(monkeypatch)
    with book() as db:
        state = load_book_state(db, BOOK)
    assert calls == []  # смета не ищет имён по всей книге
    # Спорит с разметкой согласно только абзац 1:0 (Гамук → оба «Тупуг»); остальное совпадает.
    answers = {"opus": {(1, 0): "Тупуг", (1, 1): "Тупуг", (1, 2): "Рассказчик", (2, 0): "Тупуг"},
               "sol": {(1, 0): "Тупуг", (1, 1): "Тупуг", (1, 2): "Рассказчик", (2, 0): "Тупуг"}}
    found = findings_for(state, answers)
    assert list(found) == [(1, 0, 0, 7)]
    assert found[(1, 0, 0, 7)]["kind"] == "wrong_voice"  # ремарка называет того, кто в разметке
    assert len(calls) == 1 and "сказал Гамук" in calls[0][0]


def test_identity_play_only_when_narration_names_someone_else_than_the_markup(session_factory):
    """Правило `compare`: имя в ремарке есть и оно не то, что в разметке, — «игра личностей»;
    ремарка называет того же, кто в разметке, или никого — «чужой голос»."""
    from app.services.consilium_engine import findings_for, load_book_state

    chapters = {1: [("— Идём, — сказал Гамук.", [(0, 7, "Тупуг"), (7, 23, "Рассказчик")]),
                    ("— Идём, — сказал он.", [(0, 7, "Тупуг"), (7, 20, "Рассказчик")]),
                    ("— Идём, — сказал Тупуг.", [(0, 7, "Тупуг"), (7, 23, "Рассказчик")])]}
    with session_factory() as db:
        build_book(db, chapters=chapters)
        state = load_book_state(db, BOOK)
    both = {(1, 0): "Гамук", (1, 1): "Гамук", (1, 2): "Гамук"}
    found = findings_for(state, {"opus": dict(both), "sol": dict(both)})
    assert {key: item["kind"] for key, item in found.items()} == {
        (1, 0, 0, 7): "identity_play", (1, 1, 0, 7): "wrong_voice", (1, 2, 0, 7): "wrong_voice"}
    assert state.chapters[0].narration[0].strip() == "— сказал Гамук."


def test_estimate_accepts_a_loaded_state(book, tmp_path, monkeypatch):
    import app.services.consilium_engine as engine

    with book() as db:
        state = engine.load_book_state(db, BOOK)
        expected = engine.estimate(db, BOOK, root=str(tmp_path))
        monkeypatch.setattr(engine, "load_book_state", lambda *_a, **_k: pytest.fail("state loaded twice"))
        assert engine.estimate(db, BOOK, root=str(tmp_path), state=state) == expected


def test_estimate_counts_unread_chapters_and_places(book, tmp_path):
    from app.services.consilium_engine import estimate, load_book_state, save_answers

    with book() as db:
        state = load_book_state(db, BOOK)
        for reader in ("opus", "sol"):
            save_answers(BOOK, state.chapters[0], reader, "m", {0: "Гамук", 1: "Гамук", 2: "Рассказчик"},
                         True, root=str(tmp_path))
        out = estimate(db, BOOK, root=str(tmp_path))
    assert out["chapters_total"] == 2
    assert out["chapters_to_read"] == {"recheck": 1, "reread": 2}
    assert out["arbiter_places"]["recheck"] >= 1         # найдено на прочитанной главе
    assert out["estimate_rub"]["recheck"] <= out["estimate_rub"]["reread"]  # на крошечной книге обе — 10 ₽
    assert out["estimate_rub"]["reread"] % 10 == 0


def _run_row(session_factory, mode="recheck"):
    from app.models import BackgroundRun
    from app.time_utils import utcnow_naive

    with session_factory() as db:
        row = BackgroundRun(id="run1", run_key=f"consilium:{BOOK}", job_kind="consilium",
                            entity_type="script_book", entity_id=BOOK, status="running",
                            meta_json=json.dumps({"mode": mode}), created_at=utcnow_naive(),
                            updated_at=utcnow_naive(), started_at=utcnow_naive())
        db.add(row)
        db.commit()
    return "run1"


class FakeModels:
    """Чтецы говорят «Гамук» там, где в разметке Тупуг (гл.1 абз.1); арбитр подтверждает цитатой."""

    def __init__(self):
        self.reader_calls = 0
        self.arbiter_calls = 0

    def __call__(self, model, system, user, schema):
        if "lines" in schema.get("required", []):
            self.reader_calls += 1
            numbers = [line.split()[0] for line in user.split("АБЗАЦЫ:\n", 1)[1].splitlines()]
            speakers = {"#00000": "Гамук", "#00001": "Гамук", "#00002": "Рассказчик"}
            if "Стой" in user:
                speakers = {"#00000": "Тупуг", "#00001": "Рассказчик"}
            return {"lines": [{"id": n, "speaker": speakers.get(n, "Рассказчик"), "confidence": 1} for n in numbers]}
        self.arbiter_calls += 1
        return {"speaker": "Гамук", "evidence_kind": "alternation", "evidence_para": 0,
                "evidence_quote": "сказал Гамук", "reason": "чередование"}


def _credits(values):
    it = iter(values)
    last = [None]
    def read():
        try:
            last[0] = next(it)
        except StopIteration:
            pass
        return last[0]
    return read


def test_a_full_run_reads_arbitrates_and_saves(book, tmp_path):
    from app.models import BackgroundRun, ConsiliumFinding
    from app.services.consilium_engine import run_consilium

    models = FakeModels()
    run_id = _run_row(book, "reread")
    notes = []
    out = run_consilium(session_factory=book, book_id=BOOK, run_id=run_id, mode="reread", ask=models,
                        read_credits=_credits([1000.0, 990.0]), notify=notes.append, root=str(tmp_path),
                        arbiter_workers=1)
    assert out["status"] == "done" and out["added"] == 1 and out["arbitrated"] == 1
    assert models.reader_calls == 4 and models.arbiter_calls == 1
    with book() as db:
        finding = db.query(ConsiliumFinding).one()
        assert (finding.ordinal, finding.arbiter_verdict, finding.arbiter_speaker) == (1, "change", "Гамук")
        run = db.get(BackgroundRun, run_id)
        meta = json.loads(run.meta_json)
        assert run.status == "done" and meta["phase"] == "done" and meta["result"]["added"] == 1
    assert notes and "Книга" in notes[0]


def test_recheck_does_not_call_readers_or_arbiter_when_nothing_changed(book, tmp_path):
    from app.services.consilium_engine import run_consilium

    first = FakeModels()
    run_consilium(session_factory=book, book_id=BOOK, run_id=_run_row(book, "reread"), mode="reread",
                  ask=first, read_credits=_credits([1000.0]), root=str(tmp_path), arbiter_workers=1)
    with book() as db:
        from app.models import BackgroundRun
        db.query(BackgroundRun).delete()
        db.commit()
    second = FakeModels()
    out = run_consilium(session_factory=book, book_id=BOOK, run_id=_run_row(book), mode="recheck",
                        ask=second, read_credits=_credits([1000.0]), root=str(tmp_path), arbiter_workers=1)
    assert (second.reader_calls, second.arbiter_calls) == (0, 0)
    assert (out["added"], out["updated"], out["gone"]) == (0, 0, 0)


def test_a_changed_chapter_is_read_again_and_a_vanished_dispute_is_gone(book, tmp_path):
    from app.models import BackgroundRun, ConsiliumFinding
    from app.services.consilium_engine import run_consilium
    from app.v2.attribution_ops import reassign_segment

    run_consilium(session_factory=book, book_id=BOOK, run_id=_run_row(book, "reread"), mode="reread",
                  ask=FakeModels(), read_credits=_credits([1000.0]), root=str(tmp_path), arbiter_workers=1)
    with book() as db:
        db.query(BackgroundRun).delete()
        # человек поправил разметку так, как говорили чтецы
        reassign_segment(db, segment_id="c1:00001", spans=[{"start": 0, "end": 7, "speaker": "Гамук"}],
                         actor_uid="u1")
        db.commit()
    again = FakeModels()
    out = run_consilium(session_factory=book, book_id=BOOK, run_id=_run_row(book), mode="recheck",
                        ask=again, read_credits=_credits([1000.0]), root=str(tmp_path), arbiter_workers=1)
    assert again.reader_calls == 0          # текст не менялся — разметка не повод перечитывать
    assert out["gone"] == 1
    with book() as db:
        assert db.query(ConsiliumFinding).one().status == "gone"


def test_stop_request_stops_between_chunks_and_keeps_what_was_read(book, tmp_path):
    from app.models import BackgroundRun
    from app.services.consilium_engine import request_stop, run_consilium

    run_id = _run_row(book, "reread")

    import threading
    one_writer = threading.Lock()  # база теста — одно соединение на все потоки; двое чтецов не пишут разом

    class StopAfterFirst(FakeModels):
        def __call__(self, model, system, user, schema):
            result = super().__call__(model, system, user, schema)
            with one_writer, book() as db:
                request_stop(db, BOOK)
                db.commit()
            return result

    out = run_consilium(session_factory=book, book_id=BOOK, run_id=run_id, mode="reread",
                        ask=StopAfterFirst(), read_credits=_credits([1000.0]), root=str(tmp_path),
                        arbiter_workers=1)
    assert out["status"] == "stopped" and out["reason"] == "stopped_by_user"
    with book() as db:
        run = db.get(BackgroundRun, run_id)
        assert run.status == "stopped" and "остановлен вручную" in (run.error_message or "")


def test_over_budget_stops_the_run(book, tmp_path):
    from app.services.consilium_engine import run_consilium

    out = run_consilium(session_factory=book, book_id=BOOK, run_id=_run_row(book, "reread"), mode="reread",
                        ask=FakeModels(), read_credits=_credits([1000.0, 1.0]), root=str(tmp_path),
                        arbiter_workers=1)
    assert out["status"] == "stopped" and out["reason"] in ("over_budget", "no_credits")


def test_latest_run_payload(book, tmp_path):
    from app.services.consilium_engine import latest_run, run_consilium

    run_consilium(session_factory=book, book_id=BOOK, run_id=_run_row(book, "reread"), mode="reread",
                  ask=FakeModels(), read_credits=_credits([1000.0, 995.0]), root=str(tmp_path), arbiter_workers=1)
    with book() as db:
        payload = latest_run(db, BOOK)
    assert payload["status"] == "done" and payload["mode"] == "reread"
    assert payload["chapters_total"] == 2 and payload["spent_rub"] == 5.0


def test_stop_during_the_arbiter_does_not_pay_for_the_remaining_places(session_factory, tmp_path):
    from app.services.consilium_engine import request_stop, run_consilium

    disputed = [("— Куда?", [(0, 7, "Тупуг")]), ("— Сюда?", [(0, 7, "Тупуг")]), ("— Там?", [(0, 6, "Тупуг")])]
    with session_factory() as db:
        build_book(db, chapters={1: disputed, 2: disputed})
    run_id = _run_row(session_factory, "reread")

    class StopAtFirstVerdict(FakeModels):
        def __call__(self, model, system, user, schema):
            result = super().__call__(model, system, user, schema)
            if self.arbiter_calls > 1:
                time.sleep(0.2)                 # настоящий арбитр думает секунды, чекпойнт — миллисекунды
            if self.arbiter_calls == 1:
                with session_factory() as db:
                    request_stop(db, BOOK)
                    db.commit()
            return result

    models = StopAtFirstVerdict()
    out = run_consilium(session_factory=session_factory, book_id=BOOK, run_id=run_id, mode="reread",
                        ask=models, read_credits=_credits([None]), notify=lambda _t: None,
                        root=str(tmp_path), arbiter_workers=1)
    assert out["status"] == "stopped" and out["reason"] == "stopped_by_user"
    assert models.arbiter_calls <= 2 < 4       # мест шесть; оплачено не больше одного лишнего


def test_checkpoint_does_not_overwrite_a_stop_that_lands_between_its_read_and_write(book, monkeypatch):
    import app.services.consilium_engine as engine

    run_id = _run_row(book, "reread")
    original = engine._meta
    fired = []

    def meta_then_stop(run):
        value = original(run)
        if not fired:
            fired.append(True)
            with book() as other:
                engine.request_stop(other, BOOK)
                other.commit()
        return value

    monkeypatch.setattr(engine, "_meta", meta_then_stop)
    first = engine.checkpoint_run(book, run_id, {"phase": "readers"})
    with book() as db:
        assert engine.latest_run(db, BOOK)["stop_requested"] is True
    assert first is True or engine.checkpoint_run(book, run_id, {"phase": "readers"}) is True


def test_a_finished_run_is_done_even_if_the_balance_ran_low_on_its_last_call(book, tmp_path):
    from app.models import ConsiliumFinding
    from app.services.consilium_engine import run_consilium

    out = run_consilium(session_factory=book, book_id=BOOK, run_id=_run_row(book, "reread"), mode="reread",
                        ask=FakeModels(), read_credits=_credits([1000.0, 1000.0, 1000.0, 40.0]),
                        notify=lambda _t: None, root=str(tmp_path), arbiter_workers=1)
    assert out["status"] == "done" and out["added"] == 1 and out["spent_rub"] == 960.0
    with book() as db:
        assert db.query(ConsiliumFinding).count() == 1


def _disputed_book(session_factory):
    disputed = [("— Куда?", [(0, 7, "Тупуг")]), ("— Сюда?", [(0, 7, "Тупуг")]), ("— Там?", [(0, 6, "Тупуг")])]
    with session_factory() as db:
        build_book(db, chapters={1: disputed, 2: disputed})


def test_a_stop_during_the_arbiter_keeps_the_paid_verdicts_and_the_next_run_does_not_pay_again(
        session_factory, tmp_path):
    from app.models import BackgroundRun, ConsiliumFinding
    from app.services.consilium_engine import request_stop, run_consilium

    _disputed_book(session_factory)

    class StopAtFirstVerdict(FakeModels):
        def __call__(self, model, system, user, schema):
            result = super().__call__(model, system, user, schema)
            if self.arbiter_calls > 1:
                time.sleep(0.2)
            if self.arbiter_calls == 1:
                with session_factory() as db:
                    request_stop(db, BOOK)
                    db.commit()
            return result

    first = StopAtFirstVerdict()
    notes = []
    out = run_consilium(session_factory=session_factory, book_id=BOOK, run_id=_run_row(session_factory, "reread"),
                        mode="reread", ask=first, read_credits=_credits([1000.0, 1000.0, 1000.0, 970.0]),
                        notify=notes.append, root=str(tmp_path), arbiter_workers=1)
    assert out["status"] == "stopped" and out["reason"] == "stopped_by_user"
    assert out["spent_rub"] == 30.0 and "30 ₽" in notes[0]       # баланс перечитан при остановке
    with session_factory() as db:
        saved = db.query(ConsiliumFinding).all()
        assert len(saved) == first.arbiter_calls == out["added"] >= 1   # оплачено — значит записано
        assert all(f.arbiter_verdict for f in saved) and all(f.status == "new" for f in saved)
        db.query(BackgroundRun).delete()
        db.commit()

    second = FakeModels()
    again = run_consilium(session_factory=session_factory, book_id=BOOK, run_id=_run_row(session_factory),
                          mode="recheck", ask=second, read_credits=_credits([1000.0]), notify=lambda _t: None,
                          root=str(tmp_path), arbiter_workers=1)
    assert again["status"] == "done" and second.reader_calls == 0
    assert second.arbiter_calls == 6 - first.arbiter_calls


def test_without_a_balance_only_model_calls_count_towards_the_budget(session_factory, tmp_path):
    from app.services.consilium_engine import load_book_state, run_consilium, save_answers
    from tests.consilium_book import CHAPTERS

    with session_factory() as db:
        build_book(db, chapters={i: CHAPTERS[2] for i in range(1, 10)})
        state = load_book_state(db, BOOK)
    for chapter in state.chapters[:-1]:
        for reader in ("opus", "sol"):
            save_answers(BOOK, chapter, reader, "m", {0: "Тупуг", 1: "Рассказчик"}, True, root=str(tmp_path))
    models = FakeModels()
    out = run_consilium(session_factory=session_factory, book_id=BOOK, run_id=_run_row(session_factory),
                        mode="recheck", ask=models, read_credits=lambda: None, notify=lambda _t: None,
                        root=str(tmp_path), arbiter_workers=1)
    assert models.reader_calls == 2
    assert out["status"] == "done", out


def test_the_compare_phase_is_visible_in_the_run(book, tmp_path, monkeypatch):
    import app.services.consilium_engine as engine

    phases = []
    real = engine.checkpoint_run

    def spy(session_factory, run_id, meta):
        phases.append(meta["phase"])
        return real(session_factory, run_id, meta)

    monkeypatch.setattr(engine, "checkpoint_run", spy)
    engine.run_consilium(session_factory=book, book_id=BOOK, run_id=_run_row(book, "reread"), mode="reread",
                         ask=FakeModels(), read_credits=_credits([1000.0]), notify=lambda _t: None,
                         root=str(tmp_path), arbiter_workers=1)
    assert "compare" in phases and phases.index("compare") < phases.index("arbiter")


# --- мёртвый прогон не держит книгу вечно -------------------------------------------------

def _age_run(session_factory, run_id, *, minutes, status="running", heartbeat=True):
    from datetime import timedelta

    from app.models import BackgroundRun
    from app.time_utils import utcnow_naive

    with session_factory() as db:
        run = db.get(BackgroundRun, run_id)
        old = utcnow_naive() - timedelta(minutes=minutes)
        run.status = status
        run.created_at = old
        run.started_at = old
        run.heartbeat_at = old if heartbeat else None
        db.commit()


def test_a_failure_before_the_run_starts_marks_the_run_failed(book, tmp_path, monkeypatch):
    import app.services.consilium_engine as engine
    from app.models import BackgroundRun

    run_id = _run_row(book)

    def broken(*_a, **_k):
        raise RuntimeError("смета упала")

    monkeypatch.setattr(engine, "estimate", broken)
    notes = []
    with pytest.raises(RuntimeError):
        engine.run_consilium(session_factory=book, book_id=BOOK, run_id=run_id, mode="recheck",
                             ask=FakeModels(), read_credits=_credits([1000.0]), notify=notes.append,
                             root=str(tmp_path), arbiter_workers=1)
    with book() as db:
        run = db.get(BackgroundRun, run_id)
        assert run.status == "failed" and "смета упала" in run.error_message
    assert notes and "упал" in notes[0]


def test_a_running_row_without_a_heartbeat_for_an_hour_is_dead(book):
    from app.models import BackgroundRun
    from app.services.consilium_engine import STALE_RUN_MINUTES, latest_run

    assert STALE_RUN_MINUTES == 60
    run_id = _run_row(book)
    _age_run(book, run_id, minutes=61)
    with book() as db:
        payload = latest_run(db, BOOK)
        db.commit()
    assert payload["status"] == "failed"
    assert payload["error"] == "воркер остановился — прогон можно продолжить"
    with book() as db:
        assert db.get(BackgroundRun, run_id).status == "failed"


def test_a_running_row_falls_back_to_started_at_and_a_fresh_one_lives(book):
    from app.services.consilium_engine import latest_run

    run_id = _run_row(book)
    _age_run(book, run_id, minutes=30)
    with book() as db:
        assert latest_run(db, BOOK)["status"] == "running"
    _age_run(book, run_id, minutes=90, heartbeat=False)
    with book() as db:
        assert latest_run(db, BOOK)["status"] == "failed"


def test_a_queued_row_is_not_aged(book):
    from app.services.consilium_engine import latest_run

    run_id = _run_row(book)
    _age_run(book, run_id, minutes=600, status="queued")
    with book() as db:
        assert latest_run(db, BOOK)["status"] == "queued"


def test_stop_on_a_stale_running_row_reports_nothing_to_stop(book):
    from app.services.consilium_engine import latest_run, request_stop

    run_id = _run_row(book)
    _age_run(book, run_id, minutes=61)
    with book() as db:
        assert request_stop(db, BOOK) is False
        db.commit()
    with book() as db:
        assert latest_run(db, BOOK)["status"] == "failed"


def test_stop_on_a_queued_row_stops_it_at_once(book):
    from app.models import BackgroundRun
    from app.services.consilium_engine import latest_run, request_stop

    run_id = _run_row(book)
    _age_run(book, run_id, minutes=1, status="queued")
    with book() as db:
        assert request_stop(db, BOOK) is True
        db.commit()
    with book() as db:
        run = db.get(BackgroundRun, run_id)
        assert run.status == "stopped" and run.error_message == "остановлен вручную"
        assert run.finished_at is not None
        assert latest_run(db, BOOK)["status"] == "stopped"


@pytest.fixture()
def worker(book, monkeypatch):
    import app.services.consilium_engine as engine

    monkeypatch.setattr("app.worker_tasks.SessionLocal", book)
    calls = []
    monkeypatch.setattr(engine, "run_consilium", lambda **kw: calls.append(kw) or {"status": "done"})
    return calls


def test_the_worker_does_nothing_for_a_run_that_was_stopped_in_the_queue(book, worker):
    from app.models import BackgroundRun
    from app.worker_tasks import perform_consilium_task

    run_id = _run_row(book)
    _age_run(book, run_id, minutes=1, status="stopped")
    perform_consilium_task(BOOK, mode="recheck", run_id=run_id)
    assert worker == []
    with book() as db:
        assert db.get(BackgroundRun, run_id).status == "stopped"


def test_the_worker_starts_a_queued_run(book, worker):
    from app.models import BackgroundRun
    from app.worker_tasks import perform_consilium_task

    run_id = _run_row(book)
    _age_run(book, run_id, minutes=1, status="queued")
    perform_consilium_task(BOOK, mode="reread", run_id=run_id)
    assert len(worker) == 1 and worker[0]["mode"] == "reread"
    with book() as db:
        assert db.get(BackgroundRun, run_id).status == "running"


def test_the_worker_marks_a_run_that_died_without_marking_itself(book, monkeypatch):
    import app.services.consilium_engine as engine
    from app.models import BackgroundRun
    from app.worker_tasks import perform_consilium_task

    monkeypatch.setattr("app.worker_tasks.SessionLocal", book)

    def dies(**_kw):
        raise MemoryError("кончилась память")

    monkeypatch.setattr(engine, "run_consilium", dies)
    run_id = _run_row(book)
    _age_run(book, run_id, minutes=1, status="queued")
    with pytest.raises(MemoryError):
        perform_consilium_task(BOOK, mode="recheck", run_id=run_id)
    with book() as db:
        run = db.get(BackgroundRun, run_id)
        assert run.status == "failed" and "кончилась память" in run.error_message


def test_the_worker_does_not_overwrite_the_reason_the_run_wrote_itself(book, monkeypatch):
    import app.services.consilium_engine as engine
    from app.models import BackgroundRun
    from app.worker_tasks import perform_consilium_task

    monkeypatch.setattr("app.worker_tasks.SessionLocal", book)

    def fails_itself(**kw):
        engine._finish(book, kw["run_id"], "failed", {}, "своя причина")
        raise RuntimeError("снаружи")

    monkeypatch.setattr(engine, "run_consilium", fails_itself)
    run_id = _run_row(book)
    _age_run(book, run_id, minutes=1, status="queued")
    with pytest.raises(RuntimeError):
        perform_consilium_task(BOOK, mode="recheck", run_id=run_id)
    with book() as db:
        assert db.get(BackgroundRun, run_id).error_message == "своя причина"


# --- неполная глава не снимает находки и видна в итоге --------------------------------------

class SolSilentOnChapterOne(FakeModels):
    """Второй чтец возвращает пустой ответ по первой главе — сбой, отказ, пустой кусок."""

    def __call__(self, model, system, user, schema):
        if "lines" in schema.get("required", []) and "sol" in model and "Идём" in user:
            self.reader_calls += 1
            return {"lines": []}
        return super().__call__(model, system, user, schema)


def _first_full_run(book, tmp_path):
    from app.models import BackgroundRun, ConsiliumFinding
    from app.services.consilium_engine import run_consilium

    run_consilium(session_factory=book, book_id=BOOK, run_id=_run_row(book, "reread"), mode="reread",
                  ask=FakeModels(), read_credits=_credits([1000.0]), notify=lambda _t: None,
                  root=str(tmp_path), arbiter_workers=1)
    with book() as db:
        assert db.query(ConsiliumFinding).one().status == "new"
        db.query(BackgroundRun).delete()
        db.commit()


def test_an_unanswered_paragraph_does_not_make_its_finding_gone(book, tmp_path):
    import os

    from app.models import ConsiliumFinding
    from app.services.consilium_engine import artifact_path, run_consilium

    _first_full_run(book, tmp_path)
    os.remove(artifact_path(BOOK, 1, "sol", str(tmp_path)))
    notes = []
    out = run_consilium(session_factory=book, book_id=BOOK, run_id=_run_row(book), mode="recheck",
                        ask=SolSilentOnChapterOne(), read_credits=_credits([1000.0]), notify=notes.append,
                        root=str(tmp_path), arbiter_workers=1)
    assert out["status"] == "done" and out["gone"] == 0
    with book() as db:
        assert db.query(ConsiliumFinding).one().status == "new"
    assert out["chapters_incomplete"] == 1
    assert "неполных глав: 1 — пересверка их дочитает" in notes[0]


def test_incomplete_chapters_are_counted_in_the_run(book, tmp_path):
    from app.services.consilium_engine import latest_run, run_consilium

    notes = []
    out = run_consilium(session_factory=book, book_id=BOOK, run_id=_run_row(book, "reread"), mode="reread",
                        ask=SolSilentOnChapterOne(), read_credits=_credits([1000.0]), notify=notes.append,
                        root=str(tmp_path), arbiter_workers=1)
    assert out["status"] == "done" and out["chapters_incomplete"] == 1
    with book() as db:
        payload = latest_run(db, BOOK)
    assert payload["chapters_incomplete"] == 1 and payload["result"]["chapters_incomplete"] == 1


def test_a_complete_run_says_nothing_about_incomplete_chapters(book, tmp_path):
    from app.services.consilium_engine import run_consilium

    notes = []
    out = run_consilium(session_factory=book, book_id=BOOK, run_id=_run_row(book, "reread"), mode="reread",
                        ask=FakeModels(), read_credits=_credits([1000.0]), notify=notes.append,
                        root=str(tmp_path), arbiter_workers=1)
    assert out["chapters_incomplete"] == 0 and "неполных" not in notes[0]


def test_a_reread_does_not_replace_a_complete_artifact_with_an_incomplete_one(book, tmp_path):
    from app.models import ConsiliumFinding
    from app.services.consilium_engine import load_answers, load_book_state, run_consilium

    _first_full_run(book, tmp_path)
    out = run_consilium(session_factory=book, book_id=BOOK, run_id=_run_row(book, "reread"), mode="reread",
                        ask=SolSilentOnChapterOne(), read_credits=_credits([1000.0]), notify=lambda _t: None,
                        root=str(tmp_path), arbiter_workers=1)
    with book() as db:
        state = load_book_state(db, BOOK)
        assert db.query(ConsiliumFinding).one().status == "new"
    kept = load_answers(BOOK, state.chapters[0], "sol", root=str(tmp_path))
    assert kept == {0: "Гамук", 1: "Гамук", 2: "Рассказчик"}
    assert out["chapters_incomplete"] == 0 and out["gone"] == 0


def test_the_same_model_at_two_providers_goes_to_each_providers_own_endpoint(book, tmp_path, monkeypatch):
    """Чтец на RouterAI и арбитр на OpenRouter с одним именем модели — каждый платит своему."""
    from app.services import step_models
    from app.services.consilium_engine import run_consilium
    steps = {"consilium_reader_1": ("routerai", "anthropic/claude-opus-5"),
             "consilium_reader_2": ("openrouter", "anthropic/claude-opus-5"),
             "consilium_arbiter": ("openrouter", "anthropic/claude-opus-5")}
    monkeypatch.setattr(step_models, "step_model", lambda key: steps[key])
    monkeypatch.setattr(step_models, "require_key_for", lambda key: None)
    models, sent = FakeModels(), []
    monkeypatch.setattr("app.pipeline.llm_client._resolve_provider", lambda provider: ("k", provider, "openai"))

    def call_chat(base, key, model, system, user, **kw):
        sent.append((base, "lines" in kw["json_schema"].get("required", [])))
        return {"content": models(model, system, user, kw["json_schema"])}
    monkeypatch.setattr("app.pipeline.llm_client.call_chat", call_chat)
    run_consilium(session_factory=book, book_id=BOOK, run_id=_run_row(book, "reread"), mode="reread",
                  read_credits=lambda: None, notify=lambda _t: None, root=str(tmp_path), arbiter_workers=1)
    readers = [base for base, is_reader in sent if is_reader]
    arbiter = [base for base, is_reader in sent if not is_reader]
    assert readers.count("routerai") == 2 and readers.count("openrouter") == 2
    assert arbiter == ["openrouter"]


def test_an_empty_routerai_balance_does_not_stop_a_run_on_openrouter(book, tmp_path, monkeypatch):
    from app.services import step_models
    from app.services.consilium_engine import run_consilium
    monkeypatch.setattr(step_models, "step_model", lambda key: ("openrouter", "vendor/m"))
    monkeypatch.setattr(step_models, "require_key_for", lambda key: None)
    models = FakeModels()
    monkeypatch.setattr("app.pipeline.llm_client._resolve_provider", lambda provider: ("k", provider, "openai"))
    monkeypatch.setattr("app.pipeline.llm_client.call_chat",
                        lambda base, key, model, system, user, **kw: {"content": models(model, system, user, kw["json_schema"])})
    out = run_consilium(session_factory=book, book_id=BOOK, run_id=_run_row(book, "reread"), mode="reread",
                        read_credits=lambda: 0.0, notify=lambda _t: None, root=str(tmp_path), arbiter_workers=1)
    assert out["status"] == "done" and models.reader_calls == 4


def test_each_paid_call_knows_its_consilium_step(book, tmp_path, monkeypatch):
    from app.services import spend, step_models
    from app.services.consilium_engine import run_consilium
    monkeypatch.setattr(step_models, "step_model", lambda key: ("routerai", "vendor/m"))
    monkeypatch.setattr(step_models, "require_key_for", lambda key: None)
    models, seen = FakeModels(), []
    monkeypatch.setattr("app.pipeline.llm_client._resolve_provider", lambda provider: ("k", provider, "openai"))

    def call_chat(base, key, model, system, user, **kw):
        ctx = spend._CONTEXT.get()
        seen.append((ctx.get("step"), ctx.get("book_id"), "lines" in kw["json_schema"].get("required", [])))
        return {"content": models(model, system, user, kw["json_schema"])}
    monkeypatch.setattr("app.pipeline.llm_client.call_chat", call_chat)
    run_consilium(session_factory=book, book_id=BOOK, run_id=_run_row(book, "reread"), mode="reread",
                  read_credits=lambda: None, notify=lambda _t: None, root=str(tmp_path), arbiter_workers=1)
    readers = {step for step, _b, is_reader in seen if is_reader}
    arbiter = {step for step, _b, is_reader in seen if not is_reader}
    assert readers == {"consilium_reader_1", "consilium_reader_2"} and arbiter == {"consilium_arbiter"}
    assert {b for _s, b, _r in seen} == {BOOK}

from __future__ import annotations

import logging
import signal

from scripts import scheduler_loop


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def test_jobs_run_immediately_and_then_on_their_own_cadences() -> None:
    clock = FakeClock()
    rejection_runs: list[float] = []
    deadline_runs: list[float] = []

    scheduler_loop.run_scheduler(
        send_due_rejections=lambda: rejection_runs.append(clock.now),
        run_deadlines=lambda: deadline_runs.append(clock.now),
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        should_stop=lambda: len(rejection_runs) == 6 and len(deadline_runs) == 2,
    )

    assert rejection_runs == [0.0, 60.0, 120.0, 180.0, 240.0, 300.0]
    assert deadline_runs == [0.0, 300.0]
    assert clock.sleeps == [60.0] * 5


def test_a_failed_job_is_logged_and_does_not_stop_other_jobs(caplog) -> None:
    clock = FakeClock()
    rejection_attempts = 0
    deadline_runs: list[float] = []

    def flaky_rejections() -> int:
        nonlocal rejection_attempts
        rejection_attempts += 1
        if rejection_attempts == 1:
            raise RuntimeError("database unavailable")
        return 0

    with caplog.at_level(logging.ERROR, logger="scripts.scheduler_loop"):
        scheduler_loop.run_scheduler(
            send_due_rejections=flaky_rejections,
            run_deadlines=lambda: deadline_runs.append(clock.now),
            monotonic=clock.monotonic,
            sleep=clock.sleep,
            should_stop=lambda: rejection_attempts == 2,
        )

    assert rejection_attempts == 2
    assert deadline_runs == [0.0]
    assert "send_due_rejections" in caplog.text
    assert "database unavailable" in caplog.text


def test_main_turns_sigterm_into_a_clean_stop(monkeypatch) -> None:
    handlers: dict[int, object] = {}
    previous_handler = object()

    monkeypatch.setattr(signal, "getsignal", lambda signum: previous_handler)

    def remember_handler(signum, handler):
        handlers[signum] = handler

    monkeypatch.setattr(signal, "signal", remember_handler)

    def exercise_signal(*, sleep, should_stop, **_kwargs) -> None:
        handler = handlers[signal.SIGTERM]
        assert callable(handler)
        handler(signal.SIGTERM, None)
        assert should_stop()
        assert sleep(0) is True

    monkeypatch.setattr(scheduler_loop, "run_scheduler", exercise_signal)

    assert scheduler_loop.main() == 0
    assert handlers[signal.SIGTERM] is previous_handler


def test_main_treats_keyboard_interrupt_as_a_clean_stop(monkeypatch) -> None:
    monkeypatch.setattr(signal, "getsignal", lambda _signum: signal.SIG_DFL)
    monkeypatch.setattr(signal, "signal", lambda _signum, _handler: None)

    def interrupt(**_kwargs) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(scheduler_loop, "run_scheduler", interrupt)

    assert scheduler_loop.main() == 0

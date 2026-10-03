#!/usr/bin/env python
"""Run the small periodic maintenance jobs in one long-lived process.

    python -m scripts.scheduler_loop
"""
from __future__ import annotations

import logging
import signal
import time
from dataclasses import dataclass
from threading import Event
from types import FrameType
from typing import Callable

from scripts.run_deadlines import main as run_deadlines_main
from scripts.send_due_rejections import main as send_due_rejections_main

logger = logging.getLogger(__name__)


@dataclass
class _Job:
    name: str
    interval: float
    function: Callable[[], object]
    next_run: float


def _run_job(job: _Job) -> None:
    try:
        result = job.function()
        if result not in (None, 0):
            logger.error("scheduled job %s returned exit status %s", job.name, result)
    except Exception:
        logger.exception("scheduled job %s failed", job.name)


def run_scheduler(
    *,
    send_due_rejections: Callable[[], object] = send_due_rejections_main,
    run_deadlines: Callable[[], object] = run_deadlines_main,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], object] = time.sleep,
    should_stop: Callable[[], bool] = lambda: False,
) -> None:
    """Run both jobs until ``should_stop`` asks the loop to finish.

    Jobs are due on startup. Their following deadlines stay anchored to the
    monotonic clock, so a slow or failed pass cannot gradually shift cadence.
    Dependencies are injectable to keep the scheduling behavior testable.
    """
    started_at = monotonic()
    jobs = (
        _Job("send_due_rejections", 60.0, send_due_rejections, started_at),
        _Job("run_deadlines", 300.0, run_deadlines, started_at),
    )

    while not should_stop():
        now = monotonic()
        for job in jobs:
            if now < job.next_run:
                continue

            scheduled_for = job.next_run
            _run_job(job)

            # Skip elapsed slots instead of running a burst to catch up.
            finished_at = monotonic()
            elapsed_intervals = int((finished_at - scheduled_for) // job.interval)
            job.next_run = scheduled_for + (elapsed_intervals + 1) * job.interval

        if should_stop():
            break

        delay = max(0.0, min(job.next_run for job in jobs) - monotonic())
        sleep(delay)


def main() -> int:
    stop = Event()
    previous_sigterm_handler = signal.getsignal(signal.SIGTERM)

    def request_stop(_signum: int, _frame: FrameType | None) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    try:
        run_scheduler(sleep=stop.wait, should_stop=stop.is_set)
    except KeyboardInterrupt:
        stop.set()
    finally:
        signal.signal(signal.SIGTERM, previous_sigterm_handler)
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())

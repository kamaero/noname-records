#!/usr/bin/env python3
"""RQ worker: runs the v2 pipeline and DAW export tasks (see app/worker_tasks.py).

There is no resume-on-start any more: a v2 run interrupted by a worker restart
stays as its run row says, and the operator starts it again from the book hub.
"""
import os
import sys

# Ensure the app directory is in the python path
sys.path.insert(0, os.getcwd())

from rq import Worker, Queue
from app.config import settings
from app.redis_client import redis_conn

DEFAULT_QUEUES = ["high", "default", "low"]


def queues_from_env(value: str | None) -> list[str]:
    """Очереди воркера из `RQ_QUEUES`. Пусто — основной воркер, как было всегда.

    Консилиум читает книгу часами; в общей очереди он держал бы распознавание дублей и
    финалы глав. Поэтому у него своя служба с `RQ_QUEUES=consilium`.
    """
    names = [part.strip() for part in str(value or "").split(",") if part.strip()]
    return names or list(DEFAULT_QUEUES)


def run_worker():
    listen = queues_from_env(os.environ.get("RQ_QUEUES"))
    print(f"Starting RQ worker for queues: {listen}")
    print(f"Redis URL: {settings.redis_url}")
    queues = [Queue(name, connection=redis_conn) for name in listen]
    worker = Worker(queues, connection=redis_conn)
    worker.work()

if __name__ == "__main__":
    run_worker()

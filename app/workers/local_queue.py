"""Очередь задач внутри процесса — для режима «одно место».

Тот же метод `enqueue`, что у очереди RQ, поэтому `enqueue_tracked_task` не знает, где он.
Две линии, как два воркера на VPS: консилиум (часами читает книгу) — своя, остальное —
общая; внутри линии задачи по очереди. `job_timeout` не исполняется: поток не убить,
остановка — флагами остановки прогонов, как и на VPS.
"""
import importlib
import logging
import queue
import threading
import uuid
from dataclasses import dataclass

logger = logging.getLogger(__name__)
_LINES: dict[str, "LocalQueue"] = {}
_LINES_LOCK = threading.Lock()


@dataclass(frozen=True)
class LocalJob:
    id: str


def _resolve(func_ref):
    if callable(func_ref):
        return func_ref
    module, _, name = str(func_ref).rpartition(".")
    return getattr(importlib.import_module(module), name)


class LocalQueue:
    def __init__(self, name: str):
        self.name = name
        self._tasks: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def enqueue(self, func_ref, *, job_timeout=None, result_ttl=None, **kwargs) -> LocalJob:
        job = LocalJob(uuid.uuid4().hex)
        self._tasks.put((func_ref, kwargs))
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._work, name=f"local-queue:{self.name}", daemon=True)
                self._thread.start()
        return job

    def _work(self) -> None:
        while True:
            func_ref, kwargs = self._tasks.get()
            try:
                _resolve(func_ref)(**kwargs)
            except Exception:
                # упавшая задача пишет свою ошибку в свой прогон; линия живёт дальше
                logger.exception("Задача в очереди %s упала", self.name)
            finally:
                self._tasks.task_done()

    def join(self, timeout: float) -> bool:
        """Для тестов и выхода: дождаться пустой очереди. True — дождались."""
        done = threading.Event()
        threading.Thread(target=lambda: (self._tasks.join(), done.set()), daemon=True).start()
        return done.wait(timeout)


def local_queue(name: str) -> LocalQueue:
    line = "consilium" if name == "consilium" else "main"
    with _LINES_LOCK:
        if line not in _LINES:
            _LINES[line] = LocalQueue(line)
        return _LINES[line]

from __future__ import annotations

from typing import Any, Callable


def build_startup_runtime_helpers(deps: dict[str, Any]) -> dict[str, Callable[..., Any]]:
    """Background loops the web process starts at boot: the NAS watchdog probe and the
    mirror copy-out. The v1 pipeline runner, its recovery loop, the resume-on-start and
    the retention sweep are gone (pipeline v2 runs as RQ jobs, see app/worker_tasks.py;
    retention only ever purged v1 text)."""
    settings = deps["settings"]
    session_local = deps["SessionLocal"]
    background_run_model = deps["BackgroundRun"]
    nas_probe_loop_worker = deps["nas_probe_loop_worker"]
    start_nas_probe_background = deps["start_nas_probe_background"]
    mirror_loop_worker = deps["mirror_loop_worker"]
    start_mirror_background = deps["start_mirror_background"]

    def nas_probe_loop() -> None:
        nas_probe_loop_worker(
            settings=settings,
            session_factory=session_local,
            background_run_cls=background_run_model,
        )

    def start_nas_probe_worker() -> bool:
        # Ответ стартера возвращается наружу, а не глотается: `startup` по нему пишет
        # предупреждение, и это единственный способ увидеть после рестарта, что сторож
        # NAS не встал. Пока обёртка отдавала `None`, предупреждение печаталось на
        # каждом старте — в том числе успешном — и настоящий сбой в нём терялся.
        return start_nas_probe_background(
            settings=settings,
            session_factory=session_local,
            background_run_cls=background_run_model,
            loop_factory=nas_probe_loop,
        )

    def mirror_loop() -> None:
        mirror_loop_worker(
            settings=settings,
            session_factory=session_local,
            background_run_cls=background_run_model,
        )

    def start_mirror_worker() -> bool:
        # Тот же случай, что и у сторожа: без `return` «зеркало не запущено»
        # печаталось бы всегда и не значило ничего.
        return start_mirror_background(
            settings=settings,
            session_factory=session_local,
            background_run_cls=background_run_model,
            loop_factory=mirror_loop,
        )

    return {
        "nas_probe_loop": nas_probe_loop,
        "start_nas_probe_worker": start_nas_probe_worker,
        "mirror_loop": mirror_loop,
        "start_mirror_worker": start_mirror_worker,
    }

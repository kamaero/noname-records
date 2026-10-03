"""Обёртки фоновых стартеров обязаны отдавать их ответ, а не глотать его.

`startup` смотрит на результат стартера и пишет предупреждение, если поток не встал —
это единственная диагностика, по которой после рестарта видно, поднялся ли сторож NAS.
Обёртки звали стартер без `return` и всегда отдавали `None`: предупреждение печаталось
на КАЖДОМ старте, включая успешный, и настоящий сбой в нём было не разглядеть.
"""
import pytest

from app.services.startup_runtime import build_startup_runtime_helpers


def _helpers(*, nas_answer, mirror_answer):
    return build_startup_runtime_helpers({
        "settings": object(),
        "SessionLocal": object(),
        "BackgroundRun": object(),
        "nas_probe_loop_worker": lambda **kwargs: None,
        "start_nas_probe_background": lambda **kwargs: nas_answer,
        "mirror_loop_worker": lambda **kwargs: None,
        "start_mirror_background": lambda **kwargs: mirror_answer,
    })


@pytest.mark.parametrize("answer", [True, False])
def test_the_nas_watchdog_wrapper_passes_the_starters_answer_through(answer):
    helpers = _helpers(nas_answer=answer, mirror_answer=True)

    assert helpers["start_nas_probe_worker"]() is answer


@pytest.mark.parametrize("answer", [True, False])
def test_the_mirror_wrapper_passes_the_starters_answer_through(answer):
    helpers = _helpers(nas_answer=True, mirror_answer=answer)

    assert helpers["start_mirror_worker"]() is answer

from __future__ import annotations

from app.constants import WORKSPACE_TABS
from app.models import (
    AudioFile,
    BackgroundRun,
    BookBudget,
    Character,
    CharacterBudgetSnapshot,
    OperatorIntervention,
    ScriptBook,
    ScriptChapter,
    ScriptLog,
    TelegramAuthAccount,
    User,
    UserRole,
)
from app.services.book_import import enqueue_book
from app.workers.mirror import (
    mirror_loop as _mirror_loop_worker,
    nas_probe_loop as _nas_probe_loop_worker,
    start_mirror_worker as _start_mirror_background,
    start_nas_probe_worker as _start_nas_probe_background,
)


def build_pipeline_and_model_deps() -> dict:
    return {
        "enqueue_book": enqueue_book,
        "nas_probe_loop_worker": _nas_probe_loop_worker,
        "start_nas_probe_background": _start_nas_probe_background,
        "mirror_loop_worker": _mirror_loop_worker,
        "start_mirror_background": _start_mirror_background,
        "WORKSPACE_TABS": WORKSPACE_TABS,
        "AudioFile": AudioFile,
        "BackgroundRun": BackgroundRun,
        "BookBudget": BookBudget,
        "Character": Character,
        "CharacterBudgetSnapshot": CharacterBudgetSnapshot,
        "OperatorIntervention": OperatorIntervention,
        "ScriptBook": ScriptBook,
        "ScriptChapter": ScriptChapter,
        "ScriptLog": ScriptLog,
        "TelegramAuthAccount": TelegramAuthAccount,
        "User": User,
        "UserRole": UserRole,
    }

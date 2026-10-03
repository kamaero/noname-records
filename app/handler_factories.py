"""Lazy handler registry: one factory per handler group, each fed only its contract keys.

Cross-group helpers (main_helpers) are reached through the registry
at call time, so a group can be built without the others and the lazy registry never
recurses at import.
"""
from __future__ import annotations

from app.api.book_actions import build_book_actions_handlers
from app.api.books_list import build_books_list_handlers
from app.api.budget_api import build_budget_api_handlers
from app.api.dictor_uploads import build_dictor_upload_handlers
from app.api.frontend_core import build_frontend_core_handlers
from app.api.recording import build_recording_handlers
from app.api.system_routes import build_system_routes_handlers
from app.handler_dependency_builder import build_handler_deps
from app.handler_factory_contracts import (
    BOOK_ACTIONS_HANDLER_DEP_KEYS,
    BOOKS_LIST_HANDLER_DEP_KEYS,
    BUDGET_API_HANDLER_DEP_KEYS,
    DICTOR_UPLOAD_HANDLER_DEP_KEYS,
    FRONTEND_CORE_HANDLER_DEP_KEYS,
    MAIN_HELPERS_DEP_KEYS,
    RECORDING_HANDLER_DEP_KEYS,
    STARTUP_RUNTIME_HELPERS_DEP_KEYS,
    SYSTEM_ROUTES_HANDLER_DEP_KEYS,
    select_deps,
)
from app.handler_registry import LazyHandlerRegistry
from app.services.main_helpers import build_main_helpers
from app.services.startup_runtime import build_startup_runtime_helpers


def register_handler_factories(registry: LazyHandlerRegistry, deps: dict) -> None:
    def store_audio_file(db, **kwargs):
        return deps["store_audio_file_service"](db, safe_name=deps["safe_name"], **kwargs)

    registry.register_factories(
        {
            "books_list": lambda: build_books_list_handlers(select_deps(deps, BOOKS_LIST_HANDLER_DEP_KEYS)),
            "book_actions": lambda: build_book_actions_handlers(select_deps(deps, BOOK_ACTIONS_HANDLER_DEP_KEYS)),
            "frontend_core": lambda: build_frontend_core_handlers(
                {
                    **select_deps(deps, FRONTEND_CORE_HANDLER_DEP_KEYS),
                    "owner_api_allowed": lambda request: deps["is_owner_telegram"](request) or deps["has_any_role"](request, {"admin"}),
                }
            ),
            "budget_api": lambda: build_budget_api_handlers(select_deps(deps, BUDGET_API_HANDLER_DEP_KEYS)),
            "main_helpers": lambda: build_main_helpers(
                {
                    **select_deps(deps, MAIN_HELPERS_DEP_KEYS),
                    "frontend_dist_file": lambda *parts: deps["frontend_dist_file"](deps["frontend_dist_dir"], *parts),
                }
            ),
            "dictor_upload": lambda: build_dictor_upload_handlers(
                {
                    **select_deps(deps, DICTOR_UPLOAD_HANDLER_DEP_KEYS),
                    "parse_batch_audio_filename": lambda filename, default_book_code="", default_actor_name="": deps["parse_batch_audio_filename_service"](
                        filename,
                        default_book_code=default_book_code,
                        default_actor_name=default_actor_name,
                        normalize_role_label=deps["normalize_role_label"],
                    ),
                    "apply_batch_overrides": lambda parsed, override=None: deps["apply_batch_overrides_service"](
                        parsed,
                        override,
                        normalize_role_label=deps["normalize_role_label"],
                    ),
                }
            ),
            "recording": lambda: build_recording_handlers(
                {
                    **select_deps(deps, RECORDING_HANDLER_DEP_KEYS),
                    "store_audio_file": store_audio_file,
                    "require_recording_access": registry.get("dictor_upload")["require_recording_access"],
                    "parse_batch_audio_filename": lambda filename, default_book_code="", default_actor_name="": deps["parse_batch_audio_filename_service"](
                        filename,
                        default_book_code=default_book_code,
                        default_actor_name=default_actor_name,
                        normalize_role_label=deps["normalize_role_label"],
                    ),
                }
            ),
            "system_routes": lambda: build_system_routes_handlers(
                {
                    **select_deps(deps, SYSTEM_ROUTES_HANDLER_DEP_KEYS),
                    "ensure_admin_account_cb": lambda: registry.get("main_helpers")["ensure_admin_account"](),
                    "start_nas_probe_worker_cb": lambda: registry.get("startup_runtime_helpers")["start_nas_probe_worker"](),
                    "start_mirror_worker_cb": lambda: registry.get("startup_runtime_helpers")["start_mirror_worker"](),
                }
            ),
            "startup_runtime_helpers": lambda: build_startup_runtime_helpers(select_deps(deps, STARTUP_RUNTIME_HELPERS_DEP_KEYS)),
        }
    )


def build_handler_registry(*, frontend_dist_dir: str) -> LazyHandlerRegistry:
    registry = LazyHandlerRegistry({})
    deps = build_handler_deps(frontend_dist_dir=frontend_dist_dir)
    register_handler_factories(registry, deps)
    return registry

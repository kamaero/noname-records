"""Every handler group declares the dependency keys it reads; this keeps the three
places in sync: the module (`deps["..."]`), its contract tuple, and `build_handler_deps()`.
A key missing from any of them only surfaces as a 500 on the first request otherwise
(the registry is lazy)."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.handler_dependency_builder import build_handler_deps
from app.handler_factories import build_handler_registry
from app.handler_factory_contracts import (
    BOOK_ACTIONS_HANDLER_DEP_KEYS,
    BOOKS_LIST_HANDLER_DEP_KEYS,
    BUDGET_API_HANDLER_DEP_KEYS,
    DICTOR_UPLOAD_HANDLER_DEP_KEYS,
    FRONTEND_CORE_HANDLER_DEP_KEYS,
    HANDLER_CONTRACTS,
    MAIN_HELPERS_DEP_KEYS,
    RECORDING_HANDLER_DEP_KEYS,
    STARTUP_RUNTIME_HELPERS_DEP_KEYS,
    SYSTEM_ROUTES_HANDLER_DEP_KEYS,
    contract_missing_keys,
    select_deps,
)

# (module, contract, keys the factory reads itself, keys the factory injects into the module)
MODULE_CONTRACT_CASES = (
    {"path": "app/api/books_list.py", "contract": BOOKS_LIST_HANDLER_DEP_KEYS},
    {"path": "app/api/book_actions.py", "contract": BOOK_ACTIONS_HANDLER_DEP_KEYS},
    {
        "path": "app/api/frontend_core.py",
        "contract": FRONTEND_CORE_HANDLER_DEP_KEYS,
        "factory_support": ("has_any_role",),
        "factory_injected": ("owner_api_allowed",),
    },
    {"path": "app/api/budget_api.py", "contract": BUDGET_API_HANDLER_DEP_KEYS},
    {"path": "app/services/main_helpers.py", "contract": MAIN_HELPERS_DEP_KEYS},
    {
        "path": "app/api/dictor_uploads.py",
        "contract": DICTOR_UPLOAD_HANDLER_DEP_KEYS,
        "factory_support": ("apply_batch_overrides_service", "normalize_role_label", "parse_batch_audio_filename_service"),
        "factory_injected": ("apply_batch_overrides", "parse_batch_audio_filename"),
    },
    {
        "path": "app/api/recording.py",
        "contract": RECORDING_HANDLER_DEP_KEYS,
        "factory_support": ("safe_name", "store_audio_file_service", "parse_batch_audio_filename_service", "normalize_role_label"),
        "factory_injected": (
            "require_recording_access", "store_audio_file",
            "parse_batch_audio_filename",
        ),
    },
    {
        "path": "app/api/system_routes.py",
        "contract": SYSTEM_ROUTES_HANDLER_DEP_KEYS,
        "factory_injected": (
            "ensure_admin_account_cb",
            "start_nas_probe_worker_cb",
            "start_mirror_worker_cb",
        ),
    },
    {"path": "app/services/startup_runtime.py", "contract": STARTUP_RUNTIME_HELPERS_DEP_KEYS},
)


def _deps_keys_in_file(path: str) -> tuple[str, ...]:
    text = Path(path).read_text()
    return tuple(sorted(set(re.findall(r'deps\["([^"]+)"\]', text))))


def test_every_contract_is_fulfilled_by_build_handler_deps() -> None:
    deps = build_handler_deps(frontend_dist_dir="/tmp/noname-frontend-dist")
    for name, keys in HANDLER_CONTRACTS.items():
        assert contract_missing_keys(deps, keys) == (), name


def test_every_registered_group_has_a_contract() -> None:
    registry = build_handler_registry(frontend_dist_dir="/tmp/noname-frontend-dist")
    assert set(registry._factories) == set(HANDLER_CONTRACTS)


def test_select_deps_reports_missing_keys_clearly() -> None:
    with pytest.raises(KeyError) as exc_info:
        select_deps({"present": 1}, ("present", "missing"))

    assert "Missing dependency keys: missing" in str(exc_info.value)
    assert "Available keys: present" in str(exc_info.value)


def test_handler_registry_builds_every_group() -> None:
    registry = build_handler_registry(frontend_dist_dir="/tmp/noname-frontend-dist")
    for factory_name in HANDLER_CONTRACTS:
        built = registry.get(factory_name)
        assert isinstance(built, dict), factory_name
        assert built, factory_name


def test_dependency_contracts_match_module_usage() -> None:
    covered = {case["path"] for case in MODULE_CONTRACT_CASES}
    assert len(covered) == len(HANDLER_CONTRACTS), "one module case per handler group"
    for case in MODULE_CONTRACT_CASES:
        actual = set(_deps_keys_in_file(case["path"]))
        contract = set(case["contract"])
        factory_support = set(case.get("factory_support", ()))
        factory_injected = set(case.get("factory_injected", ()))

        assert contract - factory_support == actual - factory_injected, case["path"]

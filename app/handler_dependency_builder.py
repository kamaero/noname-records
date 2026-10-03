from __future__ import annotations

from app.handler_dependency_runtime import build_base_runtime_deps
from app.handler_dependency_system import build_pipeline_and_model_deps
from app.handler_dependency_workspace import build_workspace_service_deps


def build_handler_deps(*, frontend_dist_dir: str) -> dict:
    return {
        **build_base_runtime_deps(frontend_dist_dir=frontend_dist_dir),
        **build_workspace_service_deps(),
        **build_pipeline_and_model_deps(),
    }

#!/usr/bin/env python3
"""Генератор справочников docs/reference/*.md из самого кода репозитория.

Три файла, всё выводится из живого дерева кода, а не из памяти автора:

- API_ROUTES.md  — каждый HTTP-маршрут реального приложения (app.main.app.routes)
- DB_SCHEMA.md   — таблицы из SQLAlchemy-метаданных (app.models + app.v2.models) + список
  ревизий Alembic
- ENV_VARS.md    — каждая переменная окружения, которую читает приложение: поля Settings
  (app/config.py) плюс остальные os.getenv/os.environ.get в app/ и scripts/

Вывод детерминирован: всё сортируется, единственная «живая» строка — это
«Сгенерировано из коммита <short sha>», взятая через `git rev-parse --short HEAD`.
Скрипт ничего не пишет в БД, не ходит в сеть и не трогает файлы вне --out-dir.

Запуск (из корня worktree):
    .venv/bin/python scripts/gen_reference_docs.py
"""
from __future__ import annotations

import argparse
import ast
import inspect
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# --- безопасное окружение-пустышка, ДО импорта app.* -----------------------------------
# app.main тянет app.config -> app.db -> create_engine(...). Ничего из этого не должно
# коснуться настоящей БД, Redis или сети. sqlite:// (без пути) — БД в памяти процесса,
# соединение с ней ленивое (create_engine его не открывает), файла на диске не появляется.
os.environ["DATABASE_URL"] = "sqlite://"
os.environ.setdefault("SECRET_KEY", "gen-reference-docs-dummy-secret-key-0000000000000")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ["API_RATE_LIMIT_ENABLED"] = "false"
os.environ["RETENTION_ENABLED"] = "false"
os.environ.pop("PRODUCTION", None)

sys.path.insert(0, str(REPO_ROOT))


def git_short_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        return out.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def generated_line() -> str:
    return f"_Сгенерировано из коммита {git_short_sha()}._"


# =========================================================================================
# 1. API_ROUTES.md
# =========================================================================================


def _group_for_path(path: str) -> str:
    """Префикс группировки: /api/v2/<раздел>, /api/<раздел>, иначе первый сегмент пути."""
    segments = [s for s in path.split("/") if s]
    if not segments:
        return "/"
    if segments[0] == "api" and len(segments) > 1 and segments[1] == "v2":
        return "/api/v2/" + (segments[2] if len(segments) > 2 else "")
    if segments[0] == "api":
        return "/api/" + (segments[1] if len(segments) > 1 else "")
    return "/" + segments[0]


def _first_docstring_line(obj) -> str:
    doc = inspect.getdoc(obj)
    if not doc:
        return ""
    for line in doc.splitlines():
        line = line.strip()
        if line:
            return line
    return ""


@dataclass(frozen=True)
class RouteInfo:
    methods: tuple
    path: str
    module: str
    qualname: str
    doc: str


def collect_routes() -> list:
    import app.main as app_main
    from fastapi.routing import APIRoute

    routes: list[RouteInfo] = []
    for route in app_main.app.routes:
        if not isinstance(route, APIRoute):
            continue
        endpoint = route.endpoint
        methods = tuple(sorted((route.methods or set()) - {"HEAD", "OPTIONS"}))
        if not methods:
            methods = tuple(sorted(route.methods or set()))
        module = getattr(endpoint, "__module__", "?")
        qualname = getattr(endpoint, "__qualname__", getattr(endpoint, "__name__", "?"))
        doc = _first_docstring_line(endpoint)
        routes.append(RouteInfo(methods=methods, path=route.path, module=module, qualname=qualname, doc=doc))
    return routes


def render_api_routes_md() -> str:
    routes = collect_routes()
    groups: dict[str, list[RouteInfo]] = {}
    for r in routes:
        groups.setdefault(_group_for_path(r.path), []).append(r)

    lines: list[str] = []
    lines.append("# Справочник HTTP-маршрутов")
    lines.append("")
    lines.append(
        "Список получен через интроспекцию реального приложения — импортируется `app.main:app` "
        "и обходится `app.routes` (только `fastapi.routing.APIRoute`). Колонка «Обработчик» — "
        "модуль и имя функции, «Описание» — первая строка docstring обработчика, если она есть."
    )
    lines.append("")
    lines.append(f"Всего маршрутов: **{len(routes)}**, групп: **{len(groups)}**.")
    lines.append("")
    lines.append(generated_line())
    lines.append("")

    for group in sorted(groups.keys()):
        group_routes = sorted(groups[group], key=lambda r: (r.path, r.methods))
        lines.append(f"## `{group}` ({len(group_routes)})")
        lines.append("")
        lines.append("| Методы | Путь | Обработчик | Описание |")
        lines.append("|---|---|---|---|")
        for r in group_routes:
            methods_str = ", ".join(r.methods)
            handler = f"`{r.module}:{r.qualname}`"
            doc = r.doc.replace("|", "\\|") if r.doc else ""
            lines.append(f"| {methods_str} | `{r.path}` | {handler} | {doc} |")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


# =========================================================================================
# 2. DB_SCHEMA.md
# =========================================================================================


def _model_class_doc(cls) -> str:
    doc = inspect.getdoc(cls)
    if not doc:
        return ""
    # первый абзац: до первой пустой строки
    paragraph_lines: list[str] = []
    for line in doc.splitlines():
        if not line.strip():
            if paragraph_lines:
                break
            continue
        paragraph_lines.append(line.strip())
    return " ".join(paragraph_lines)


def _column_type_str(col) -> str:
    try:
        return str(col.type)
    except Exception:
        return type(col.type).__name__


def _column_default_str(col) -> str:
    parts = []
    if col.default is not None:
        arg = getattr(col.default, "arg", col.default)
        if callable(arg):
            name = getattr(arg, "__name__", None) or "<callable>"
            parts.append(f"python:{name}()")
        else:
            parts.append(f"python:{arg!r}")
    if col.server_default is not None:
        try:
            sd = col.server_default.arg
            sd_text = sd.text if hasattr(sd, "text") else str(sd)
        except Exception:
            sd_text = str(col.server_default)
        parts.append(f"server:{sd_text}")
    return "; ".join(parts) if parts else ""


def render_db_schema_md() -> str:
    import app.models  # noqa: F401  (populates Base.metadata)
    import app.v2.models  # noqa: F401  (populates Base.metadata)
    from app.db import Base

    # таблица -> класс модели (для docstring и для порядка "по классу", если понадобится)
    table_to_class: dict[str, type] = {}
    for mapper in Base.registry.mappers:
        cls = mapper.class_
        table = getattr(cls, "__tablename__", None)
        if table:
            table_to_class[table] = cls

    metadata = Base.metadata
    lines: list[str] = []
    lines.append("# Справочник схемы базы данных")
    lines.append("")
    lines.append(
        "Получено интроспекцией `SQLAlchemy`-метаданных: импортируются `app.models` и "
        "`app.v2.models`, дальше используется `Base.metadata` (та же `Base`, что в `app/db.py`)."
    )
    lines.append("")
    lines.append(f"Всего таблиц: **{len(metadata.tables)}**.")
    lines.append("")
    lines.append(generated_line())
    lines.append("")
    lines.append("## Таблицы")
    lines.append("")

    for table_name in sorted(metadata.tables.keys()):
        table = metadata.tables[table_name]
        cls = table_to_class.get(table_name)
        lines.append(f"### `{table_name}`" + (f" — `{cls.__module__}.{cls.__qualname__}`" if cls else ""))
        lines.append("")
        if cls is not None:
            doc = _model_class_doc(cls)
            if doc:
                lines.append(doc)
                lines.append("")

        lines.append("| Колонка | Тип | Nullable | PK | FK | Умолчание |")
        lines.append("|---|---|---|---|---|---|")
        for col in sorted(table.columns, key=lambda c: c.name):
            fks = ", ".join(sorted(f"{fk.column.table.name}.{fk.column.name}" for fk in col.foreign_keys))
            lines.append(
                "| `{name}` | {type} | {nullable} | {pk} | {fk} | {default} |".format(
                    name=col.name,
                    type=_column_type_str(col),
                    nullable="да" if col.nullable else "нет",
                    pk="да" if col.primary_key else "",
                    fk=fks,
                    default=_column_default_str(col).replace("|", "\\|"),
                )
            )
        lines.append("")

        if table.indexes:
            lines.append("Индексы:")
            lines.append("")
            for idx in sorted(table.indexes, key=lambda i: i.name or ""):
                cols = ", ".join(c.name for c in idx.columns)
                uniq = " (unique)" if idx.unique else ""
                lines.append(f"- `{idx.name}`{uniq}: {cols}")
            lines.append("")

    lines.append("## Ревизии Alembic")
    lines.append("")
    lines.append(
        "По порядку (`revision` → `down_revision`, от `0001_baseline`), первая строка "
        "docstring файла ревизии."
    )
    lines.append("")
    revisions = collect_alembic_revisions()
    lines.append("| Revision | Первая строка docstring | Файл |")
    lines.append("|---|---|---|")
    for rev in revisions:
        doc = rev.doc.replace("|", "\\|") if rev.doc else ""
        lines.append(f"| `{rev.revision}` | {doc} | `{rev.file_name}` |")
    lines.append("")

    return "\n".join(lines).rstrip() + "\n"


@dataclass(frozen=True)
class RevisionInfo:
    revision: str
    down_revision: str | None
    doc: str
    file_name: str


def collect_alembic_revisions() -> list:
    versions_dir = REPO_ROOT / "alembic" / "versions"
    revisions: dict[str, RevisionInfo] = {}
    for path in sorted(versions_dir.glob("*.py")):
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
        except Exception:
            continue
        module_doc = ast.get_docstring(tree) or ""
        first_line = ""
        for line in module_doc.splitlines():
            if line.strip():
                first_line = line.strip()
                break

        revision_val = None
        down_revision_val = None
        for node in tree.body:
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                name = node.target.id
                if name == "revision" and node.value is not None:
                    try:
                        revision_val = ast.literal_eval(node.value)
                    except Exception:
                        pass
                elif name == "down_revision" and node.value is not None:
                    try:
                        down_revision_val = ast.literal_eval(node.value)
                    except Exception:
                        pass
        if revision_val is None:
            # запасной путь: имя файла без .py и числового префикса-суффикса не трогаем
            revision_val = path.stem
        revisions[revision_val] = RevisionInfo(
            revision=revision_val,
            down_revision=down_revision_val,
            doc=first_line,
            file_name=path.name,
        )

    # топологический порядок от корня (down_revision is None) по цепочке down_revision
    by_down: dict[str | None, list[RevisionInfo]] = {}
    for rev in revisions.values():
        by_down.setdefault(rev.down_revision, []).append(rev)
    for lst in by_down.values():
        lst.sort(key=lambda r: r.revision)

    ordered: list[RevisionInfo] = []
    seen: set[str] = set()

    def walk(down_key: str | None) -> None:
        for rev in by_down.get(down_key, []):
            if rev.revision in seen:
                continue
            seen.add(rev.revision)
            ordered.append(rev)
            walk(rev.revision)

    walk(None)
    # любые ревизии, не дотянувшиеся до цепочки (например, ветвление) — довесить по имени файла
    for rev in sorted(revisions.values(), key=lambda r: r.file_name):
        if rev.revision not in seen:
            ordered.append(rev)
            seen.add(rev.revision)

    return ordered


# =========================================================================================
# 3. ENV_VARS.md
# =========================================================================================

_SECRET_NAME_RE = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD)$", re.IGNORECASE)


@dataclass(frozen=True)
class EnvVarInfo:
    name: str
    default_repr: str
    masked: bool
    location: str
    description: str
    source: str  # "Settings" | "other"


def _preceding_comment(lines: list[str], lineno_1based: int) -> str:
    """Комментарий прямо над строкой (без пустой строки-разделителя), склеенный в одну."""
    collected: list[str] = []
    i = lineno_1based - 2  # индекс строки перед lineno (0-based)
    while i >= 0:
        raw = lines[i]
        stripped = raw.strip()
        if stripped.startswith("#"):
            text = stripped.lstrip("#").lstrip(":").strip()
            collected.insert(0, text)
            i -= 1
            continue
        break
    return " ".join(c for c in collected if c)


def _default_repr_for_call(call: ast.Call) -> str | None:
    if len(call.args) < 2:
        return None
    try:
        return repr(ast.literal_eval(call.args[1]))
    except Exception:
        try:
            return ast.unparse(call.args[1])
        except Exception:
            return "<сложное выражение>"


def collect_settings_env_vars() -> list:
    config_path = REPO_ROOT / "app" / "config.py"
    source = config_path.read_text(encoding="utf-8")
    lines = source.splitlines()
    tree = ast.parse(source, filename=str(config_path))

    settings_class = None
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "Settings":
            settings_class = node
            break
    if settings_class is None:
        return []

    results: list[EnvVarInfo] = []
    for node in ast.walk(settings_class):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        is_getenv = (
            isinstance(func, ast.Attribute)
            and func.attr == "getenv"
            and isinstance(func.value, ast.Name)
            and func.value.id == "os"
        )
        if not is_getenv or not node.args:
            continue
        try:
            name = ast.literal_eval(node.args[0])
        except Exception:
            continue
        if not isinstance(name, str):
            continue
        default_repr = _default_repr_for_call(node)
        masked = bool(_SECRET_NAME_RE.search(name))
        lineno = node.lineno
        description = _preceding_comment(lines, lineno)
        results.append(
            EnvVarInfo(
                name=name,
                default_repr=default_repr if default_repr is not None else "",
                masked=masked,
                location=f"app/config.py:{lineno}",
                description=description,
                source="Settings",
            )
        )
    # убрать дубликаты по имени (первое вхождение выигрывает — сверху вниз по файлу)
    seen: set[str] = set()
    deduped: list[EnvVarInfo] = []
    for item in results:
        if item.name in seen:
            continue
        seen.add(item.name)
        deduped.append(item)
    return deduped


_GETENV_RE = re.compile(
    r"os\.(?:getenv|environ\.get)\(\s*(['\"])([A-Z][A-Z0-9_]*)\1(?:\s*,\s*(.+?))?\s*\)"
)


def collect_other_env_vars(known_names: set) -> list:
    """os.getenv/os.environ.get вне app/config.py — по всему app/ и scripts/."""
    results: dict[str, EnvVarInfo] = {}
    for base in (REPO_ROOT / "app", REPO_ROOT / "scripts"):
        for path in sorted(base.rglob("*.py")):
            if path == REPO_ROOT / "app" / "config.py":
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except Exception:
                continue
            lines = text.splitlines()
            for i, line in enumerate(lines):
                m = _GETENV_RE.search(line)
                if not m:
                    continue
                name = m.group(2)
                default_raw = m.group(3)
                default_repr = default_raw.strip() if default_raw else ""
                masked = bool(_SECRET_NAME_RE.search(name))
                rel = path.relative_to(REPO_ROOT)
                lineno = i + 1
                description = _preceding_comment(lines, lineno)
                key = f"{name}@{rel}:{lineno}"
                results[key] = EnvVarInfo(
                    name=name,
                    default_repr=default_repr,
                    masked=masked,
                    location=f"{rel}:{lineno}",
                    description=description,
                    source="other",
                )
    return sorted(results.values(), key=lambda e: (e.name, e.location))


def render_env_vars_md() -> str:
    settings_vars = collect_settings_env_vars()
    known_names = {v.name for v in settings_vars}
    other_vars = collect_other_env_vars(known_names)

    lines: list[str] = []
    lines.append("# Справочник переменных окружения")
    lines.append("")
    lines.append(
        "Получено статическим разбором исходников (`ast`), без выполнения приложения "
        "с реальным окружением и без чтения `.env`. Раздел «Settings» — поля "
        "`app/config.py:Settings`, читаемые через `os.getenv`. Раздел «Прочее» — "
        "`os.getenv`/`os.environ.get` в `app/` и `scripts/` вне `app/config.py`."
    )
    lines.append("")
    lines.append(
        "Переменные, чьё имя оканчивается на `KEY`/`TOKEN`/`SECRET`/`PASSWORD`, помечены "
        "как секретные — для них показано только «задан ли непустой default в коде», "
        "само значение default никогда не печатается."
    )
    lines.append("")
    total = len(settings_vars) + len(other_vars)
    lines.append(f"Всего переменных: **{total}** (Settings: {len(settings_vars)}, прочее: {len(other_vars)}).")
    lines.append("")
    lines.append(generated_line())
    lines.append("")

    def render_table(vars_list: list) -> list:
        out = ["| Переменная | Default | Где читается | Описание |", "|---|---|---|---|"]
        for v in sorted(vars_list, key=lambda e: e.name):
            if v.masked:
                has_default = bool(v.default_repr) and v.default_repr not in ("''", '""', "")
                default_cell = "секрет — есть непустой default в коде" if has_default else "секрет — default пуст"
            else:
                default_cell = f"`{v.default_repr}`" if v.default_repr else ""
            desc = v.description.replace("|", "\\|") if v.description else ""
            out.append(f"| `{v.name}` | {default_cell} | `{v.location}` | {desc} |")
        return out

    lines.append("## Settings (`app/config.py`)")
    lines.append("")
    lines.extend(render_table(settings_vars))
    lines.append("")

    lines.append("## Прочее (вне Settings)")
    lines.append("")
    if other_vars:
        lines.extend(render_table(other_vars))
    else:
        lines.append("_Не найдено._")
    lines.append("")

    return "\n".join(lines).rstrip() + "\n"


# =========================================================================================
# main
# =========================================================================================


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=REPO_ROOT / "docs" / "reference",
        help="Куда писать три .md файла (по умолчанию docs/reference/).",
    )
    args = parser.parse_args()

    out_dir: Path = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    files = {
        "API_ROUTES.md": render_api_routes_md(),
        "DB_SCHEMA.md": render_db_schema_md(),
        "ENV_VARS.md": render_env_vars_md(),
    }
    for name, content in files.items():
        (out_dir / name).write_text(content, encoding="utf-8")
        print(f"wrote {out_dir / name}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

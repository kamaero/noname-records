"""`scripts/gen_reference_docs.py`: справочники должны собираться из кода без побочных
эффектов — без сети, без настоящей БД, без утечки значений секретов.

Скрипт запускается отдельным процессом (тем же интерпретатором, что запустил pytest):
он сам расставляет безопасное окружение-пустышку до импорта `app.*`, и сравнение
"что видно снаружи" честнее, чем прямой вызов функций внутри процесса тестов (где
`conftest.py` уже успел навязать свои переменные окружения).
"""
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "gen_reference_docs.py"

# Похоже на настоящий секрет: длинная строка из букв/цифр/-_/. без пробелов, не наш
# собственный маркер маскировки и не safe-плейсхолдер из исходников.
_SECRET_LOOKING_RE = re.compile(r"(sk-[A-Za-z0-9_\-]{10,}|AKIA[0-9A-Z]{12,}|[A-Za-z0-9+/]{40,}={0,2})")
_SAFE_DEFAULT_STRINGS = {
    "change-me",
    "gen-reference-docs-dummy-secret-key-0000000000000",
}


def _run_generator(out_dir: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--out-dir", str(out_dir)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_generator_writes_three_files_with_known_entries(tmp_path):
    out_dir = tmp_path / "reference"
    result = _run_generator(out_dir)
    assert result.returncode == 0, result.stderr

    api_routes = out_dir / "API_ROUTES.md"
    db_schema = out_dir / "DB_SCHEMA.md"
    env_vars = out_dir / "ENV_VARS.md"
    for f in (api_routes, db_schema, env_vars):
        assert f.exists(), f"{f} не создан"
        assert f.stat().st_size > 0

    api_routes_text = api_routes.read_text(encoding="utf-8")
    db_schema_text = db_schema.read_text(encoding="utf-8")
    env_vars_text = env_vars.read_text(encoding="utf-8")

    # известные маршруты
    assert "/api/me" in api_routes_text
    assert "/api/v2/chapters" in api_routes_text or "/api/v2/books" in api_routes_text
    assert "Всего маршрутов" in api_routes_text

    # известная таблица
    assert "sound_markers" in db_schema_text
    assert "Ревизии Alembic" in db_schema_text
    assert "0001_baseline" in db_schema_text

    # известная переменная окружения
    assert "ELEVENLABS_API_KEY" in env_vars_text
    assert "RQ_QUEUES" in env_vars_text  # вне Settings, найдена grep'ом по app/


def test_generated_line_has_short_git_sha(tmp_path):
    out_dir = tmp_path / "reference"
    result = _run_generator(out_dir)
    assert result.returncode == 0, result.stderr

    sha = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    for name in ("API_ROUTES.md", "DB_SCHEMA.md", "ENV_VARS.md"):
        text = (out_dir / name).read_text(encoding="utf-8")
        assert f"Сгенерировано из коммита {sha}" in text


def test_no_line_contains_a_real_looking_secret(tmp_path):
    out_dir = tmp_path / "reference"
    result = _run_generator(out_dir)
    assert result.returncode == 0, result.stderr

    for name in ("API_ROUTES.md", "DB_SCHEMA.md", "ENV_VARS.md"):
        text = (out_dir / name).read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            match = _SECRET_LOOKING_RE.search(line)
            assert not match, f"{name}:{lineno} похоже на настоящий секрет: {line!r}"


def test_secret_named_env_vars_never_show_their_default_literal(tmp_path):
    """*_KEY/*_TOKEN/*_SECRET/PASSWORD — маскируются: строка таблицы содержит только
    пометку 'секрет — ...', а не сам литерал default из app/config.py."""
    out_dir = tmp_path / "reference"
    result = _run_generator(out_dir)
    assert result.returncode == 0, result.stderr

    env_vars_text = (out_dir / "ENV_VARS.md").read_text(encoding="utf-8")
    row_re = re.compile(r"^\| `([A-Z0-9_]+)` \| (.+?) \| `([^`]+)` \| (.*) \|$")
    checked_masked = 0
    for line in env_vars_text.splitlines():
        m = row_re.match(line)
        if not m:
            continue
        name, default_cell, _location, _desc = m.groups()
        if re.search(r"(KEY|TOKEN|SECRET|PASSWORD)$", name):
            checked_masked += 1
            assert default_cell.startswith("секрет"), (
                f"{name}: default-ячейка не замаскирована: {default_cell!r}"
            )
    assert checked_masked >= 10  # в app/config.py их больше десятка — sanity, что парсер вообще работал


def test_generator_touches_only_the_requested_out_dir(tmp_path):
    """Никаких файлов вне --out-dir; ни одна БД, ни один сокет не создаются рядом."""
    out_dir = tmp_path / "reference"
    marker_before = set(REPO_ROOT.glob("*.db"))
    result = _run_generator(out_dir)
    assert result.returncode == 0, result.stderr
    marker_after = set(REPO_ROOT.glob("*.db"))
    assert marker_before == marker_after, "скрипт создал файл БД рядом с репозиторием"
    assert sorted(p.name for p in out_dir.iterdir()) == ["API_ROUTES.md", "DB_SCHEMA.md", "ENV_VARS.md"]

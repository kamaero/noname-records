"""Время уходит на фронт с поясом: иначе браузер читает UTC как местное."""
import pathlib
import re
from datetime import UTC, datetime, timedelta, timezone

from app.time_utils import iso_utc


def test_a_naive_utc_moment_gets_its_offset():
    assert iso_utc(datetime(2026, 9, 29, 15, 0, 0, 123456)) == "2026-09-29T15:00:00.123+00:00"


def test_an_aware_moment_is_told_in_utc():
    msk = timezone(timedelta(hours=3))
    assert iso_utc(datetime(2026, 9, 29, 18, 0, tzinfo=msk)) == "2026-09-29T15:00:00.000+00:00"
    assert iso_utc(datetime(2026, 9, 29, 15, 0, tzinfo=UTC)).endswith("+00:00")


def test_no_moment_is_none():
    assert iso_utc(None) is None


def test_no_bare_isoformat_of_a_database_time_is_left():
    """Сторож: время наружу — через `iso_utc` или `format_dt`, не голым `.isoformat()`."""
    root = pathlib.Path(__file__).resolve().parents[1] / "app"
    allowed = {"app/time_utils.py", "app/services/shared_runtime.py"}
    offenders = []
    for path in root.rglob("*.py"):
        rel = str(path.relative_to(root.parent))
        if rel in allowed:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            # datetime.now(timezone.utc).isoformat() уже с поясом — это не наивное время из базы
            if ".isoformat(" in line and "timezone.utc" not in line and "tz=" not in line and 'Z"' not in line:
                offenders.append(f"{rel}:{number}: {line.strip()}")
    assert offenders == []

from pathlib import Path


def test_app_has_no_datetime_utcnow_calls() -> None:
    app_dir = Path(__file__).resolve().parent.parent / "app"
    offenders: list[str] = []
    for path in app_dir.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "datetime.utcnow(" in text:
            offenders.append(str(path.relative_to(app_dir.parent)))
    assert offenders == [], f"Found forbidden datetime.utcnow calls in: {', '.join(sorted(offenders))}"


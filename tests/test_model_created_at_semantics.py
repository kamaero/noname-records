import re
from pathlib import Path


def test_model_created_at_has_no_onupdate() -> None:
    models_path = Path(__file__).resolve().parent.parent / "app" / "models.py"
    text = models_path.read_text(encoding="utf-8")
    bad_lines = []
    for line in text.splitlines():
        if "created_at" in line and "mapped_column" in line and "onupdate=" in line:
            bad_lines.append(line.strip())
    assert bad_lines == [], "created_at must not use onupdate: " + " | ".join(bad_lines)


"""Ключ читается в одном месте — иначе ключ с сайта видит один шаг, а другой ходит со старым."""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1] / "app"
DIRECT = re.compile(r"settings\.[a-z_]*(api_key|speech_key)\b|env_value\(\"[A-Z_]*_KEY\"\)")


def test_no_module_reads_a_provider_key_directly():
    offenders = []
    for path in ROOT.rglob("*.py"):
        rel = path.relative_to(ROOT).as_posix()
        if rel in {"config.py", "services/provider_keys.py"}:
            continue
        for no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if DIRECT.search(line) and not line.lstrip().startswith("#"):
                offenders.append(f"{rel}:{no}")
    assert offenders == []

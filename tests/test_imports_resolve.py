"""Каждый импорт из `app` в приложении и скриптах указывает на то, что существует.

Импорт внутри функции проверяется только при её вызове. 2026-09-29 удалили модуль,
из которого `app/v2/character_map.py` брал `assess_char_map` прямо в теле функции, — и
широкий `except` вокруг принял ошибку импорта за «корпуса канона нет»: карта персонажей
молча перестала сверяться с каноном автора. Этот тест ловит такое до выкатки.
"""
import ast
import importlib
import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _imports():
    for path in [*(ROOT / "app").rglob("*.py"), *(ROOT / "scripts").rglob("*.py")]:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] == "app":
                yield path, node


def test_every_app_import_resolves_even_inside_functions():
    broken = []
    for path, node in _imports():
        where = f"{path.relative_to(ROOT)}:{node.lineno}"
        if importlib.util.find_spec(node.module) is None:
            broken.append(f"{where}: нет модуля {node.module}")
            continue
        module = importlib.import_module(node.module)
        for alias in node.names:
            if alias.name == "*" or hasattr(module, alias.name):
                continue
            if importlib.util.find_spec(f"{node.module}.{alias.name}") is None:
                broken.append(f"{where}: в {node.module} нет {alias.name}")
    assert broken == []

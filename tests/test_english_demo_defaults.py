"""Системное пустое имя переводится; имя файла или подпись пользователя сохраняются."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.demo_audio import demo_title
from app.services.demo_fetch import FALLBACK_NAME, _save


@pytest.mark.parametrize("name", ["", "demo.wav", "демо.wav"])
def test_empty_generated_demo_title_is_english(name):
    assert demo_title(name, "Sample Actor") == "Demo"


def test_custom_demo_description_is_preserved():
    assert demo_title("Тихий вечер.wav", "Sample Actor") == "Тихий вечер"


@pytest.mark.parametrize("headers,expected", [({}, "demo.bin"),
    ({"content-disposition": 'attachment; filename="Мой исходный файл.wav"'}, "Мой исходный файл.wav")])
def test_fetch_fallback_does_not_rename_a_named_source_file(tmp_path, headers, expected):
    response = SimpleNamespace(headers=headers, iter_content=lambda size: [b"original-audio"])
    path = Path(_save(response, str(tmp_path), FALLBACK_NAME))
    assert path.name == expected and path.read_bytes() == b"original-audio"

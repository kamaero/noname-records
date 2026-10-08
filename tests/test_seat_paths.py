"""Папка данных: всё, что пишет пользователь, — под DATA_DIR, а не относительно рабочего каталога.
В настольной версии рабочий каталог — внутри пакета программы, писать туда нельзя."""
import re
from pathlib import Path

from app import paths
from app.config import settings

ROOT = Path(__file__).resolve().parents[1]


def test_data_path_follows_the_setting(monkeypatch, tmp_path):
    odd = tmp_path / "Тестовый Пользователь" / "AppData" / "Noname Records"
    monkeypatch.setattr(settings, "data_dir", str(odd))
    assert paths.data_dir() == odd
    assert paths.data_path("book_sources", "b1") == odd / "book_sources" / "b1"


def test_relative_default_is_resolved(monkeypatch):
    monkeypatch.setattr(settings, "data_dir", "data")
    assert paths.data_dir().is_absolute()


def test_user_data_modules_use_data_path(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path / "д а"))
    from app.services import book_images, book_import, consilium_engine, lore_import
    from app.pipeline import attribution_artifact
    assert book_import.book_source_dir() == tmp_path / "д а" / "book_sources"
    assert Path(book_images.image_dir("b1")) == tmp_path / "д а" / "lore" / "books" / "b1"
    assert consilium_engine.artifact_root() == tmp_path / "д а" / "book_reports"
    assert lore_import.lore_image_root() == tmp_path / "д а" / "lore"
    assert attribution_artifact.base_dir() == tmp_path / "д а" / "book_reports"


def test_no_hardcoded_user_data_paths():
    pattern = re.compile(r"""["']data/(book_sources|lore|book_reports|char_memory)""")
    offenders = [str(p.relative_to(ROOT)) for p in (ROOT / "app").rglob("*.py")
                 if p.name != "paths.py" and pattern.search(p.read_text(encoding="utf-8"))]
    assert offenders == []


def test_dictionaries_and_canon_follow_the_data_folder(monkeypatch, tmp_path):
    # Викисловарь, авторские ударения и канон — файлы, которые кладёт человек (или скрипт
    # импорта): в настольной версии им место в папке данных, а не внутри пакета программы.
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    from app.services import canon_seed
    from app.v2 import stress, stress_forms
    assert stress_forms.default_path() == tmp_path / "stress_forms.sqlite"
    assert stress.author_stress_dir() == tmp_path / "author_stress"
    assert Path(canon_seed.canon_db_path()) == tmp_path / "canon_kb.sqlite"


def test_forms_table_in_the_data_folder_is_opened(monkeypatch, tmp_path):
    from app.v2 import stress_forms
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    monkeypatch.setattr(settings, "stress_forms_db_path", "")
    monkeypatch.setattr(stress_forms, "_DEFAULT", None)
    opened = []
    monkeypatch.setattr(stress_forms, "open_lookup", lambda path: opened.append(path) or (lambda word: None))
    stress_forms.default_forms_lookup("сомнамбула")
    assert opened == [tmp_path / "stress_forms.sqlite"]


def test_no_data_folder_joins_outside_paths_py():
    # «data» как часть пути через Path(...) / "data" или os.path.join(..., "data", ...)
    pattern = re.compile(r"""(/\s*["']data["']|join\([^)]*["']data["']\s*,)""")
    offenders = [str(p.relative_to(ROOT)) for p in (ROOT / "app").rglob("*.py")
                 if p.name != "paths.py" and pattern.search(p.read_text(encoding="utf-8"))]
    assert offenders == []

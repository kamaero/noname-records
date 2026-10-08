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

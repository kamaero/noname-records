"""Сценарий роли в PDF — раскладка `RoleExport` на страницы A4.

PDF строится на сервере, а не печатью браузера: в окне настольной программы (Tauri)
печать ненадёжна, а режиссёру нужен файл, который можно отправить актёру.
"""
from __future__ import annotations

from pathlib import Path

FONT_DIR = Path(__file__).resolve().parents[1] / "assets" / "fonts"
#: Noname Sans несёт только кириллицу, латиницу и цифры даёт Plex — запасным шрифтом fpdf2
FONTS = {"noname": "NonameSans-Cyrillic.ttf", "plex": "IBMPlexSans-Latin.ttf"}

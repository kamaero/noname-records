"""Шрифты фронта (woff2) → TTF для PDF сценария роли.

fpdf2 читает TTF; woff2 — тот же шрифт в сжатой обёртке. Берём именно шрифты приложения:
Noname Sans — это Plex со сдвинутым знаком ударения (`center_stress_accent.py`), и в PDF
знак должен стоять там же, где в Читалке. Запускать после каждой правки woff2:
    python3 scripts/fonts/pdf_fonts.py
Нужен fontTools с brotli (requirements-dev.txt).
"""
from pathlib import Path

from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parents[2]
SOURCES = {
    "noname-sans-cyrillic.woff2": "NonameSans-Cyrillic.ttf",
    "ibm-plex-sans-latin.woff2": "IBMPlexSans-Latin.ttf",
}


def main() -> None:
    target = ROOT / "app" / "assets" / "fonts"
    target.mkdir(parents=True, exist_ok=True)
    for source, name in SOURCES.items():
        font = TTFont(ROOT / "frontend" / "src" / "assets" / "fonts" / source)
        font.flavor = None
        font.save(target / name)
        print(target / name)


if __name__ == "__main__":
    main()

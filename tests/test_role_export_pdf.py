"""Сценарий роли в PDF: шрифт приложения, ударение над гласной, всё на месте."""
from fontTools.ttLib import TTFont

from app.v2.role_export_pdf import FONT_DIR, FONTS

#: всё, что сценарий пишет сам, плюс обычная типографика книги
NEEDED = "АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯабвгдеёжзийклмнопрстуфхцчшщъыьэюя" \
         "ABCXYZabcxyz0123456789«»—–…:;!?.,[]()_́"


def test_the_fonts_are_shipped_with_the_app():
    for name in FONTS.values():
        assert (FONT_DIR / name).is_file()
    assert (FONT_DIR / "OFL.txt").read_text(encoding="utf-8").count("SIL OPEN FONT LICENSE") >= 1


def test_together_they_cover_what_a_script_prints():
    cmaps = [TTFont(FONT_DIR / name).getBestCmap() for name in FONTS.values()]
    missing = [ch for ch in NEEDED if not any(ord(ch) in cmap for cmap in cmaps)]
    assert missing == []


def test_shaping_is_available():
    # без формирования знак ударения уезжает вправо от гласной — PDF без него хуже текста
    import uharfbuzz  # noqa: F401

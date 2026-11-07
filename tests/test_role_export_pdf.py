"""Сценарий роли в PDF: шрифт приложения, ударение над гласной, всё на месте."""
import io

import pytest
from fontTools.ttLib import TTFont
from pypdf import PdfReader
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.v2.role_export import Chapter, Line, Piece, build_role_export
from app.v2.role_export_pdf import FONT_DIR, FONTS, render_pdf
from tests.role_export_book import BOOK, build_book

#: всё, что сценарий пишет сам, плюс обычная типографика книги
NEEDED = "АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯабвгдеёжзийклмнопрстуфхцчшщъыьэюя" \
         "ABCXYZabcxyz0123456789«»—–…:;!?.,[]()_́"


def test_the_fonts_are_shipped_with_the_app():
    for name in FONTS.values():
        assert (FONT_DIR / name).is_file()
    assert (FONT_DIR / "OFL.txt").read_text(encoding="utf-8").count("SIL OPEN FONT LICENSE") >= 1
    assert "bitstream-vera" in (FONT_DIR / "DejaVu-LICENSE.txt").read_text(encoding="utf-8")


def test_together_they_cover_what_a_script_prints():
    cmaps = [TTFont(FONT_DIR / name).getBestCmap() for name in FONTS.values()]
    missing = [ch for ch in NEEDED if not any(ord(ch) in cmap for cmap in cmaps)]
    assert missing == []


def test_shaping_is_available():
    # без формирования знак ударения уезжает вправо от гласной — PDF без него хуже текста
    import uharfbuzz  # noqa: F401


@pytest.fixture()
def export():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with sessionmaker(bind=engine)() as session:
        build_book(session)
        yield build_role_export(session, BOOK, "Мира")


def _text(data: bytes) -> str:
    return "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(data)).pages)


def test_it_is_a_pdf_with_the_lines_in_it(export):
    data = render_pdf(export)
    text = _text(data)

    assert data[:5] == b"%PDF-"
    for needle in ("Мира", "Тихая гавань", "Глава 3. Тайна", "TG_Ch03_Mira_AnnaTestova.wav", "Знаю"):
        assert needle in text


def test_the_title_is_in_the_metadata(export):
    assert PdfReader(io.BytesIO(render_pdf(export))).metadata.title == "Мира — Тихая гавань"


def test_a_big_role_breaks_into_pages(export):
    lines = [Line(pieces=[Piece("— Реплика номер %d, довольно длинная, чтобы занять строку." % n, True)],
                  cue_speaker="Ярош", cue_text="— Подводка.") for n in range(400)]
    export.chapters = [Chapter(index=1, title="Глава 1", filename="", lines=lines)]

    assert len(PdfReader(io.BytesIO(render_pdf(export))).pages) > 5


def test_a_letter_outside_the_app_fonts_still_reaches_the_pdf(export):
    # Ω и ₽ в книгах бывают; без запасного шрифта они молча пропадали из реплики
    export.chapters[0].lines[0].pieces = [Piece("Слово Ω и 100 ₽ конец", True)]

    text = _text(render_pdf(export))

    assert "Ω" in text and "₽" in text


def test_a_sign_no_font_has_is_marked_and_named_not_dropped(export):
    export.chapters[0].lines[0].pieces = [Piece("Знак 漢 конец", True)]

    text = _text(render_pdf(export))

    assert "�" in text
    assert "text version" in text and "U+6F22" in text

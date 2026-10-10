"""Сценарий роли в PDF — раскладка `RoleExport` на страницы A4.

PDF строится на сервере, а не печатью браузера: в окне настольной программы (Tauri)
печать ненадёжна, а режиссёру нужен файл, который можно отправить актёру.
"""
from __future__ import annotations

import functools
from pathlib import Path

from app.v2.role_export import Chapter, Line, Piece, RoleExport, chapter_heading

FONT_DIR = Path(__file__).resolve().parents[1] / "assets" / "fonts"
#: Noname Sans несёт только кириллицу, латиницу и цифры даёт Plex — запасным шрифтом fpdf2.
#: DejaVu Sans — последний запасной: без него греческие буквы, ₽ и прочие знаки книги
#: молча пропадали из реплики (финальное ревью проекта 2).
FONTS = {"noname": "NonameSans-Cyrillic.ttf", "plex": "IBMPlexSans-Latin.ttf", "dejavu": "DejaVuSans.ttf"}
#: знак на месте символа, которого нет ни в одном шрифте, — видно, что здесь что-то было
REPLACEMENT = "\ufffd"

INK = (20, 20, 20)
MUTED = (115, 115, 115)
ACCENT = (40, 70, 150)
LINE_H = 5.6


@functools.cache
def _covered() -> frozenset[int]:
    from fontTools.ttLib import TTFont

    codes: set[int] = set()
    for filename in FONTS.values():
        codes |= set(TTFont(FONT_DIR / filename).getBestCmap())
    return frozenset(codes)


def _strings(export: RoleExport):
    yield from (export.role, export.book_title, export.actor, export.description, export.facts, export.note)
    for chapter in export.chapters:
        yield chapter.title
        yield chapter.filename
        for line in chapter.lines:
            yield line.cue_speaker
            yield line.cue_text
            yield from (piece.text for piece in line.pieces)


def missing_chars(export: RoleExport) -> list[str]:
    """Знаки сценария, которых нет ни в одном шрифте PDF, — в порядке появления."""
    covered = _covered()
    seen: dict[str, None] = {}
    for text in _strings(export):
        for ch in text or "":
            if not ch.isspace() and ord(ch) not in covered:
                seen.setdefault(ch, None)
    return list(seen)


def _clean(text: str, missing: set[str]) -> str:
    return "".join(REPLACEMENT if ch in missing else ch for ch in text)


def _cleaned(export: RoleExport, missing: set[str]) -> RoleExport:
    """Копия сценария, где непечатаемые знаки заменены видимым «�»: молча пропавший знак
    меняет реплику, а замена хотя бы видна актёру."""
    def c(text: str) -> str:
        return _clean(text, missing)

    return RoleExport(
        book_title=c(export.book_title), role=c(export.role), actor=c(export.actor),
        description=c(export.description), facts=c(export.facts), note=c(export.note),
        chapters=[
            Chapter(index=chapter.index, title=c(chapter.title), filename=c(chapter.filename), lines=[
                Line(pieces=[Piece(c(piece.text), piece.own) for piece in line.pieces],
                     cue_speaker=c(line.cue_speaker), cue_text=c(line.cue_text))
                for line in chapter.lines
            ])
            for chapter in export.chapters
        ],
    )


def _fpdf():
    from fpdf import FPDF

    pdf = FPDF(format="A4")
    pdf.set_margins(18, 16, 18)
    pdf.set_auto_page_break(True, margin=16)
    for family, filename in FONTS.items():
        pdf.add_font(family, "", str(FONT_DIR / filename))
    pdf.set_font("noname", size=11)
    pdf.set_fallback_fonts(["plex", "dejavu"])
    # якоря знака ударения в шрифте работают только с формированием текста
    pdf.set_text_shaping(True)
    return pdf


def render_pdf(export: RoleExport) -> bytes:
    missing = missing_chars(export)
    export = _cleaned(export, set(missing))
    pdf = _fpdf()
    pdf.set_title(f"{export.role} — {export.book_title}")
    pdf.set_creator("Noname Records")
    pdf.add_page()
    left = pdf.l_margin

    pdf.set_font_size(18)
    pdf.set_text_color(*INK)
    pdf.multi_cell(0, 9, f"{export.role} — «{export.book_title}»", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font_size(10.5)
    for label, value in (("Актёр", export.actor), ("Кто это", export.description), ("", export.facts),
                         ("Заметка режиссёра", export.note)):
        if value:
            pdf.multi_cell(0, 5.4, f"{label}: {value}" if label else value, new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(*MUTED)
    pdf.multi_cell(0, 5.4, f"Реплик: {export.line_count}, глав: {len(export.chapters)}. "
                           "Ударения стоят в редких словах.", new_x="LMARGIN", new_y="NEXT")
    if missing:
        # имя знака, а не сам знак: напечатать его PDF как раз и не может
        codes = ", ".join(f"U+{ord(ch):04X}" for ch in missing)
        pdf.set_text_color(*ACCENT)
        pdf.multi_cell(0, 5.4, f"Некоторые знаки PDF не умеет показать и заменил на {REPLACEMENT} ({codes}) — "
                               "они есть в текстовой версии сценария.", new_x="LMARGIN", new_y="NEXT")

    for chapter in export.chapters:
        pdf.ln(5)
        pdf.set_font_size(14)
        pdf.set_text_color(*INK)
        pdf.multi_cell(0, 7.5, chapter_heading(chapter), new_x="LMARGIN", new_y="NEXT")
        if chapter.filename:
            pdf.set_font_size(9.5)
            pdf.set_text_color(*MUTED)
            pdf.multi_cell(0, 5, f"Файл записи: {chapter.filename}", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(1.5)
        for line in chapter.lines:
            if line.cue_text:
                pdf.set_font_size(9.5)
                pdf.set_text_color(*MUTED)
                pdf.set_x(left + 6)
                label = f"{line.cue_speaker}: " if line.cue_speaker else ""
                pdf.multi_cell(0, 5, label + line.cue_text, new_x="LMARGIN", new_y="NEXT")
            pdf.set_font_size(11)
            page, top = pdf.page, pdf.get_y()
            pdf.set_left_margin(left + 4)
            pdf.set_x(left + 4)
            pdf.set_text_color(*ACCENT)
            pdf.write(LINE_H, f"{export.role}: ")
            for piece in line.pieces:
                pdf.set_text_color(*(INK if piece.own else MUTED))
                pdf.write(LINE_H, (piece.text if piece.own else f"[{piece.text}]") + " ")
            pdf.ln(LINE_H)
            pdf.set_left_margin(left)
            if pdf.page == page:
                # полоса слева — «это читаете вы»; на разрыве страницы её не рисуем, чем
                # тянуть через поле
                pdf.set_draw_color(*ACCENT)
                pdf.set_line_width(0.6)
                pdf.line(left + 1, top + 0.8, left + 1, pdf.get_y() - 0.8)
            pdf.ln(2.5)
    return bytes(pdf.output())

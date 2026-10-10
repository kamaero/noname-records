"""Сценарий роли наружу: всё, что актёру нужно, чтобы записать роль у себя.

В «одном месте» у актёра нет входа в программу — режиссёр отправляет ему файл. Файл
заменяет экран роли: реплики по главам, ударения в редких словах, кто это, и перед
каждой репликой — подводка, абзац партнёра, на который он отвечает (выбор владельца
10.10: подводка, а не два абзаца вокруг — коротко, и ясно, когда вступать).

Данные собираются здесь один раз, а текст и PDF только раскладывают их: два формата не
могут разойтись в том, какие реплики и ударения в них попали.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import quote

from app.models import Character, ScriptBook
from app.services.audio_naming import book_token, latin_word
from app.services.audio_uploads import build_canonical_audio_filename, derive_book_code
from app.services.author_profile import normalize_name
from app.services.shared_runtime import is_narrator_name
from app.v2.cast_ops import approved_actor, split_aliases
from app.v2.reader import UNSURE, _narrator_actor
from app.v2.role_view import MAX_LIMIT, _spoken_by, role_canon, role_script

#: ударения, поставленные людьми, видны всегда — правило «редкие» Читалки (StressToggle.tsx)
HUMAN_SOURCES = frozenset({"operator", "book", "author", "text"})
#: подводка — хвост абзаца: актёру нужен конец чужой фразы, а не весь монолог
CUE_CHARS = 160
ACUTE = "\u0301"


@dataclass
class Piece:
    text: str
    own: bool


@dataclass
class Line:
    pieces: list[Piece]
    cue_speaker: str = ""
    cue_text: str = ""


@dataclass
class Chapter:
    index: int
    title: str
    filename: str
    lines: list[Line] = field(default_factory=list)


@dataclass
class RoleExport:
    book_title: str
    role: str
    actor: str
    description: str
    facts: str
    note: str
    chapters: list[Chapter]

    @property
    def line_count(self) -> int:
        return sum(len(chapter.lines) for chapter in self.chapters)


def _visible(marks: list[dict]) -> list[dict]:
    """Частые слова не ударяются (правило владельца): только редкие и поставленные людьми."""
    return [mark for mark in marks if mark.get("rare") is not False or mark.get("source") in HUMAN_SOURCES]


def _with_marks(text: str, marks: list[dict], start: int, end: int) -> str:
    """`text[start:end]` с U+0301 после ударной гласной каждого слова внутри куска."""
    out = text[start:end]
    positions = sorted(
        (mark["start"] + mark["vowel"] + 1 for mark in marks if start <= mark["start"] + mark["vowel"] < end),
        reverse=True,
    )
    for position in positions:
        local = position - start
        if out[local : local + 1] != ACUTE:
            out = out[:local] + ACUTE + out[local:]
    return out


def _pieces(segment: dict, spellings: set[str]) -> list[Piece]:
    """Абзац кусками «своё / чужое». Слова автора внутри реплики — Рассказчику, и актёр
    не должен прочитать их как свои; знаки препинания между кусками своих не делят."""
    text = str(segment.get("text") or "")
    own = [False] * len(text)
    for span in segment.get("spans") or []:
        if span["speaker"] in spellings:
            for index in range(max(0, span["start"]), min(len(text), span["end"])):
                own[index] = True
    marks = _visible(segment.get("stress") or [])
    runs: list[Piece] = []
    start = 0
    while start < len(text):
        end = start
        while end < len(text) and own[end] == own[start]:
            end += 1
        chunk = _with_marks(text, marks, start, end)
        mine = own[start] or not any(ch.isalpha() for ch in chunk)
        if runs and runs[-1].own == mine:
            runs[-1].text += chunk
        else:
            runs.append(Piece(chunk, mine))
        start = end
    return [Piece(run.text.strip(), run.own) for run in runs if run.text.strip()]


def _cue(previous: dict) -> tuple[str, str]:
    text = str(previous.get("text") or "").strip()
    if not text:
        return "", ""
    spans = sorted(previous.get("spans") or [], key=lambda span: span["end"])
    speaker = spans[-1]["speaker"] if spans else ""
    if speaker == UNSURE:
        speaker = ""
    if len(text) > CUE_CHARS:
        tail = text[-CUE_CHARS:]
        space = tail.find(" ")
        text = "…" + (tail[space + 1 :] if 0 <= space < 40 else tail)
    return speaker, text


def _character(characters: list[Character], role: str) -> Character | None:
    wanted = normalize_name(role)
    for row in characters:
        names = {normalize_name(item) for item in [str(row.name or ""), *split_aliases(row.aliases)]}
        if wanted and wanted in names:
            return row
    return None


def build_role_export(db, book_id: str, role: str, *, published_only: bool = False) -> RoleExport | None:
    """Все реплики роли по главам, с подводкой. None — книги нет."""
    book = db.get(ScriptBook, str(book_id or "").strip())
    if book is None:
        return None
    role = str(role or "").strip()
    characters = db.query(Character).filter(Character.book_id == book.id).all()
    row = _character(characters, role)
    if is_narrator_name(role):
        actor = approved_actor(_narrator_actor(db, book.id, characters))
    else:
        actor = approved_actor(str(row.actor_name or "")) if row is not None else ""
    canon = role_canon(db, book.id, role) or {}
    facts = " · ".join(
        part for part in (str(getattr(row, "race", "") or "").strip(), str(getattr(row, "temperament", "") or "").strip())
        if part
    )
    export = RoleExport(
        book_title=str(book.display_title or book.title or "").strip(),
        role=role,
        actor=actor,
        description=str(canon.get("description") or "").strip(),
        facts=facts,
        note=str(getattr(row, "operator_note", "") or "").strip(),
        chapters=[],
    )
    spellings = set(_spoken_by(db, book.id, role))
    code = derive_book_code(book.title or "")
    by_id: dict[str, Chapter] = {}
    # «Своё» — по всем уже пройденным страницам: на стыке страниц предыдущий абзац мог
    # быть репликой роли с прошлой страницы, а `mine` знает только свою страницу.
    seen: set[tuple[str, int]] = set()
    offset = 0
    while True:
        page = role_script(db, book.id, role, radius=1, limit=MAX_LIMIT, offset=offset, published_only=published_only)
        for chapter in page["chapters"]:
            chapter_id = chapter["chapter_id"]
            target = by_id.get(chapter_id)
            if target is None:
                title = chapter["chapter_title"]
                # половина имени хуже, чем ничего: без актёра подсказки нет (как у формы записи)
                filename = build_canonical_audio_filename(code, title, role, actor, "take.wav") if actor else ""
                target = by_id[chapter_id] = Chapter(index=chapter["chapter_index"], title=title, filename=filename)
                export.chapters.append(target)
            by_ordinal = {segment["ordinal"]: segment for segment in chapter["segments"]}
            for segment in chapter["segments"]:
                key = (chapter_id, segment["ordinal"])
                if not segment["mine"] or key in seen:
                    continue
                seen.add(key)
                pieces = _pieces(segment, spellings)
                if not pieces:
                    continue
                previous = by_ordinal.get(segment["ordinal"] - 1)
                own_before = (chapter_id, segment["ordinal"] - 1) in seen
                speaker, cue = ("", "") if previous is None or own_before else _cue(previous)
                target.lines.append(Line(pieces=pieces, cue_speaker=speaker, cue_text=cue))
        if not page["has_more"]:
            break
        offset += page["counts"]["shown"]
    export.chapters = [chapter for chapter in export.chapters if chapter.lines]
    return export


_CHAPTER_WORD = re.compile(r"^глава\s*\d+", re.I)


def chapter_heading(chapter: Chapter) -> str:
    """Как на экране роли: «Глава N. Название», без повтора, если название уже с «Главой»."""
    title = chapter.title.strip()
    if not title:
        return f"Глава {chapter.index}"
    return title if _CHAPTER_WORD.match(title) else f"Глава {chapter.index}. {title}"


def line_text(line: Line) -> str:
    return " ".join(piece.text if piece.own else f"[{piece.text}]" for piece in line.pieces)


def render_text(export: RoleExport) -> str:
    out = [f"{export.role} — «{export.book_title}»"]
    if export.actor:
        out.append(f"Актёр: {export.actor}")
    if export.description:
        out.append(f"Кто это: {export.description}")
    if export.facts:
        out.append(export.facts)
    if export.note:
        out.append(f"Заметка режиссёра: {export.note}")
    out.append(f"Реплик: {export.line_count}, глав: {len(export.chapters)}. Ударения стоят в редких словах.")
    for chapter in export.chapters:
        out += ["", f"=== {chapter_heading(chapter)} ==="]
        if chapter.filename:
            out.append(f"Файл записи: {chapter.filename}")
        for line in chapter.lines:
            out.append("")
            if line.cue_text:
                label = f"{line.cue_speaker}: " if line.cue_speaker else ""
                out.append(f"  {label}{line.cue_text}")
            out.append(f"▶ {export.role}: {line_text(line)}")
    return "\n".join(out) + "\n"


def download_names(export: RoleExport, ext: str) -> tuple[str, str]:
    """Имя для человека и латинский запасной для программ, не понимающих `filename*`."""
    full = f"{export.role} — «{export.book_title}».{ext}"
    code = book_token(derive_book_code(export.book_title)) or "BOOK"
    role = (latin_word(export.role) or "Role").encode("ascii", "ignore").decode() or "Role"
    return full, f"{code}_{role}.{ext}"


def content_disposition(export: RoleExport, ext: str) -> str:
    full, ascii_name = download_names(export, ext)
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(full)}"

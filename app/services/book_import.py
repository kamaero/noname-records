"""Приём книги: файл → текст → главы → строки в базе.

Разбор docx/fb2/epub/txt (`extract_book_text`), деление на главы по оглавлению и
заголовкам (`split_into_chapters`), загрузка книги (`enqueue_book`) и снимок исходника
на диске. Раньше жило в `app/script_pipeline.py` вместе с остатками конвейера v1.
"""
from __future__ import annotations

import io
import re
import difflib
import logging
from pathlib import Path
from dataclasses import dataclass
from typing import Any
from docx import Document
from sqlalchemy.orm import Session
from app.config import settings
from app.models import (
    ScriptBook,
    ScriptChapter,
    ScriptJob,
    ScriptLog,
)
from app.services import book_title as book_title_service
from app.pipeline.text_splitter import _explode_large_chapters
from app.pipeline.book_parser import (
    _decode_text,
    parse_filename_title,
    _extract_epub_text,
    _extract_fb2_author,
    _extract_fb2_text,
)

logger = logging.getLogger(__name__)


DEFAULT_MAX_CHAPTER_CHARS = 180000


BOOK_SOURCE_DIR = Path("data/book_sources")


# Pre-compiled heading patterns for _score_heading_line (called per line of book text)
_RE_HEADING_CHAPTER_WORD  = re.compile(r"^(?:глава|chapter)\s+(?=[\dA-Za-zА-Яа-яЁё])[^\n]{0,140}$", re.IGNORECASE)


_RE_HEADING_ARABIC_TEXT   = re.compile(r"^\d{1,3}\s*[.)-]\s+\S.+$")


_RE_HEADING_ROMAN_TEXT    = re.compile(r"^[IVXLCDM]{1,8}\s*[.)-]\s+\S.+$")


_RE_HEADING_ARABIC_ONLY   = re.compile(r"^\d{1,3}\s*[.)]?$")


_RE_HEADING_ROMAN_ONLY    = re.compile(r"^[IVXLCDM]{1,8}\s*[.)]?$")


_RE_HEADING_DIGITS_ONLY   = re.compile(r"[^0-9]")


@dataclass
class ParsedBook:
    title: str
    text: str
    source_format: str
    author: str = ""  # from fb2 metadata when available; used for author auto-detect
    #: автор, узнанный в имени файла («Белозёровы») — им подписывают книгу на витрине,
    #: тогда как `author` из метаданных fb2 («Александр Белозёров») ищет канон автора
    filename_author: str = ""


@dataclass
class ChapterLine:
    index: int
    start: int
    end: int
    text: str


@dataclass
class ChapterCandidate:
    start: int
    title: str
    score: float
    kind: str


def extract_book_text(filename: str, payload: bytes) -> ParsedBook:
    lower = filename.lower()
    # Имя файла даёт название и, если узнан, автора — порознь: автор в названии
    # портит код книги, по которому находят записи актёров.
    fname_author, fname_title = parse_filename_title(filename)
    if lower.endswith(".txt"):
        return ParsedBook(title=fname_title, text=_decode_text(payload), source_format="txt",
                          filename_author=fname_author)
    if lower.endswith(".docx"):
        doc = Document(io.BytesIO(payload))
        lines = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
        return ParsedBook(title=fname_title, text="\n\n".join(lines), source_format="docx",
                          filename_author=fname_author)
    if lower.endswith(".fb2"):
        return ParsedBook(title=fname_title, text=_extract_fb2_text(payload),
                          source_format="fb2", author=_extract_fb2_author(payload),
                          filename_author=fname_author)
    if lower.endswith(".epub"):
        return ParsedBook(title=fname_title, text=_extract_epub_text(payload), source_format="epub",
                          filename_author=fname_author)
    raise ValueError("Поддерживаются только txt, docx, fb2, epub")


def split_into_chapters(text: str) -> list[tuple[str, str]]:
    full = text or ""
    max_chapter_chars = max(45000, int(getattr(settings, "max_chapter_chars", DEFAULT_MAX_CHAPTER_CHARS) or DEFAULT_MAX_CHAPTER_CHARS))
    if not full.strip():
        return [("Глава 1", "")]
    lines = _build_lines(full)
    toc = _extract_toc(lines)
    candidates = _collect_chapter_candidates(lines, toc_end=toc.get("end", -1))
    selected = _select_candidates(candidates, toc_entries=toc.get("entries", []), text_len=len(full))

    if len(selected) < 2:
        # fallback to legacy "Глава/Chapter" detector
        chapter_header = re.compile(r"^\s*(?:ГЛАВА|Глава|CHAPTER|Chapter)\s+([0-9IVXLCА-Яа-яA-Za-z._-]+.*)?$", re.MULTILINE)
        selected = [ChapterCandidate(start=m.start(), title=(m.group(0) or "").strip(), score=1.0, kind="legacy") for m in chapter_header.finditer(full)]

    if not selected:
        return _explode_large_chapters([("Глава 1", full.strip())], max_chars=max_chapter_chars)

    selected = sorted(selected, key=lambda x: x.start)
    chunks: list[tuple[str, str]] = []

    # preserve meaningful prefatory text before first chapter
    first_start = selected[0].start
    preface = full[:first_start].strip()
    if preface and len(preface) > 220:
        chunks.append(("Вступление", preface))

    starts = [c.start for c in selected]
    for idx, start in enumerate(starts):
        end = starts[idx + 1] if idx + 1 < len(starts) else len(full)
        body = full[start:end].strip()
        title = (selected[idx].title or f"Глава {idx + 1}").strip()
        if body:
            chunks.append((title, body))
    return _explode_large_chapters(chunks, max_chars=max_chapter_chars)


def _build_lines(text: str) -> list[ChapterLine]:
    out: list[ChapterLine] = []
    for idx, m in enumerate(re.finditer(r".*(?:\n|$)", text), start=0):
        raw = m.group(0)
        if raw == "" and m.start() >= len(text):
            break
        out.append(ChapterLine(index=idx, start=m.start(), end=m.end(), text=raw.rstrip("\n")))
    return out


def _clean_title_for_match(value: str) -> str:
    t = (value or "").strip().lower()
    t = re.sub(r"^(?:глава|chapter)\s+", "", t, flags=re.IGNORECASE)
    t = re.sub(r"^[ivxlcdm]+\s*[.)-]\s*", "", t, flags=re.IGNORECASE)
    t = re.sub(r"^\d+\s*[.)-]\s*", "", t)
    t = re.sub(r"^\d+\s*$", "", t)
    t = re.sub(r"[«»\"'`]", " ", t)
    t = re.sub(r"[^\wа-яё ]+", " ", t, flags=re.IGNORECASE)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _extract_toc(lines: list[ChapterLine]) -> dict[str, Any]:
    heads = {"оглавление", "содержание", "contents", "table of contents"}
    start_idx = -1
    for line in lines[:800]:
        if line.start > 70000:
            break
        if line.text.strip().lower() in heads:
            start_idx = line.index
            break
    if start_idx < 0:
        return {"entries": [], "end": -1}

    entries: list[dict[str, Any]] = []
    end_pos = -1
    empty_run = 0
    for line in lines[start_idx + 1:start_idx + 260]:
        raw = line.text.strip()
        if not raw:
            empty_run += 1
            if empty_run >= 4 and entries:
                end_pos = line.end
                break
            continue
        empty_run = 0

        m = re.match(r"^(?P<title>.+?)\s*(?:\.{2,}|\s{2,}|\s)\s*(?P<page>\d{1,4})\s*$", raw)
        if not m:
            m = re.match(r"^(?P<title>(?:глава|chapter)?\s*[0-9ivxlcdmа-яё.\- ]{1,120})\s+(?P<page>\d{1,4})\s*$", raw, flags=re.IGNORECASE)
        if not m:
            continue
        title = m.group("title").strip(" .\t")
        page = int(m.group("page"))
        if not title:
            continue
        entries.append({"title": title, "page": page, "norm": _clean_title_for_match(title)})
        end_pos = line.end
    if len(entries) < 2:
        return {"entries": [], "end": end_pos}
    return {"entries": entries, "end": end_pos}


def _score_heading_line(value: str, prev_blank: bool, next_blank: bool) -> tuple[float, str, str] | None:
    raw = value.strip()
    if not raw or len(raw) > 150:
        return None

    score = 0.0
    kind = ""
    title = raw

    if _RE_HEADING_CHAPTER_WORD.match(raw):
        score = 1.0
        kind = "chapter_word"
    elif _RE_HEADING_ARABIC_TEXT.match(raw):
        score = 0.95
        kind = "arabic_with_text"
    elif _RE_HEADING_ROMAN_TEXT.match(raw):
        score = 0.95
        kind = "roman_with_text"
    elif _RE_HEADING_ARABIC_ONLY.match(raw):
        score = 0.82
        kind = "arabic_only"
        title = f"Глава {_RE_HEADING_DIGITS_ONLY.sub('', raw)}"
    elif _RE_HEADING_ROMAN_ONLY.match(raw):
        score = 0.82
        kind = "roman_only"
        title = f"Глава {raw}"
    else:
        return None

    if prev_blank:
        score += 0.08
    if next_blank:
        score += 0.08
    if raw.endswith((".", "!", "?")) and kind in {"chapter_word", "arabic_with_text", "roman_with_text"}:
        score -= 0.08
    return max(0.0, min(1.2, score)), kind, title


def _collect_chapter_candidates(lines: list[ChapterLine], toc_end: int) -> list[ChapterCandidate]:
    out: list[ChapterCandidate] = []
    for i, line in enumerate(lines):
        if line.start <= toc_end:
            continue
        prev_blank = i > 0 and not lines[i - 1].text.strip()
        next_blank = i + 1 < len(lines) and not lines[i + 1].text.strip()
        scored = _score_heading_line(line.text, prev_blank, next_blank)
        if not scored:
            continue
        score, kind, title = scored
        out.append(ChapterCandidate(start=line.start, title=title, score=score, kind=kind))
    # dedupe near-identical starts
    out.sort(key=lambda x: x.start)
    dedup: list[ChapterCandidate] = []
    for cand in out:
        if dedup and abs(cand.start - dedup[-1].start) < 40:
            if cand.score > dedup[-1].score:
                dedup[-1] = cand
            continue
        dedup.append(cand)
    return dedup


def _select_candidates(candidates: list[ChapterCandidate], toc_entries: list[dict[str, Any]], text_len: int) -> list[ChapterCandidate]:
    if not candidates:
        return []

    base = [c for c in candidates if c.score >= 0.86]
    if not base:
        base = [c for c in candidates if c.score >= 0.78]
    if not toc_entries:
        return _prune_by_distance(base, min_gap=260)

    pages = [int(e.get("page", 0)) for e in toc_entries if int(e.get("page", 0)) > 0]
    p_min = min(pages) if pages else 1
    p_max = max(pages) if pages else max(2, len(toc_entries))

    chosen: list[ChapterCandidate] = []
    last_start = -1
    for idx, entry in enumerate(toc_entries):
        target = 0
        if p_max > p_min:
            ratio = (int(entry.get("page", p_min)) - p_min) / (p_max - p_min)
            target = int(ratio * max(1, text_len - 1))
        norm_title = str(entry.get("norm") or "")

        best = None
        best_score = 0.0
        for cand in candidates:
            if cand.start <= last_start:
                continue
            cand_norm = _clean_title_for_match(cand.title)
            sim = difflib.SequenceMatcher(a=norm_title, b=cand_norm).ratio() if norm_title and cand_norm else 0.0
            dist = 1.0 - min(1.0, abs(cand.start - target) / max(1, text_len))
            total = (0.62 * sim) + (0.26 * dist) + (0.12 * min(1.0, cand.score))
            if total > best_score:
                best_score = total
                best = cand
        if best and (best_score >= 0.54 or (idx < 3 and best_score >= 0.47)):
            chosen.append(best)
            last_start = best.start

    chosen = _prune_by_distance(chosen, min_gap=260)
    if len(chosen) >= max(2, len(toc_entries) // 2):
        return chosen
    return _prune_by_distance(base, min_gap=260)


def _prune_by_distance(candidates: list[ChapterCandidate], min_gap: int) -> list[ChapterCandidate]:
    ordered = sorted(candidates, key=lambda x: x.start)
    out: list[ChapterCandidate] = []
    for cand in ordered:
        if out and cand.start - out[-1].start < min_gap:
            if cand.score > out[-1].score:
                out[-1] = cand
            continue
        out.append(cand)
    return out


def calc_book_stats(text: str, chapter_count: int) -> tuple[int, int, bool]:
    chars = len(text)
    return chars, int((chars / 40000) * 1000), chapter_count > 1


def enqueue_book(
    db: Session,
    filename: str,
    payload: bytes,
    *,
    pipeline_mode: str = "v2",
    validation_profile: str = "operator_only",
    genre_guidelines: str = "",
    domain_lexicon: str = "",
    created_by_user_id: str = "",
    created_by_name: str = "",
    title: str = "",
    author: str = "",
) -> ScriptBook:
    """Parse the upload, persist the book and its chapters, and park it as `uploaded`.

    No job rows are created: pipeline v2 runs only when `POST /api/v2/books/{id}/run`
    asks for it, and its cast step makes its own `char_extraction` ScriptJob.

    `title` и `author` — то, что человек вписал в форме загрузки; пусто — берём из
    имени файла. Книга показывается как «Автор - "Название"», но в `title` лежит
    только название: из него выводится код книги для имён файлов актёров.
    """
    parsed = extract_book_text(filename, payload)
    chapters = split_into_chapters(parsed.text)
    total_chars, author_sheets_x1000, has_chapters = calc_book_stats(parsed.text, len(chapters))

    book_title = (title or "").strip() or parsed.title
    book_author = (author or "").strip() or parsed.filename_author
    book = ScriptBook(
        title=book_title,
        author_label=book_author[:120],
        display_title=book_title_service.compose_display_title(book_author, book_title),
        source_filename=filename,
        source_format=parsed.source_format,
        total_chars=total_chars,
        author_sheets_x1000=author_sheets_x1000,
        chapter_count=len(chapters),
        has_chapters="true" if has_chapters else "false",
        pipeline_mode=(pipeline_mode or "v2").strip() or "v2",
        validation_profile=(validation_profile or "operator_only").strip() or "operator_only",
        genre_guidelines=(genre_guidelines or "").strip(),
        domain_lexicon=(domain_lexicon or "").strip(),
        created_by_user_id=(created_by_user_id or "").strip(),
        created_by_name=(created_by_name or "").strip(),
        status="uploaded",
    )
    # Auto-bind to a known author by title/filename. Once bound, the author roster
    # seeds char_extraction and char_memory_sync preloads reply colour + voice actor.
    from app.services.author_detect import detect_author_id

    book.author_id = detect_author_id(db, parsed.author, book_author, book_title, filename)
    db.add(book)
    db.flush()
    if book.author_id:
        db.add(
            ScriptLog(
                book_id=book.id,
                level="info",
                message="Автор определён автоматически — подгружены канон и каст автора (имена, алиасы, актёры, цвета).",
            )
        )
    _persist_book_source_snapshot(book.id, filename, payload, parsed.text)
    _create_book_chapters(db, book=book, chapters=chapters)

    db.add(
        ScriptLog(
            book_id=book.id,
            level="info",
            message=f"Книга загружена: {filename}. Глав: {len(chapters)}. Символов: {total_chars}. Ждёт запуска пайплайна v2.",
        )
    )
    db.commit()
    db.refresh(book)
    return book


def _create_book_chapters(db: Session, *, book: ScriptBook, chapters: list[tuple[str, str]]) -> None:
    for idx, (chapter_title, chapter_text) in enumerate(chapters, start=1):
        db.add(
            ScriptChapter(
                book_id=book.id,
                chapter_index=idx,
                chapter_title=chapter_title,
                char_count=len(chapter_text),
                source_text=chapter_text,
                status="queued",
            )
        )
    db.flush()


def _book_source_root(book_id: str) -> Path:
    path = BOOK_SOURCE_DIR / str(book_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _book_source_original_path(book_id: str, filename: str) -> Path:
    suffix = Path(filename or "book.txt").suffix or ".txt"
    return _book_source_root(book_id) / f"original{suffix}"


def _book_source_text_path(book_id: str) -> Path:
    return _book_source_root(book_id) / "extracted.txt"


def _persist_book_source_snapshot(book_id: str, filename: str, payload: bytes, extracted_text: str) -> None:
    _book_source_original_path(book_id, filename).write_bytes(payload or b"")
    _book_source_text_path(book_id).write_text(extracted_text or "", encoding="utf-8")

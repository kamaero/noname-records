"""
Text splitting and chunking utilities.
Pure text functions — no DB or model imports.
"""
from __future__ import annotations

import re


def _split_text_parts(text: str, max_chars: int) -> list[str]:
    clean = text.strip()
    if not clean:
        return [""]
    if len(clean) <= max_chars:
        return [clean]

    paragraphs = _split_paragraph_blocks(clean, max_chars=max_chars)
    if not paragraphs:
        paragraphs = [clean]

    out: list[str] = []
    current: list[str] = []
    current_len = 0
    for p in paragraphs:
        add_len = len(p) + 2
        if current and current_len + add_len > max_chars:
            out.append("\n\n".join(current))
            current = [p]
            current_len = len(p)
        else:
            current.append(p)
            current_len += add_len
    if current:
        out.append("\n\n".join(current))
    return out


def _build_chunk_overlap_context(parts: list[str], index: int, overlap_chars: int) -> str:
    if overlap_chars <= 0 or not parts or index < 0 or index >= len(parts):
        return ""
    prev_tail = ""
    next_head = ""
    if index > 0:
        prev_tail = str(parts[index - 1] or "").strip()[-overlap_chars:].strip()
    if index + 1 < len(parts):
        next_head = str(parts[index + 1] or "").strip()[:overlap_chars].strip()
    if not prev_tail and not next_head:
        return ""

    lines = [
        "Контекст соседних частей (только для ориентации, не дублируй этот контекст в ответе):",
    ]
    if prev_tail:
        lines.append(f"[PREV_TAIL]\n{prev_tail}")
    if next_head:
        lines.append(f"[NEXT_HEAD]\n{next_head}")
    return "\n\n".join(lines).strip()


def _split_paragraph_blocks(text: str, max_chars: int) -> list[str]:
    raw_blocks = [block.strip() for block in re.split(r"\n{2,}", str(text or "").replace("\r\n", "\n")) if block.strip()]
    if not raw_blocks:
        return []
    out: list[str] = []
    for block in raw_blocks:
        out.extend(_split_long_paragraph_block(block, max_chars=max_chars))
    return out


def _split_long_paragraph_block(block: str, max_chars: int) -> list[str]:
    clean = str(block or "").strip()
    if not clean:
        return []
    if len(clean) <= max_chars:
        return [clean]

    by_lines = _pack_units([line.strip() for line in clean.splitlines() if line.strip()], max_chars=max_chars, sep="\n")
    if by_lines and max(len(part) for part in by_lines) <= max_chars:
        return by_lines

    sentences = [s.strip() for s in re.split(r"(?<=[.!?…])\s+", clean) if s.strip()]
    by_sentences = _pack_units(sentences, max_chars=max_chars, sep=" ")
    if by_sentences and max(len(part) for part in by_sentences) <= max_chars:
        return by_sentences

    return _hard_wrap_text(clean, max_chars=max_chars)


def _pack_units(units: list[str], max_chars: int, sep: str) -> list[str]:
    if not units:
        return []
    out: list[str] = []
    current = units[0]
    for unit in units[1:]:
        candidate = f"{current}{sep}{unit}"
        if len(candidate) <= max_chars:
            current = candidate
            continue
        out.append(current)
        current = unit
    out.append(current)
    return out


def _hard_wrap_text(text: str, max_chars: int) -> list[str]:
    clean = str(text or "").strip()
    if not clean:
        return []
    if len(clean) <= max_chars:
        return [clean]

    out: list[str] = []
    start = 0
    total = len(clean)
    while start < total:
        end = min(start + max_chars, total)
        if end < total:
            soft_break = clean.rfind(" ", start + int(max_chars * 0.6), end)
            if soft_break > start:
                end = soft_break
        part = clean[start:end].strip()
        if part:
            out.append(part)
        start = end if end > start else start + max_chars
        while start < total and clean[start].isspace():
            start += 1
    return out or [clean]


def _coalesce_tiny_first_part(parts: list[str], max_chars: int) -> list[str]:
    if len(parts) < 2:
        return parts
    first = (parts[0] or "").strip()
    second = (parts[1] or "").strip()
    if not first or not second:
        return parts
    tiny_threshold = min(1200, max(220, int(max_chars * 0.08)))
    if len(first) > tiny_threshold:
        return parts
    merged = [f"{first}\n\n{second}".strip()]
    merged.extend(parts[2:])
    return merged


def _explode_large_chapters(chapters: list[tuple[str, str]], max_chars: int) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for title, text in chapters:
        parts = _coalesce_tiny_first_part(_split_text_parts(text, max_chars=max_chars), max_chars=max_chars)
        if len(parts) == 1:
            out.append((title, parts[0]))
            continue
        for idx, part in enumerate(parts, start=1):
            out.append((f"{title} / часть {idx}", part))
    return out

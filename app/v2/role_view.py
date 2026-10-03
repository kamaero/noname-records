"""One actor's own lines across the whole book, each with the text around it.

A dictor does not work by chapters — he works by his role. Бимькмолепус speaks in six
chapters of «Крыльев», and finding those places meant opening the reader sixty times
and filtering each chapter separately. This assembles them once: every line of the
role, in reading order, with a few paragraphs of context on each side so the actor
knows what he is answering.

Context is what makes this readable rather than a list of quotes, and it is also what
makes it big: a role with a thousand lines and two paragraphs on each side is three
thousand paragraphs. So the answer is paged by lines of the role — `limit` counts the
actor's own lines, never the context — and neighbouring windows that overlap are
merged instead of repeating the same paragraph twice.

Everything is read narrowly: the speakers of the book (a few hundred rows) to find
which spellings are this role, then the spans of that role, then only the paragraphs
of the page being shown. Loading the whole book to answer «где я говорю» took two
seconds per open, and this screen is opened all day.
"""
from __future__ import annotations

import collections

from sqlalchemy import and_, func, or_, select

from app.constants import ChapterStatus
from app.models import ScriptBook, ScriptChapter
from app.services.author_profile import normalize_name
from app.services.shared_runtime import is_narrator_name
from app.v2.cast_ops import split_aliases
from app.v2.models import V2Attribution, V2Segment
from app.v2.reader import (
    UNSURE,
    _latest_versions_for_book,
    effective_attributions,
    effective_stress,
    segments_payload,
)

DEFAULT_RADIUS = 2
MAX_RADIUS = 6
DEFAULT_LIMIT = 300
MAX_LIMIT = 1000


def role_aliases(db, book_id: str, role: str) -> set[str]:
    """Every spelling of the role that an attribution may carry, folded for comparison.

    The markup speaks the name the model wrote; the cast may know it with a stress
    mark or under an alias. Matching on the folded set means «Дгарни́н» and «Дгар» find
    the same actor's lines.
    """
    from app.models import Character

    wanted = normalize_name(role)
    if not wanted:
        return set()
    names = {wanted}
    for row in db.query(Character).filter(Character.book_id == book_id).all():
        candidates = [str(row.name or ""), *split_aliases(row.aliases)]
        folded = {normalize_name(item) for item in candidates if str(item or "").strip()}
        if wanted in folded:
            names |= folded
    return {name for name in names if name}


def role_canon(db, book_id: str, role: str) -> dict | None:
    """Что энциклопедия автора говорит об этой роли — «кто это» для актёра.

    Связь уже есть: `characters.author_character_id` проставляет разметка, когда книга
    привязана к автору. Берём её, а не ищем по имени заново: там, где имя в книге и в
    каноне расходятся («Астрид» против «Астрид Дегатти»), поиск по имени промахнулся
    бы, а связь стоит.
    """
    from app.models import AuthorCharacter, Character

    wanted = normalize_name(role)
    if not wanted:
        return None
    for row in db.query(Character).filter(Character.book_id == book_id).all():
        names = {normalize_name(item) for item in [str(row.name or ""), *split_aliases(row.aliases)]}
        if wanted not in names:
            continue
        canon_id = str(getattr(row, "author_character_id", "") or "")
        if not canon_id:
            return None
        canon = db.get(AuthorCharacter, canon_id)
        if canon is None:
            return None
        from app.v2.illustrations_api import portraits_for

        description = str(canon.description or "").strip()
        portrait = portraits_for(db, [canon_id]).get(canon_id, "")
        if not description and not portrait:
            # Карточка без описания пришла из каста, а не из энциклопедии: сказать о
            # роли ей нечего, и строки «кто это» быть не должно. Портрет же, данный
            # человеком, показывается и без статьи — актёру лицо важнее справки.
            return None
        return {
            "name": str(canon.canonical_name or "").strip(),
            "aliases": [a for a in split_aliases(str(canon.aliases or "")) if a.strip()],
            "description": description,
            "topic": str(canon.source_topic or "").strip(),
            "portrait": portrait,
        }
    return None


def _spoken_by(db, book_id: str, role: str) -> list[str]:
    """The raw speaker strings in this book's markup that mean `role`."""
    wanted = role_aliases(db, book_id, role)
    narrator = is_narrator_name(role)
    segment_ids = select(V2Segment.id).where(V2Segment.book_id == book_id)
    speakers = db.execute(
        select(V2Attribution.speaker).where(V2Attribution.segment_id.in_(segment_ids)).distinct()
    ).scalars().all()
    out = []
    for speaker in speakers:
        name = str(speaker or "").strip()
        if not name or name == UNSURE:
            continue
        if (narrator and is_narrator_name(name)) or normalize_name(name) in wanted:
            out.append(name)
    return out


def _windows(indexes: list[int], radius: int, total: int) -> list[tuple[int, int]]:
    """Merge `index ± radius` into non-overlapping (start, end) ranges."""
    merged: list[list[int]] = []
    for index in indexes:
        start, end = max(0, index - radius), min(total - 1, index + radius)
        if merged and start <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(start, end) for start, end in merged]


def role_script(
    db,
    book_id: str,
    role: str,
    *,
    radius: int = DEFAULT_RADIUS,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
    published_only: bool = False,
) -> dict | None:
    """The role's lines with context, chapter by chapter. None when the book is unknown."""
    book = db.get(ScriptBook, str(book_id or "").strip())
    if book is None:
        return None
    radius = max(0, min(MAX_RADIUS, int(radius)))
    limit = max(1, min(MAX_LIMIT, int(limit)))
    offset = max(0, int(offset))

    chapters_query = db.query(ScriptChapter).filter(ScriptChapter.book_id == book.id)
    if published_only:
        # Диктор работает только по опубликованным главам: неопубликованная ещё
        # правится автором, и реплика из неё может исчезнуть или сменить роль.
        chapters_query = chapters_query.filter(ScriptChapter.status == ChapterStatus.PUBLISHED.value)
    chapters = chapters_query.order_by(ScriptChapter.chapter_index.asc()).all()
    order = {chapter.id: index for index, chapter in enumerate(chapters)}
    by_id = {chapter.id: chapter for chapter in chapters}

    # «Кто это» по канону автора — одинаково и для роли с репликами, и для пустой:
    # актёр открывает страницу роли раньше, чем в книге появляются её реплики.
    canon = role_canon(db, book.id, role)
    empty = {
        "book": {"id": book.id, "title": str(book.display_title or book.title or "").strip()},
        "role": str(role or "").strip(),
        "canon": canon,
        "radius": radius,
        "counts": {"lines": 0, "chapters": 0, "shown": 0, "offset": offset},
        "has_more": False,
        "chapters": [],
        "in_chapters": [],
    }
    spellings = _spoken_by(db, book.id, role)
    if not spellings:
        return empty

    # Where the role speaks: one row per span, ordered the way the book reads.
    latest = _latest_versions_for_book(V2Attribution, book.id)
    hits = db.execute(
        select(V2Segment.chapter_id, V2Segment.ordinal)
        .join(V2Attribution, V2Attribution.segment_id == V2Segment.id)
        .join(latest, and_(latest.c.segment_id == V2Attribution.segment_id, latest.c.v == V2Attribution.version))
        .where(V2Segment.book_id == book.id, V2Attribution.speaker.in_(spellings))
        .distinct()
    ).all()
    lines = sorted(
        ((str(chapter_id), int(ordinal or 0)) for chapter_id, ordinal in hits if chapter_id in order),
        key=lambda item: (order[item[0]], item[1]),
    )
    if not lines:
        return empty

    # «В каких главах я есть» — по всем репликам роли, а не по показанной странице:
    # у большой роли первая страница кончается задолго до конца книги.
    per_chapter = collections.Counter(chapter_id for chapter_id, _ in lines)
    in_chapters = [
        {
            "chapter_id": chapter_id,
            "chapter_index": int(getattr(by_id[chapter_id], "chapter_index", 0) or 0),
            "lines": count,
        }
        for chapter_id, count in per_chapter.items()
    ]

    page = lines[offset : offset + limit]
    by_chapter: dict[str, list[int]] = collections.OrderedDict()
    for chapter_id, ordinal in page:
        by_chapter.setdefault(chapter_id, []).append(ordinal)

    # Only the paragraphs of this page: the windows around its lines, chapter by chapter.
    sizes = dict(
        db.execute(
            select(V2Segment.chapter_id, func.max(V2Segment.ordinal))
            .where(V2Segment.chapter_id.in_(list(by_chapter)))
            .group_by(V2Segment.chapter_id)
        ).all()
    )
    windows: dict[str, list[tuple[int, int]]] = {
        chapter_id: _windows(ordinals, radius, int(sizes.get(chapter_id, 0)) + 1)
        for chapter_id, ordinals in by_chapter.items()
    }
    conditions = [
        and_(V2Segment.chapter_id == chapter_id, V2Segment.ordinal.between(start, end))
        for chapter_id, ranges in windows.items()
        for start, end in ranges
    ]
    picked = (
        db.query(V2Segment)
        .filter(or_(*conditions))
        .order_by(V2Segment.chapter_id.asc(), V2Segment.ordinal.asc())
        .all()
    )
    segment_ids = [segment.id for segment in picked]
    attributions = effective_attributions(db, segment_ids)
    stress = effective_stress(db, segment_ids)
    payload = {item["id"]: item for item in segments_payload(picked, attributions, stress)}

    mine = {(chapter_id, ordinal) for chapter_id, ordinal in page}
    out_chapters = []
    for chapter_id, ranges in windows.items():
        chapter = by_id.get(chapter_id)
        segments = []
        by_ordinal = {
            int(segment.ordinal or 0): segment for segment in picked if segment.chapter_id == chapter_id
        }
        for index, (start, end) in enumerate(ranges):
            for ordinal in range(start, end + 1):
                segment = by_ordinal.get(ordinal)
                if segment is None:
                    continue
                item = dict(payload[segment.id])
                item["mine"] = (chapter_id, ordinal) in mine
                # true when the paragraph opens a new island inside the same chapter
                item["after_gap"] = index > 0 and ordinal == start
                segments.append(item)
        out_chapters.append({
            "chapter_id": chapter_id,
            "chapter_index": int(getattr(chapter, "chapter_index", 0) or 0),
            "chapter_title": str(getattr(chapter, "chapter_title", "") or "").strip(),
            "lines": len(by_chapter[chapter_id]),
            "segments": segments,
        })

    return {
        "book": {"id": book.id, "title": str(book.display_title or book.title or "").strip()},
        "role": str(role or "").strip(),
        "canon": canon,
        "radius": radius,
        "counts": {
            "lines": len(lines),
            "chapters": len(per_chapter),
            "shown": len(page),
            "offset": offset,
        },
        "has_more": offset + len(page) < len(lines),
        "chapters": out_chapters,
        "in_chapters": in_chapters,
    }


def role_traps(
    db,
    book_id: str,
    role: str,
    *,
    fresh: bool = True,
    published_only: bool = False,
) -> dict | None:
    """«Слова-ловушки»: the rare words inside one role's own lines, chapter by chapter, with their stress.

    The narrator of «Полумракские байки» said карли́ца and сомнамбу́ла while reading in
    flow — the marks were there, he did not look. This is the list to look at before
    the session: words of this role only (a span of the role covers the word), rare
    only (`word_rarity`; «даже» he says right anyway), and no «ё» words — there is no
    stress to get wrong. A word nobody marked yet is listed too, with `stressed` None:
    that is a word to check, not one to skip.

    `fresh` keeps a word only in the chapter where the role meets it first; forms are
    never merged — «ка́рлики» does not vouch for «ка́рлица», which is the one he missed.
    """
    from app.v2.stress import ACUTE, find_words
    from app.v2.word_rarity import is_rare

    book = db.get(ScriptBook, str(book_id or "").strip())
    if book is None:
        return None
    chapters_query = db.query(ScriptChapter).filter(ScriptChapter.book_id == book.id)
    if published_only:
        chapters_query = chapters_query.filter(ScriptChapter.status == ChapterStatus.PUBLISHED.value)
    chapters = chapters_query.order_by(ScriptChapter.chapter_index.asc()).all()
    order = {chapter.id: index for index, chapter in enumerate(chapters)}
    by_id = {chapter.id: chapter for chapter in chapters}
    result = {
        "book": {"id": book.id, "title": str(book.display_title or book.title or "").strip()},
        "role": str(role or "").strip(),
        "fresh": bool(fresh),
        "chapters": [],
    }
    spellings = _spoken_by(db, book.id, role)
    if not spellings:
        return result

    latest = _latest_versions_for_book(V2Attribution, book.id)
    spans: dict[str, list[tuple[int, int]]] = collections.defaultdict(list)
    for segment_id, start, end in db.execute(
        select(V2Attribution.segment_id, V2Attribution.span_start, V2Attribution.span_end)
        .join(latest, and_(latest.c.segment_id == V2Attribution.segment_id, latest.c.v == V2Attribution.version))
        .where(V2Attribution.speaker.in_(spellings))
    ):
        spans[str(segment_id)].append((int(start or 0), int(end or 0)))
    if not spans:
        return result

    segments = [
        (str(segment_id), str(chapter_id), int(ordinal or 0), str(text or ""))
        for segment_id, chapter_id, ordinal, text in db.execute(
            select(V2Segment.id, V2Segment.chapter_id, V2Segment.ordinal, V2Segment.text)
            .where(V2Segment.book_id == book.id)
        )
        if str(segment_id) in spans and chapter_id in order
    ]
    segments.sort(key=lambda item: (order[item[1]], item[2]))
    marks = _traps_marks(db, book.id, [item[0] for item in segments])

    seen: set[str] = set()
    per_chapter: dict[str, dict[str, dict]] = collections.OrderedDict()
    for segment_id, chapter_id, _ordinal, text in segments:
        own = spans[segment_id]
        at = {start: (end, vowel, source) for start, end, vowel, source in marks.get(segment_id, [])}
        words = per_chapter.setdefault(chapter_id, collections.OrderedDict())
        for start, end in find_words(text):
            if not any(a <= start and end <= b for a, b in own):
                continue
            word = text[start:end].lower()
            if "ё" in word or ACUTE in word or not is_rare(word) or _stretched(text, start, end):
                continue
            if fresh and word in seen and word not in words:
                continue
            entry = words.get(word)
            if entry is None:
                mark = at.get(start)
                stressed = None
                if mark is not None and mark[0] == end and 0 <= mark[1] < len(word):
                    stressed = word[: mark[1] + 1] + ACUTE + word[mark[1] + 1 :]
                entry = words[word] = {
                    "word": word,
                    "stressed": stressed,
                    "source": mark[2] if mark is not None else None,
                    "count": 0,
                    "segment_id": segment_id,
                }
            entry["count"] += 1
        seen.update(words)

    for chapter_id, words in per_chapter.items():
        if not words:
            continue
        chapter = by_id[chapter_id]
        result["chapters"].append({
            "chapter_id": chapter_id,
            "chapter_index": int(chapter.chapter_index or 0),
            "chapter_title": str(chapter.chapter_title or "").strip(),
            "words": list(words.values()),
        })
    return result


def _stretched(text: str, start: int, end: int) -> bool:
    """A part of «Коне-е-ечно» or «Н-нет»: a hyphenated run with a one-letter part is a voice, not a word."""
    from app.v2.stress import _compound_span

    span = _compound_span(text, start, end)
    return span is not None and any(len(part) == 1 for part in text[span[0] : span[1]].split("-"))


# Above this many segments the role is most of the book (the narrator), and one join
# on the latest version beats fetching every version of every mark by id: 190k rows
# instead of 450k for «Крылья».
_WHOLE_BOOK_SEGMENTS = 2000


def _traps_marks(db, book_id: str, segment_ids: list[str]) -> dict[str, list[tuple[int, int, int, str]]]:
    """Effective `(word_start, word_end, vowel_offset, source)` per segment, the cheaper way for the size."""
    from app.v2.models import V2StressMark
    from app.v2.reader import effective_book_rows
    from app.v2.stress_ops import _effective_marks_for

    if len(segment_ids) < _WHOLE_BOOK_SEGMENTS:
        return _effective_marks_for(db, segment_ids)
    wanted = set(segment_ids)
    out: dict[str, list[tuple[int, int, int, str]]] = {}
    for segment_id, start, end, vowel, source in effective_book_rows(
        db, V2StressMark, book_id,
        V2StressMark.word_start, V2StressMark.word_end, V2StressMark.vowel_offset, V2StressMark.source,
    ):
        if segment_id in wanted:
            out.setdefault(str(segment_id), []).append((int(start), int(end), int(vowel), str(source or "")))
    return out

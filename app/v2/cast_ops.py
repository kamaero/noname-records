"""The book's cast as the legend editor sees it: who speaks, where, and in what colour.

The reader builds a legend per chapter from that chapter's spans. The editor wants
the whole cast — every `Character` row of the book, speaking or not — with the
counts derived from the effective v2 attributions rather than from v1's
`appears_in`, because v2 is what the actor will read. Colours go through the same
`build_character_style_maps` the reader uses, so a character that has no colour yet
shows the same auto colour in both places; saving one stays with the v1 budget
endpoint, which already validates and persists it.
"""
from __future__ import annotations

import collections
import json
from typing import Callable

from app.models import Character, ScriptChapter
from app.services.author_profile import normalize_name
from app.services.character_colors import build_character_style_maps, resolve_character_style
from app.services.shared_runtime import is_narrator_name
from app.services.telegram import names_match
from app.v2.models import V2Segment
from app.v2.store import load_chapter_segments
from app.v2.reader import NARRATOR, UNSURE, _narrator_actor, effective_book_attributions


def is_placeholder_name(name: str) -> bool:
    """`UNSURE` and `UNSURE:*` are the model's «I don't know», not members of the cast."""
    key = str(name or "").strip().upper()
    return key == UNSURE or key.startswith(UNSURE + ":")


def split_aliases(raw: str) -> list[str]:
    """Aliases as v1 stores them: a comma list; a JSON list is accepted too."""
    text = str(raw or "").strip()
    if not text:
        return []
    if text.startswith("["):
        try:
            data = json.loads(text)
            if isinstance(data, list):
                return [str(item).strip() for item in data if str(item).strip()]
        except ValueError:
            pass
    return [part.strip() for part in text.replace(";", ",").replace("\n", ",").split(",") if part.strip()]


def parse_actor_name(raw: str) -> tuple[str, bool]:
    """Разобрать сырую запись диктора: имя без хвостового «?» и «предварительно ли».

    Хвостовой знак вопроса — нотация владельца для предварительного назначения:
    «Химера — Натали Ким?». «???» без имени перед знаками — пометка «ещё не
    решил», а не предварительное назначение: назначать предварительно некого,
    роль свободна, и после срезания вопросов у неё не остаётся имени актёра.

    Единственное место этого разбора: `character_map` и `role_intersections`
    обязаны понимать одного и того же актёра одинаково — иначе «Натали Ким» и
    «Натали Ким?» превращаются в двух разных актёров, и пара с предварительным
    назначением пропадает из пересечений, ни разу не показавшись.
    """
    text = str(raw or "").strip()
    name = text.rstrip("?").strip()
    tentative = bool(name) and text.endswith("?")
    return name, tentative


def approved_actor(raw: str) -> str:
    """Актёр, которого запись в касте действительно утверждает. Предварительный — никто.

    Хвостовой «?» — предложение агента, а не решение, и всё, что спрашивает «этот
    актёр утверждён на роль?», обязано спрашивать здесь. Своя проверка на «?» на
    месте вызова была бы вторым разбором нотации: `names_match` знака не видит —
    он чистит строку до букв и цифр, — и стоит одному месту забыть проверку, как
    «Натали Ким?» становится настоящей Натали Ким: её проба ложится дублем, роль
    показывается ей как своя в панели записи, а рассылка касту зовёт её к микрофону.

    Пусто — и когда актёра нет вовсе, и когда он лишь предложен: тому, кто спрашивает
    про утверждение, это один и тот же ответ.
    """
    name, tentative = parse_actor_name(raw)
    return "" if tentative else name


def as_tentative(raw: str) -> str:
    """Имя, каким его записывает агент: всегда со знаком вопроса.

    Агент назначает предварительно и никак иначе, поэтому знак не требуется от него, а
    дописывается за него. Пустое имя — снятие собственного предложения, а «???» —
    пометка «ещё не решил»: ни там, ни там назначать некого, и обе строки остаются
    какими пришли.

    Разбор — через `parse_actor_name`, а не поиском «?» в строке: нотация хвостовых
    вопросов живёт в одном месте, иначе карта персонажей и пересечения ролей начнут
    понимать одного актёра по-разному.
    """
    name, tentative = parse_actor_name(raw)
    if not name or tentative:
        return raw
    return f"{name}?"


def cast_canonicalizer(cast_rows: list[Character]) -> Callable[[str], str]:
    """Строит свёртку имени к написанию, которое использует карта персонажей.

    Сравнение имён идёт по свёрнутой форме (регистр, ударения, пробелы — как в
    `app.services.author_profile.normalize_name`) и по алиасам персонажа — теми
    же строительными блоками, что и у `_resolver` в `app.v2.budget_ops`
    (`normalize_name` + `split_aliases`). Свернулось во что-то из карты —
    возвращаем написание карты; не свернулось никуда — имя остаётся собственной
    строкой.

    Общая точка для `character_map` (собирает строки таблицы) и
    `role_intersections` (ищет пары одного актёра): написание, которое таблица
    подклеивает к роли через регистр или алиас, обязано быть видимо и там, и
    там — иначе те же две реплики читаются как две разные роли в зависимости от
    того, какой из модулей на них смотрит.
    """
    by_key: dict[str, str] = {}
    names = [str(row.name or "").strip() for row in cast_rows]
    for name in names:
        if name:
            by_key.setdefault(normalize_name(name), name)
    for row, name in zip(cast_rows, names):
        if not name:
            continue
        for alias in split_aliases(row.aliases):
            by_key.setdefault(normalize_name(alias), name)

    def canonicalize(candidate: str) -> str:
        return by_key.get(normalize_name(candidate), candidate)

    return canonicalize


class CreateRoleError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def chapter_role_counts(db, chapter_id: str) -> dict[str, int]:
    """Role name → lines it speaks in this chapter, from the effective v2 attributions.

    The recording screen used to read these from the fountain text v1 generated, so on
    a v2 book its role list came out empty and an actor had nothing to pick. The
    narrator is counted like anybody else here: somebody records the narration too.
    """
    from app.v2.reader import effective_attributions

    segments = load_chapter_segments(db, chapter_id=str(chapter_id or "").strip())
    if not segments:
        return {}
    counts: collections.Counter = collections.Counter()
    for rows in effective_attributions(db, [segment.id for segment in segments]).values():
        # a replica is a paragraph the role speaks in, however the author's words tear it
        speakers = {str(row.speaker or "").strip() for row in rows}
        for name in speakers:
            if name and not is_placeholder_name(name):
                counts[name] += 1
    return dict(counts)


def create_role(db, *, book_id: str, name: str, actor_uid: str, actor_name: str = "") -> dict:
    """Add a role to the book's cast, or hand back the one that is already there.

    Called from the reader, where a missing role is found: the model named a speaker
    the cast never had, or the operator recognises somebody the model called UNSURE.
    Matching is the profile's folding — stress marks and case ignored, aliases counted
    — so «дгарни́н» does not become a second Дгарнин.

    The narrator is not a role here: it exists in every book by construction and has
    its own row in the legend. `UNSURE` is the model's «I don't know», not a person.
    """
    from app.models import ScriptBook
    from app.services.character_colors import ensure_character_style
    from app.v2.store import record_operator_intervention

    book = db.get(ScriptBook, str(book_id or "").strip())
    if book is None:
        raise CreateRoleError("book_not_found")

    wanted = " ".join(str(name or "").split())
    if not wanted:
        raise CreateRoleError("name_required")
    if is_placeholder_name(wanted):
        raise CreateRoleError("placeholder_name")
    if is_narrator_name(wanted):
        raise CreateRoleError("narrator_is_not_a_role")

    key = normalize_name(wanted)
    for row in db.query(Character).filter(Character.book_id == book.id).all():
        names = [str(row.name or ""), *split_aliases(row.aliases)]
        if any(normalize_name(item) == key for item in names if item):
            return {"created": False, "character_id": str(row.id), "name": str(row.name or "")}

    character = Character(book_id=book.id, name=wanted)
    ensure_character_style(character)
    db.add(character)
    db.flush()
    record_operator_intervention(
        db, book=book, action_type="v2_create_role", actor_uid=actor_uid, actor_name=actor_name,
        reason="v2: новая роль из читалки",
        payload={"character_id": str(character.id), "name": wanted},
    )
    return {"created": True, "character_id": str(character.id), "name": wanted}


def book_cast(db, book_id: str, *, only_speaking: bool = False, actor_name: str = "") -> list[dict]:
    """Legend rows for the book; the narrator first, then by v2 line count.

    `actor_name` is who is reading: every row it casts to him comes back with
    `mine`, so a picker can put his own parts on top. The cast and the account
    spell a person differently — «Белозёров Александр» against «Александр Белозёров» —
    and `names_match` is the studio's answer to that, shared with the recording panel.

    A speaker in an attribution is matched to a character by name or alias with the
    profile's folding (stress marks and case ignored). Speakers the cast does not
    know are not rows here — the cast editor edits `Character` rows, and an unknown
    speaker is the attribution's problem, visible in the reader's UNSURE legend.
    """
    reader = str(actor_name or "").strip()
    characters = [
        row for row in db.query(Character).filter(Character.book_id == book_id).all()
        if str(row.name or "").strip() and not is_placeholder_name(row.name)
    ]
    narrator_row = next((row for row in characters if is_narrator_name(row.name)), None)
    narrator_key = str(narrator_row.id) if narrator_row is not None else ""

    by_key: dict[str, str] = {}
    for row in characters:
        by_key.setdefault(normalize_name(row.name), str(row.id))
    for row in characters:
        for alias in split_aliases(row.aliases):
            by_key.setdefault(normalize_name(alias), str(row.id))

    chapter_index = {
        str(chapter_id): int(index or 0)
        for chapter_id, index in db.query(ScriptChapter.id, ScriptChapter.chapter_index)
        .filter(ScriptChapter.book_id == book_id).all()
    }
    segment_chapter = {
        str(segment_id): str(chapter_id)
        for segment_id, chapter_id in db.query(V2Segment.id, V2Segment.chapter_id)
        .filter(V2Segment.book_id == book_id).all()
    }

    def resolve(name: str) -> str | None:
        if not name or is_placeholder_name(name):
            return None
        return narrator_key if is_narrator_name(name) else by_key.get(normalize_name(name))

    # A book has a dozen distinct speakers and 13k spans; fold each name once.
    resolved: dict[str, str | None] = {}
    lines: collections.Counter = collections.Counter()
    chapters: dict[str, set[int]] = collections.defaultdict(set)
    # a replica is a paragraph the role speaks in: «— А, — сказал Пупип, — Б» is one
    counted: set[tuple[str, str]] = set()
    for segment_id, _start, _end, speaker, _source in effective_book_attributions(db, book_id):
        name = str(speaker or "").strip()
        if name not in resolved:
            resolved[name] = resolve(name)
        target = resolved[name]
        if target is None:
            continue
        if (target, str(segment_id)) not in counted:
            counted.add((target, str(segment_id)))
            lines[target] += 1
        chapters[target].add(chapter_index.get(segment_chapter.get(str(segment_id), ""), 0))

    bg_map, text_map, weight_map, style_map = build_character_style_maps(characters, extra_names=[NARRATOR, UNSURE])

    def style_of(name: str) -> tuple[str, str, str, str]:
        if name in bg_map:
            return bg_map[name], text_map[name], weight_map[name], style_map[name]
        return resolve_character_style(name)

    def entry(row: Character | None, key: str, *, is_narrator: bool) -> dict:
        name = str(row.name).strip() if row is not None else NARRATOR
        bg, fg, weight, style = style_of(name)
        actor = str(row.actor_name or "").strip() if row is not None else ""
        if is_narrator and not actor:
            actor = _narrator_actor(db, book_id, characters)
        # Роль своя, только если актёр утверждён: предложение агента («Натали Ким?»)
        # ролью не наделяет, а `names_match` знака вопроса не видит. Показывать имя
        # с вопросом в строке касты при этом надо — предложение видно, а роль не «моя».
        approved = approved_actor(actor)
        return {
            "mine": bool(reader and approved and names_match(approved, reader)),
            # what the pipeline learned about the character, and what the author says
            # about the voice — the two things a dictor needs and never saw
            "race": str(getattr(row, "race", "") or "").strip() if row is not None else "",
            "temperament": str(getattr(row, "temperament", "") or "").strip() if row is not None else "",
            "note": str(getattr(row, "operator_note", "") or "").strip() if row is not None else "",
            "character_id": str(row.id) if row is not None else "",
            "char_map_id": str(row.char_map_id or "") if row is not None else "",
            "name": name,
            "is_narrator": is_narrator,
            "actor_name": actor,
            "appears_in_chapters": sorted(chapters.get(key, set())),
            "lines_count": int(lines.get(key, 0)),
            "character_color": bg,
            "character_text_color": fg,
            "character_font_weight": weight,
            "character_font_style": style,
        }

    rows = [entry(narrator_row, narrator_key, is_narrator=True)]
    others = [row for row in characters if row is not narrator_row]
    others.sort(key=lambda row: (-lines.get(str(row.id), 0), str(row.name).lower()))
    for row in others:
        if only_speaking and not lines.get(str(row.id), 0):
            continue
        rows.append(entry(row, str(row.id), is_narrator=False))
    return rows

"""Две роли одного актёра, которые встречаются в тексте.

Режиссёр стремится к схеме «одна роль — один актёр». Роли, разнесённые по книге,
терпимы; роли, которые разговаривают друг с другом, — нет: актёр отвечает сам
себе. На двухстах ролях удержать это в голове невозможно, а слышно в готовом
спектакле, когда переделывать поздно.

Здесь только арифметика. Допустимо ли пересечение — решает человек: близнецы
звучат одинаково намеренно, у демонов тяжёлая обработка голоса, рассказчик
вообще в другом регистре. Модуль не знает таких вещей и не должен.
"""
from __future__ import annotations

import collections

from app.models import Character, RolePairNote, ScriptChapter
from app.services.shared_runtime import is_narrator_name
from app.v2.cast_ops import cast_canonicalizer, is_placeholder_name, parse_actor_name
from app.v2.models import V2Segment
from app.v2.reader import effective_book_attributions
from app.time_utils import iso_utc

#: Пороги в абзацах. Первый бесспорен — это диалог. Второй приблизителен: границ
#: сцен в разметке нет, и «одна сцена» здесь означает «достаточно близко, чтобы
#: слушатель узнал голос».
DIALOGUE = 3
SCENE = 15

#: Короче этого сравнивать корни бессмысленно — совпадёт что угодно.
RACE_ROOT = 4


def race_root(race: str) -> str:
    """Корень расы: «фархеррим (высший демон)» → «фархеррим».

    Раса — свободный текст: 71 разное значение на 150 заполненных, с уточнением
    в скобках и родовыми формами. Одна раса пишется тремя способами, поэтому
    сравнивать строки целиком нельзя.
    """
    head = str(race or "").strip().lower().split("(")[0].strip()
    return head.split()[0] if head else ""


def same_race(a: str, b: str) -> bool:
    """Одна ли раса. Ошибаемся в сторону предупреждения.

    Родовые формы — «демон» и «демоница», «фархеррим» и «фархерримка» — это одна
    раса, и короткий корень оказывается началом длинного. Лишний флажок стоит
    человеку одного клика «так задумано»; пропущенный слышен в готовом спектакле,
    когда переделывать поздно.
    """
    x, y = race_root(a), race_root(b)
    if not x or not y:
        return False
    if x == y:
        return True
    short, long_ = (x, y) if len(x) <= len(y) else (y, x)
    return len(short) >= RACE_ROOT and long_.startswith(short)


def _severity(distance: int) -> str:
    if distance <= DIALOGUE:
        return "dialogue"
    if distance <= SCENE:
        return "scene"
    return "chapter"


def _pair_key(role_a: str, role_b: str) -> tuple[str, str]:
    """Пара ненаправленная: «А с Б» — то же самое, что «Б с А»."""
    a, b = str(role_a or "").strip(), str(role_b or "").strip()
    return (a, b) if a <= b else (b, a)


def acknowledge_pair(db, *, book_id: str, role_a: str, role_b: str, reason: str,
                     actor_uid: str = "", actor_name: str = "") -> dict:
    """Запомнить, что пересечение этой пары — так задумано.

    Спрашиваем один раз: если запись уже есть, обновляем причину, а не заводим
    вторую — иначе при повторном подтверждении в базе накапливались бы дубли.
    """
    a, b = _pair_key(role_a, role_b)
    existing = (
        db.query(RolePairNote)
        .filter(RolePairNote.book_id == book_id, RolePairNote.role_a == a, RolePairNote.role_b == b)
        .first()
    )
    if existing is not None:
        existing.reason = str(reason or "").strip()
        return {"created": 0, "role_a": a, "role_b": b}
    db.add(RolePairNote(book_id=book_id, role_a=a, role_b=b, reason=str(reason or "").strip(),
                        actor_uid=actor_uid, actor_name=actor_name))
    return {"created": 1, "role_a": a, "role_b": b}


def forget_pair(db, *, book_id: str, role_a: str, role_b: str) -> dict:
    """Отменить решение — предупреждение про пару вернётся."""
    a, b = _pair_key(role_a, role_b)
    removed = (
        db.query(RolePairNote)
        .filter(RolePairNote.book_id == book_id, RolePairNote.role_a == a, RolePairNote.role_b == b)
        .delete(synchronize_session=False)
    )
    return {"removed": int(removed), "role_a": a, "role_b": b}


def role_intersections(db, book_id: str) -> list[dict]:
    """Пары ролей одного актёра, встречающиеся в одной главе.

    Возвращает по паре одну строку: самая близкая встреча задаёт степень, а
    список общих глав показывает масштаб. Пара ненаправленная, имена в ответе
    отсортированы — иначе одно и то же пересечение приехало бы дважды.
    """
    book_id = str(book_id or "").strip()
    cast_rows = db.query(Character).filter(Character.book_id == book_id).all()
    cast_by_name = {str(row.name or "").strip(): row for row in cast_rows}
    # Тот же разбор «имя плюс хвостовой знак вопроса», что и у `character_map` —
    # иначе «Натали Ким» и «Натали Ким?» становятся двумя разными актёрами, а
    # «???» без имени (роль ещё не решена) — актёром «???», который склеивает
    # между собой все ничьи роли в фантомные пары.
    actor_of = {name: parse_actor_name(row.actor_name)[0] for name, row in cast_by_name.items()}
    # Та же свёртка написания, что строит таблицу карты: спикер разметки может
    # отличаться от карты регистром или быть алиасом персонажа, и без этой
    # свёртки такая пара для пересечений невидима — реплики есть, а актёр не
    # находится.
    canonicalize = cast_canonicalizer(cast_rows)
    chapter_of = dict(
        db.query(V2Segment.id, V2Segment.chapter_id).filter(V2Segment.book_id == book_id).all()
    )
    ordinal_of = dict(
        db.query(V2Segment.id, V2Segment.ordinal).filter(V2Segment.book_id == book_id).all()
    )
    index_of = {
        chapter_id: index
        for chapter_id, index in db.query(ScriptChapter.id, ScriptChapter.chapter_index)
        .filter(ScriptChapter.book_id == book_id).all()
    }

    # роль → глава → позиции реплик
    where: dict[str, dict[str, list[int]]] = collections.defaultdict(lambda: collections.defaultdict(list))
    for segment_id, _start, _end, speaker, _source in effective_book_attributions(db, book_id):
        raw_name = str(speaker or "").strip()
        if not raw_name:
            continue
        # Плейсхолдер («модель не уверена») не сворачиваем — сворачивать некуда,
        # это не персонаж. Остальное — тем же написанием, что и в таблице карты.
        name = raw_name if is_placeholder_name(raw_name) else canonicalize(raw_name)
        if is_narrator_name(name):
            # Рассказчик не пересекается ни с кем по существу: повествование и
            # речь — разные регистры, один актёр разводит их голосом. Он же
            # присутствует практически в каждой главе, так что оставить его в
            # расчёте значит утопить настоящие пересечения в шуме. Раньше это
            # лечилось пометкой по умолчанию, которую нельзя было снять; выход
            # из расчёта честнее и снимает то ограничение.
            continue
        actor = actor_of.get(name, "")
        if not actor:
            # Свободная роль ещё ничья, пересекаться нечему.
            continue
        chapter_id = chapter_of.get(segment_id)
        if chapter_id is None:
            continue
        where[name][chapter_id].append(ordinal_of.get(segment_id, 0))

    by_actor: dict[str, list[str]] = collections.defaultdict(list)
    for name in where:
        by_actor[actor_of[name]].append(name)

    out: list[dict] = []
    for actor, roles in by_actor.items():
        roles = sorted(roles)
        for i in range(len(roles)):
            for j in range(i + 1, len(roles)):
                a, b = roles[i], roles[j]
                shared = sorted(
                    set(where[a]) & set(where[b]),
                    key=lambda chapter_id: index_of.get(chapter_id, 10**9),
                )
                if not shared:
                    continue
                best = None
                for chapter_id in shared:
                    for x in where[a][chapter_id]:
                        for y in where[b][chapter_id]:
                            distance = abs(x - y)
                            if best is None or distance < best[0]:
                                best = (distance, chapter_id)
                race_a = str(getattr(cast_by_name.get(a), "race", "") or "")
                race_b = str(getattr(cast_by_name.get(b), "race", "") or "")
                out.append({
                    "actor": actor,
                    "role_a": a,
                    "role_b": b,
                    "distance": best[0],
                    "chapter": index_of.get(best[1], 0),
                    "severity": _severity(best[0]),
                    "chapters": [index_of.get(c, 0) for c in shared],
                    "race_a": race_a,
                    "race_b": race_b,
                    "same_race": same_race(race_a, race_b),
                })
    # Пересечение — арифметика, а его допустимость — решение человека. Правила
    # по умолчанию нет: читаем решения по паре (ключ ненаправленный), и если
    # решения нет — пара не признана, точка.
    notes = {
        (row.role_a, row.role_b): row
        for row in db.query(RolePairNote).filter(RolePairNote.book_id == book_id).all()
    }
    for row in out:
        key = _pair_key(row["role_a"], row["role_b"])
        note = notes.get(key)
        if note is not None:
            row["acknowledged"], row["reason"] = True, note.reason
            # Кто решил — важнее причины: карту правят двое, автор и режиссёр,
            # и основания у них разные.
            row["acknowledged_by"] = note.actor_name
            row["acknowledged_at"] = iso_utc(note.created_at) or ""
        else:
            row["acknowledged"], row["reason"] = False, ""
            row["acknowledged_by"], row["acknowledged_at"] = "", ""

    # Порядок задаёт близость, а раса — вес внутри неё. Страшен прежде всего
    # диалог: актёр отвечает сам себе, и это слышно. Одна раса делает диалог
    # хуже, но у ролей в разных концах главы не превращает «с натяжкой ок» в
    # срочное. Сортировка расой поверх расстояния подняла бы наверх пару,
    # отстоящую на двадцать три абзаца, и утопила бы разговор в упор.
    # Принятые решения не меняют эту логику — они лишь уводятся в конец
    # списка: то, что уже решено, не должно закрывать собой то, что ещё ждёт
    # решения, но и совсем пропадать из списка не должно.
    order = {"dialogue": 0, "scene": 1, "chapter": 2}
    out.sort(key=lambda row: (
        row["acknowledged"],
        order.get(row["severity"], 9),
        not row["same_race"],
        row["distance"],
        row["actor"],
        row["role_a"],
    ))
    return out

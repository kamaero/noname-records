"""Карта персонажей и разметка — в одной таблице.

Спикер в атрибуции хранится строкой, а не ссылкой на карту. Поэтому имя может
говорить, не существуя в карте, и существовать в карте, ни разу не заговорив.
Показывать только карту — значит показывать половину.

Сравнение имён идёт по свёрнутой форме (регистр, ударения, пробелы — как в
`app.services.author_profile.normalize_name`) и по алиасам персонажа — теми же
строительными блоками, что и у `_resolver` в `app.v2.budget_ops`
(`normalize_name` + `split_aliases`): «Похотливый Жрец» в разметке и
«Похотливый жрец» в карте — один персонаж, а не молчащий фантом рядом с
говорящим чужаком. Одного `_resolver` не воспроизводит намеренно: разбор
рассказчика (`is_narrator_name`) там особый случай — синонимы вроде «от
автора» схлопываются в одну строку бюджета, а здесь остаются собственной
строкой таблицы, потому что резолвер бюджета не должен путать её со второй
ролью рассказчика, которую кто-то мог завести в карте по ошибке. Свёртка не
трогает буквы корня: «Жена Ветциога» и «Жена Песгуоза» отличаются не
регистром, а буквой, и обязаны остаться двумя строками — ради разбора именно
такой опечатки таблица и существует.
"""
from __future__ import annotations

from app.models import AudioFile, Character, ScriptBook, ScriptChapter
from app.services.audio_naming import book_token
from app.services.audio_uploads import AUDITION, TAKE, derive_book_code
from app.services.author_profile import normalize_name
from app.services.shared_runtime import is_narrator_name
from app.v2.cast_ops import cast_canonicalizer, is_placeholder_name, parse_actor_name, split_aliases
from app.v2.models import V2Segment
from app.v2.reader import effective_book_attributions
from app.time_utils import iso_utc


def _effective_speakers(db, book_id: str, canonicalize) -> dict[str, tuple[int, set[str]]]:
    """Сколько реплик и в скольких главах говорит каждое имя (после свёртки к карте).

    Действующие версии сегментов даёт `effective_book_attributions` из
    `app.v2.reader` — тот же путь, которым книгу читает актёр. Второй, свой,
    способ посчитать «максимальная версия на сегмент» здесь не заводится: правка
    одной копии не дошла бы до другой.
    """
    chapter_of = dict(
        db.query(V2Segment.id, V2Segment.chapter_id).filter(V2Segment.book_id == book_id).all()
    )
    out: dict[str, tuple[int, set[str]]] = {}
    # Реплика — абзац, где роль говорит: «— А, — сказал Пупип, — Б» — одна, не две.
    counted: set[tuple[str, str]] = set()
    for segment_id, _start, _end, speaker, _source in effective_book_attributions(db, book_id):
        raw = str(speaker or "").strip()
        if not raw:
            continue
        name = raw if is_placeholder_name(raw) else canonicalize(raw)
        chapter_id = chapter_of.get(str(segment_id))
        lines, chapters = out.get(name, (0, set()))
        fresh = (name, str(segment_id)) not in counted
        counted.add((name, str(segment_id)))
        out[name] = (lines + (1 if fresh else 0), chapters | ({chapter_id} if chapter_id else set()))
    return out


def _claimed_chapters(character: Character | None) -> list[int]:
    """Главы «по мнению извлечения» (`appears_in`). Хранятся строкой через
    запятую; мусор в ней молча пропускаем — это подсказка для сверки, а не
    источник правды (им остаётся то, что посчитано по разметке — `chapters`)."""
    raw = str(getattr(character, "appears_in", "") or "")
    out = []
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            out.append(int(part))
    return sorted(out)


def _canon_known_names(db, book_id: str) -> set[str] | None:
    """Свёрнутые имена ролей, которых узнаёт канон автора. `None` — корпуса нет.

    Различие важное: без корпуса честный ответ «не знаем», а не «новый». Объявить
    двести ролей выдумкой из-за отсутствия справочника хуже, чем промолчать —
    автор пойдёт перепроверять то, что и так написал сам.
    """
    from app.models import ScriptBook

    book = db.get(ScriptBook, book_id)
    if book is None:
        return None
    import sqlite3

    from app.services.canon_reconcile import reconcile
    from app.services.canon_seed import CANON_DB
    from app.services.char_map_assess import _load_canon_rows, assess_char_map

    try:
        if not assess_char_map(db, book).canon_available:
            return None
        rows = _load_canon_rows(CANON_DB)
    except (OSError, sqlite3.Error, ValueError):
        # Корпус — внешний файл: его может не быть, он может быть недочитан.
        # Карта от этого работать не перестаёт, просто молчит о каноне. Импорты —
        # вне `try`: 2026-09-29 здесь молча сломалась сверка, когда удалили модуль,
        # из которого брали `assess_char_map`, — широкий `except` принял это за
        # «корпуса нет».
        return None
    cast_rows = db.query(Character).filter(Character.book_id == book_id).all()
    pairs = [(str(row.name or ""), split_aliases(row.aliases)) for row in cast_rows]
    confirmed = reconcile(pairs, rows).get("confirmed", [])
    return {normalize_name(str(item.get("proposal") or "")) for item in confirmed}


def character_map(db, book_id: str) -> list[dict]:
    """Строка на каждое имя из карты, из разметки, из аудио — или из нескольких сразу."""
    book_id = str(book_id or "").strip()
    cast_rows = db.query(Character).filter(Character.book_id == book_id).all()
    cast = {str(row.name or "").strip() for row in cast_rows}
    cast.discard("")
    canonicalize = cast_canonicalizer(cast_rows)
    # Строка карты по имени: источник всех полей роли, которые не считает
    # разметка (раса, возраст, характер, голос, алиасы, диктор, заявленные главы).
    # Диктор и остальные поля — свойство строки карты, а не разметки:
    # осиротевшая или ещё не заведённая строка их не несёт.
    by_name = {str(row.name or "").strip(): row for row in cast_rows}
    # Номер главы, а не её идентификатор: расхождение — сигнал о тексте, и
    # режиссёр идёт смотреть главу глазами. «Размечено две из трёх» не говорит,
    # какую искать.
    known = _canon_known_names(db, book_id)
    index_of = dict(
        db.query(ScriptChapter.id, ScriptChapter.chapter_index)
        .filter(ScriptChapter.book_id == book_id).all()
    )
    by_name.pop("", None)

    spoken = _effective_speakers(db, book_id, canonicalize)

    # Роль в аудио пишется именем персонажа, но принадлежность книге у записи
    # выражена кодом, а не идентификатором: фильтровать надо по нему, иначе роль
    # из соседней книги пометит чужую строку как записанную.
    book = db.get(ScriptBook, book_id)
    wanted_code = book_token(derive_book_code(str(getattr(book, "title", "") or "")))
    voiced = {
        canonicalize(str(role or "").strip())
        # Дубли и пробы — записи голоса роли; трек эмбиента ролью не является.
        for (role, code) in db.query(AudioFile.role, AudioFile.book_code)
        .filter(AudioFile.kind.in_((TAKE, AUDITION))).distinct().all()
        if str(role or "").strip() and book_token(str(code or "")) == wanted_code
    }

    rows = []
    for name in sorted(cast | set(spoken) | voiced):
        lines, chapters = spoken.get(name, (0, set()))
        character = by_name.get(name)
        # Разбор хвостового «?» — общий с `role_intersections`
        # (`app.v2.cast_ops.parse_actor_name`): обе стороны обязаны понимать,
        # кто такой актёр, одинаково.
        actor, tentative = parse_actor_name(getattr(character, "actor_name", ""))
        row = {
            "name": name,
            # Идентификатор строки карты: по нему экран сводит смету с картой.
            # Пусто у имени, которое говорит в разметке, но роли для него нет, —
            # сводить такое не с чем, и подставлять сюда имя было бы ложью.
            "character_id": str(getattr(character, "id", "") or ""),
            "in_cast": name in cast,
            "in_markup": name in spoken,
            "lines": lines,
            "chapters": len(chapters),
            "chapter_numbers": sorted(index_of[c] for c in chapters if c in index_of),
            "has_audio": name in voiced,
            "is_placeholder": is_placeholder_name(name),
            "actor_name": actor,
            "actor_tentative": tentative,
            "race": str(getattr(character, "race", "") or ""),
            "age": str(getattr(character, "age", "") or ""),
            "temperament": str(getattr(character, "temperament", "") or ""),
            # Историческое имя поля: заполнено кастинговым заданием, а не заметкой.
            "voice": str(getattr(character, "operator_note", "") or ""),
            "aliases": str(getattr(character, "aliases", "") or ""),
            "claimed_chapters": _claimed_chapters(character) if character is not None else [],
            # Пусто — канон не подключён; «не знаем» честнее, чем «новый».
            "canon_status": ("" if known is None
                             else ("known" if normalize_name(name) in known else "new")),
            "updated_at": (iso_utc(character.updated_at) if character is not None else None) or "",
        }
        rows.append(row)
    # Вниз по числу реплик: наверху окажется то, что важнее всего разобрать.
    rows.sort(key=lambda row: (-row["lines"], row["name"]))
    return rows


class CharacterMapError(Exception):
    """Отказ с кодом, который экран покажет словами."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _row(db, book_id: str, name: str) -> dict:
    # Точное совпадение со строкой, которую вернула character_map: свёртка
    # регистра и алиасов уже случилась там, здесь только ищем готовую строку
    # по её итоговому написанию. Имя, которого среди этих строк нет — опечатка
    # или написание, до которого свёртка не дотянулась, — даёт not_found.
    wanted = str(name or "").strip()
    for row in character_map(db, book_id):
        if row["name"] == wanted:
            return row
    raise CharacterMapError("not_found")


def _character_row(db, book_id: str, name: str) -> Character | None:
    return (
        db.query(Character)
        .filter(Character.book_id == str(book_id or "").strip(), Character.name == name)
        .first()
    )


#: Что можно править на этом экране. Имя роли сюда не входит: переименование
#: разводит карту и разметку, и для этого есть слияние (`merge_speaker`).
EDITABLE = {"race", "age", "temperament", "voice", "aliases", "actor_name"}

#: `voice` на экране — это `operator_note` в базе. Поле названо исторически, а
#: заполнено кастинговыми заданиями, и переименовывать колонку ради красоты
#: значит трогать миграцией то, что и так работает.
_COLUMN = {"voice": "operator_note"}


def update_role(db, book_id: str, name: str, fields: dict) -> dict:
    """Поменять поля роли. Трогает только названные ключи — соседние поля,
    которые правит другая сторона (автор или режиссёр) не в этот же момент,
    остаются как были."""
    character = _character_row(db, book_id, name)
    if character is None:
        raise CharacterMapError("not_in_cast")
    touched = []
    for key, value in (fields or {}).items():
        if key not in EDITABLE:
            continue
        setattr(character, _COLUMN.get(key, key), str(value or "").strip())
        touched.append(key)
    db.add(character)
    return {"name": character.name, "fields": sorted(touched)}


def _lost_row_payload(character: Character | None, *, name: str) -> dict:
    """Всё, что версии атрибуций не хранят и что нечем будет восстановить,
    если строка карты исчезнет: диктор, цвет, алиасы, связь с каноном автора,
    заметка оператора, раса, возраст, темперамент, заявленные главы. Не
    «удалили X», а что именно потеряно.

    Раса, возраст и темперамент — редактируемые поля этого экрана, а версии
    атрибуций хранят только разметку: кто что говорит, а не кем персонаж
    является. На живой книге это 150 рас и 149 темпераментов, вписанных
    человеком, — слияние роли без этих полей в журнале уносит их безвозвратно,
    и восстановить их неоткуда.

    Ручные ставки здесь же, и это не мелочь: восстановленная по журналу строка
    без них тихо съедет на тарифный план студии, и расхождение всплывёт в смете,
    а не на экране. На живой книге такие ставки стоят у трёх персонажей.
    """
    if character is None:
        return {"name": name}
    return {
        "name": character.name,
        "actor_name": character.actor_name,
        "character_color": character.character_color,
        "aliases": character.aliases,
        "author_character_id": character.author_character_id,
        "operator_note": character.operator_note,
        "manual_rate_rub_per_min": character.manual_rate_rub_per_min,
        "manual_fixed_rub": character.manual_fixed_rub,
        "race": character.race,
        "age": character.age,
        "temperament": character.temperament,
        "appears_in": character.appears_in,
    }


def delete_character(db, book_id: str, name: str, *, actor_uid: str = "", actor_name: str = "") -> dict:
    """Убрать строку карты. Только у того, кто молчит и не записан.

    Говорящее имя удалять нельзя: разметка не изменится, реплики останутся на
    имени, которого в карте больше нет, и рассогласование станет только больше.
    Запись без строки карты (четвёртый вид строки — есть только аудио) сюда не
    попадёт: `_row` вернёт её, но `has_audio` откажет раньше, чем дело дойдёт
    до удаления — удалять там нечего, строки карты не существует.
    """
    from app.v2.store import record_operator_intervention

    book_id = str(book_id or "").strip()
    row = _row(db, book_id, name)
    if row["is_placeholder"]:
        raise CharacterMapError("placeholder")
    if row["lines"]:
        raise CharacterMapError("speaks")
    if row["has_audio"]:
        raise CharacterMapError("has_audio")
    character = _character_row(db, book_id, row["name"])
    deleted = (
        db.query(Character)
        .filter(Character.book_id == book_id, Character.name == row["name"])
        .delete(synchronize_session=False)
    )
    book = db.get(ScriptBook, book_id)
    if book is not None and deleted:
        record_operator_intervention(
            db, book=book, action_type="v2_delete_character",
            actor_uid=actor_uid, actor_name=actor_name,
            payload=_lost_row_payload(character, name=row["name"]),
        )
    return {"deleted": int(deleted), "name": row["name"]}


def adopt_speaker(db, book_id: str, name: str) -> dict:
    """Завести в карте имя, которое уже говорит. Разметку не трогает.

    Заводить можно только реально говорящее имя (`in_markup`) — иначе это не
    «узаконить то, что размечено», а завести пустую строку карты вручную, для
    чего есть другая ручка. Плейсхолдер («модель не уверена») в карту не идёт
    никогда: это не персонаж, а сигнал незаконченной разметки. Рассказчик — тоже
    никогда: в карте он один, а синоним в разметке («от автора», «narrator»)
    выглядит как отдельное говорящее имя ровно потому, что таблица его не
    схлопывает (см. докстроку модуля) — завести его значило бы родить второго
    рассказчика, которого резолвер бюджета однажды может выбрать вместо первого.
    """
    row = _row(db, book_id, name)
    if row["is_placeholder"]:
        raise CharacterMapError("placeholder")
    if is_narrator_name(row["name"]):
        raise CharacterMapError("narrator_is_not_a_role")
    if not row["in_markup"]:
        raise CharacterMapError("does_not_speak")
    if row["in_cast"]:
        # Уже заведён — вызов второй раз ничего не меняет, а не падает.
        return {"created": 0, "name": row["name"]}
    db.add(Character(book_id=str(book_id or "").strip(), name=row["name"]))
    return {"created": 1, "name": row["name"]}


def merge_speaker(db, *, book_id: str, source: str, target: str,
                  actor_uid: str, actor_name: str = "") -> dict:
    """Переклеить реплики одного имени на другое и убрать исходное из карты.

    Новой версией, а не правкой на месте: версии — единственный откат, который
    остаётся у автора после слияния, задевшего шестьдесят глав.

    Сегмент переписывается ЦЕЛИКОМ. Взять только спаны сливаемого имени значило бы
    оставить соседние реплики в прежней версии — то есть стереть их, потому что
    читается всегда максимальная. Такая потеря не даёт ни ошибки, ни следа.

    Спаны выбираются ТОЙ ЖЕ свёрткой, что построила строку в таблице
    (`cast_canonicalizer`), а не голым `normalize_name`: `normalize_name` сворачивает
    всегда, а таблица — только когда написание нашлось в карте. Вне карты «Дракон»
    и «ДРАКОН» — две строки и два предпросмотра; сличай их `normalize_name`, и
    слияние одной списало бы реплики обеих — лист обещал N, уехало бы больше.
    """
    from app.v2.reader import effective_book_rows
    from app.v2.models import V2Attribution
    from app.v2.store import record_operator_intervention, store_attributions

    book_id = str(book_id or "").strip()
    from_row = _row(db, book_id, source)
    to_row = _row(db, book_id, target)
    if from_row["is_placeholder"] or to_row["is_placeholder"]:
        raise CharacterMapError("placeholder")
    # Рассказчика нельзя слить ни как источник, ни как цель: у него тысячи
    # реплик, и синоним рассказчика в этой таблице — собственная строка (см.
    # докстринг модуля), а не то же самое, что бюджетный резолвер схлопывает
    # сам. `adopt_speaker` и `rename_role` уже отбивают рассказчика этим кодом —
    # у слияния цена ошибки выше их обоих, а защиты не было вовсе.
    if is_narrator_name(from_row["name"]) or is_narrator_name(to_row["name"]):
        raise CharacterMapError("narrator_is_not_a_role")
    if from_row["name"] == to_row["name"]:
        raise CharacterMapError("same_name")
    if not from_row["lines"]:
        # Источник ничего не говорит — сливать нечего. Это переодетое удаление
        # строки карты, и оно обязано пройти те же проверки, что и delete_character
        # (там для дублей диктора отказ уже есть) — не удрать от них через merge.
        raise CharacterMapError("nothing_to_merge")
    if not to_row["in_cast"]:
        # Цель обязана быть в карте. Иначе слияние делает хуже, чем было: строка
        # карты исходного имени исчезает, а реплики оказываются на имени, о котором
        # система не знает, — и вместо одного разошедшегося имени получаем ноль
        # персонажей. Правильный путь из двух обратимых шагов: сначала «завести
        # в карте», потом сливать.
        raise CharacterMapError("target_not_in_cast")

    cast_rows = db.query(Character).filter(Character.book_id == book_id).all()
    canonicalize = cast_canonicalizer(cast_rows)
    rows = effective_book_rows(
        db, V2Attribution, book_id,
        V2Attribution.span_start, V2Attribution.span_end, V2Attribution.speaker,
        V2Attribution.confidence, V2Attribution.source,
    )
    by_segment: dict[str, list[tuple]] = {}
    for segment_id, start, end, speaker, confidence, origin in rows:
        by_segment.setdefault(segment_id, []).append((start, end, speaker, confidence, origin))

    def is_source(speaker) -> bool:
        return canonicalize(str(speaker or "").strip()) == from_row["name"]

    touched = {
        segment_id for segment_id, spans in by_segment.items()
        if any(is_source(speaker) for _s, _e, speaker, _c, _o in spans)
    }

    records = []
    moved_lines = 0
    for segment_id in touched:
        for start, end, speaker, confidence, _origin in by_segment[segment_id]:
            moved = is_source(speaker)
            if moved:
                moved_lines += 1
            records.append({
                "unit_id": segment_id,
                "span_start": start,
                "span_end": end,
                "speaker": to_row["name"] if moved else speaker,
                "confidence": confidence,
                "source": "operator",
            })
    stored = store_attributions(db, records) if records else 0

    lost_character = _character_row(db, book_id, from_row["name"])
    db.query(Character).filter(
        Character.book_id == book_id, Character.name == from_row["name"],
    ).delete(synchronize_session=False)

    book = db.get(ScriptBook, book_id)
    if book is not None:
        payload = _lost_row_payload(lost_character, name=from_row["name"])
        payload.update({"source": from_row["name"], "target": to_row["name"],
                        "segments": len(touched), "lines": moved_lines})
        record_operator_intervention(
            db, book=book, action_type="v2_merge_character",
            actor_uid=actor_uid, actor_name=actor_name,
            payload=payload,
        )
    return {"source": from_row["name"], "target": to_row["name"],
            "segments": len(touched), "lines": moved_lines, "rows": stored}


def rename_role(db, *, book_id: str, name: str, new_name: str,
                move_lines: bool = False,
                actor_uid: str = "", actor_name: str = "") -> dict:
    """Переименовать роль, при необходимости перевезя её реплики.

    Переименование строки карты НЕ трогает разметку — и это ловушка, из-за
    которой карта и разметка разъезжаются молча: настоящая роль начинает
    числиться молчащей, а её реплики висят на опечатке. Обе таблицы при этом
    выглядят исправными по отдельности.

    Поэтому у говорящей роли переименование без согласия отклоняется: экран
    обязан сказать, сколько реплик и в скольких главах переедет, и спросить.
    Реплики переезжают НОВОЙ версией через `store_attributions`, сегментами
    целиком, тем же путём, что и слияние.

    Переименование в имя, которое уже есть в карте, — это слияние, а не
    переименование: у него свои проверки и своя цена. Пускать в него отсюда
    значит обойти их. «В карте» здесь значит строку `character_map` целиком —
    имя может жить только в разметке (говорит, но не заведено), совпасть с
    чужим написанием после свёртки регистра или оказаться алиасом другой роли:
    все три случая — то же самое скрытое слияние, и им не пройти мимо `merge_speaker`.

    Служебная метка (`UNSURE`) и синоним рассказчика — тоже не имя роли: уйти
    туда значило бы завести дверь без ручки (`adopt`/`delete`/`merge`/сам
    `rename` отказывают по коду `placeholder` на служебной метке) или тихо
    отдать чужие реплики рассказчику.

    Рассказчик отклоняется и как ИСТОЧНИК, не только как цель: он проходит
    `character_map` той же строкой, что и говорящая роль (`_effective_speakers`
    не знает про `is_narrator_name`, только про плейсхолдер), и без этой
    проверки честный `needs_consent` называл бы настоящую цену и переносил
    тысячи реплик рассказчика на новое имя по-настоящему.
    """
    from app.v2.reader import effective_book_rows
    from app.v2.models import V2Attribution
    from app.v2.store import record_operator_intervention, store_attributions

    book_id = str(book_id or "").strip()
    target = str(new_name or "").strip()
    row = _row(db, book_id, name)
    if row["is_placeholder"]:
        raise CharacterMapError("placeholder")
    # Рассказчик — тоже не имя роли, и не только как цель (см. ниже): у него в
    # разметке тысячи реплик, `character_map` заводит ему строку тем же путём,
    # что и говорящей роли (`_effective_speakers` не знает про `is_narrator_name`,
    # только про плейсхолдер), и `needs_consent` этой строке отвечает честно —
    # значит без этой проверки экран называет реальную цену и переносит
    # реплики рассказчика на новое имя по-настоящему. Проверка ДО `needs_consent`
    # и до всякой записи — источнику нельзя дать даже спросить согласие.
    if is_narrator_name(row["name"]):
        raise CharacterMapError("narrator_is_not_a_role")
    if not target:
        raise CharacterMapError("empty_name")
    if target == row["name"]:
        raise CharacterMapError("same_name")
    if is_placeholder_name(target):
        raise CharacterMapError("placeholder")
    if is_narrator_name(target):
        raise CharacterMapError("narrator_is_not_a_role")

    cast_rows = db.query(Character).filter(Character.book_id == book_id).all()
    canonicalize = cast_canonicalizer(cast_rows)
    # Сравнение идёт со свёрнутым написанием, а не с сырой строкой `Character`:
    # цель может быть занята говорящим именем без строки карты, чужим написанием
    # в другом регистре или алиасом соседней роли — во всех трёх случаях таблица
    # уже показывает эту строку, просто не как `Character.name` буквально.
    #
    # Сворачиваются ОБЕ стороны, и по-разному: `cast_canonicalizer` подтягивает
    # алиас к канону («Ящер» → «Дракон»), но знает только имена каста — имя,
    # которое говорит в разметке без своей строки каста, в его словарь не
    # попадает вовсе. Сравнивай после него побуквенно — и «ДРАКОН» из разметки
    # разошёлся бы с набранным «Дракон»: отказа нет, роль молча забирает чужие
    # реплики, журнал вмешательств отчитывается только о своих, а обратное
    # переименование подберёт оба написания и сделает ошибку необратимой.
    # Поэтому последнее слово за `normalize_name` — той же свёрткой регистра и
    # ударений, которой строки таблицы сравнивает весь остальной модуль.
    folded_target = normalize_name(canonicalize(target))
    if any(
        normalize_name(existing["name"]) == folded_target and existing["name"] != row["name"]
        for existing in character_map(db, book_id)
    ):
        raise CharacterMapError("name_taken")
    if row["lines"] and not move_lines:
        raise CharacterMapError("needs_consent")

    moved_lines = 0
    touched: set[str] = set()
    if row["lines"]:
        rows = effective_book_rows(
            db, V2Attribution, book_id,
            V2Attribution.span_start, V2Attribution.span_end, V2Attribution.speaker,
            V2Attribution.confidence, V2Attribution.source,
        )
        by_segment: dict[str, list[tuple]] = {}
        for segment_id, start, end, speaker, confidence, origin in rows:
            by_segment.setdefault(segment_id, []).append((start, end, speaker, confidence, origin))

        def is_source(speaker) -> bool:
            return canonicalize(str(speaker or "").strip()) == row["name"]

        touched = {
            segment_id for segment_id, spans in by_segment.items()
            if any(is_source(speaker) for _s, _e, speaker, _c, _o in spans)
        }
        records = []
        for segment_id in touched:
            # Сегмент переписывается целиком: соседние спаны обязаны попасть в
            # новую версию, иначе они исчезнут вместе с ней и без следа.
            for start, end, speaker, confidence, _origin in by_segment[segment_id]:
                moved = is_source(speaker)
                if moved:
                    moved_lines += 1
                records.append({
                    "unit_id": segment_id,
                    "span_start": start,
                    "span_end": end,
                    "speaker": target if moved else speaker,
                    "confidence": confidence,
                    "source": "operator",
                })
        if records:
            store_attributions(db, records)

    character = _character_row(db, book_id, row["name"])
    if character is not None:
        character.name = target

    book = db.get(ScriptBook, book_id)
    if book is not None:
        record_operator_intervention(
            db, book=book, action_type="v2_rename_character",
            actor_uid=actor_uid, actor_name=actor_name,
            payload={"was": row["name"], "name": target,
                     "segments": len(touched), "lines": moved_lines},
        )
    return {"name": target, "was": row["name"],
            "lines": moved_lines, "segments": len(touched)}

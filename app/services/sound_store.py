"""Звуковая разметка в базе: применение прогона, правки человека, перепривязка.

Правило одно: человек главнее прогона. Правленое (`source='human'`) прогон не трогает,
отклонённое (`dismissed`) не воскрешает — иначе каждая перечитка отменяла бы работу.
"""
from __future__ import annotations

import logging

from app.models import SoundMarker, SoundPlace, SoundPlacePair
from app.pipeline.sound_markers import Checked, find_quote, norm
from app.time_utils import iso_utc, utcnow_naive

logger = logging.getLogger(__name__)

ANCHOR_CHARS = 80
SCENE_FIELDS = ("time_of_day", "weather", "ambience", "mood", "music_queries")
EDITABLE = {"scene": SCENE_FIELDS, "transition": ("what",), "sound": ("description", "queries")}
#: на сколько абзацев сцена нового прогона может уехать и остаться той же сценой
SCENE_REUSE_REACH = 3


def _segment(chapter_id: str, ordinal: int) -> str:
    return f"{chapter_id}:{int(ordinal):05d}"


def _ordinal(segment_id: str) -> int:
    return int(str(segment_id).rsplit(":", 1)[1])


def _key(kind: str, segment_id: str, quote: str) -> tuple:
    return (kind, segment_id, norm(quote) if kind == "sound" else "")


def touch_sessions(db, chapter_ids) -> None:
    """Правка разметки главы устарила её архивный проект — кроме сданных глав.

    Решение владельца «б»: у главы с `delivered_at` монтаж уже идёт в архивной
    сессии, и правка маркеров после сдачи не должна помечать её устаревшей —
    только правка ролей/дублей (как и раньше, `mark_session_outdated` зовётся
    напрямую из тех мест).

    Импорт `mark_session_outdated` — внутри функции: `chapter_delivery` сам тянет
    из этого модуля `active_chapter_markers`/`_ordinal` для сборки `.sesx`, и
    импорт на уровне файла замкнул бы два модуля друг на друге.
    """
    from app.models import ScriptChapter
    from app.services.chapter_delivery import mark_session_outdated

    ids = {str(cid) for cid in (chapter_ids or []) if cid}
    if not ids:
        return
    delivered = {
        cid for (cid,) in db.query(ScriptChapter.id)
        .filter(ScriptChapter.id.in_(ids), ScriptChapter.delivered_at.isnot(None))
    }
    for chapter_id in ids - delivered:
        mark_session_outdated(db, chapter_id)


def place_rows(db, book_id: str) -> list[dict]:
    rows = (db.query(SoundPlace).filter(SoundPlace.book_id == book_id, SoundPlace.merged_into.is_(None))
            .order_by(SoundPlace.created_at.asc()).all())
    return [{"id": p.id, "name": p.name, "description": p.description} for p in rows]


def _live_place(db, book_id: str, place_id: str) -> SoundPlace | None:
    """Место по id, доведённое сквозь `merged_into` до живого (не склеенного) места этой книги.

    Цикл в `merged_into` в базе появиться не должен, но заходить в бесконечный цикл на
    кривых данных нельзя — `seen` останавливает обход при повторе.
    """
    seen: set[str] = set()
    while place_id and place_id not in seen:
        seen.add(place_id)
        row = db.get(SoundPlace, place_id)
        if row is None or row.book_id != book_id:
            return None
        if row.merged_into is None:
            return row
        place_id = row.merged_into
    return None


def _place_for(db, book_id: str, place: dict) -> tuple[str, bool]:
    if place.get("id"):
        live = _live_place(db, book_id, str(place["id"]))
        if live is not None:
            return live.id, False
        # id чужой книги или ведёт в никуда — не бракуем сцену, ищем место по имени, как без id.
    wanted = norm(place.get("name"))
    for row in db.query(SoundPlace).filter(SoundPlace.book_id == book_id, SoundPlace.merged_into.is_(None)):
        if norm(row.name) == wanted:
            return row.id, False
    # Имя могло совпасть с местом, которое с тех пор склеили в другое (модель не знает о
    # склейке) — тогда верный адрес не новая карточка, а место, куда его склеили.
    for row in db.query(SoundPlace).filter(SoundPlace.book_id == book_id, SoundPlace.merged_into.isnot(None)):
        if norm(row.name) == wanted:
            live = _live_place(db, book_id, row.merged_into)
            if live is not None:
                return live.id, False
    row = SoundPlace(book_id=book_id, name=place.get("name") or "без имени",
                     description=place.get("description") or "",
                     ambience_queries=list(place.get("ambience_queries") or []))
    db.add(row)
    db.flush()
    return row.id, True


def _scene_reuse(old_rows: list[tuple[int, SoundMarker]],
                 new_scenes: list[tuple[int, str]]) -> dict[int, SoundMarker]:
    """Какая сцена прошлого прогона достаётся какой сцене нового — чтобы не порвать эмбиент.

    `old_rows` — `[(абзац, строка), ...]`, `new_scenes` — `[(абзац, место), ...]` в порядке
    прогона; ответ — `индекс новой сцены → старая строка`.

    Сначала разбираются все совпадения по абзацу: тот же абзац — та же сцена, даже если
    прогон назвал другое место (место у сцены правится, трек с неё снимать не за что). Если
    на одном абзаце лежат две старые сцены, берётся та, чьё место совпало с новым.

    Остаток разбирается сопоставлением (алгоритм Куна с дополняющими путями), а не
    жадно по ближайшим парам. Жадность теряла деньги: пара с расстоянием 1 забирала
    строку, которая была единственным кандидатом у другой сцены, и та заводилась заново —
    оплаченный трек отваливался, хотя раскладка на всех существовала. Поэтому сначала
    важно, чтобы пар нашлось как можно БОЛЬШЕ, и только при равном числе пар — что они
    ближе: сцены разбираются в порядке (расстояние до ближайшего кандидата, абзац), а
    кандидаты каждой — в порядке (расстояние, абзац старой, id). Когда пара возможна
    только одна, её берёт ближайшая сцена; ответ не зависит от порядка выдачи СУБД.

    Одна старая строка достаётся не больше чем одной новой сцене.
    """
    taken: set[str] = set()
    out: dict[int, SoundMarker] = {}
    rest: list[tuple[int, int, str]] = []
    for index, (ordinal, place_id) in enumerate(new_scenes):
        same = [row for old, row in old_rows if row.id not in taken and old == ordinal]
        if not same:
            rest.append((index, ordinal, place_id))
            continue
        row = next((r for r in same if place_id and r.place_id == place_id), same[0])
        taken.add(row.id)
        out[index] = row

    # Кандидаты остатка: то же место, не дальше SCENE_REUSE_REACH абзацев, строка свободна.
    rows_by_id = {row.id: row for _old, row in old_rows}
    candidates: dict[int, list[str]] = {}     # индекс новой сцены → id кандидатов, ближние первыми
    queue: list[tuple[int, int, int]] = []    # (расстояние до ближайшего кандидата, абзац, индекс)
    for index, ordinal, place_id in rest:
        near = sorted((abs(old - ordinal), old, row.id) for old, row in old_rows
                      if row.id not in taken and place_id and row.place_id == place_id
                      and abs(old - ordinal) <= SCENE_REUSE_REACH)
        if not near:
            continue
        candidates[index] = [row_id for _distance, _old, row_id in near]
        queue.append((near[0][0], ordinal, index))
    owner: dict[str, int] = {}          # id старой строки → индекс новой сцены, которая её взяла

    def claim(index: int, seen: set[str]) -> bool:
        """Дополняющий путь: сцена берёт свободную строку либо уговаривает ту, что держит
        приглянувшуюся, подвинуться на другого своего кандидата."""
        for row_id in candidates.get(index, ()):
            if row_id in seen:
                continue
            seen.add(row_id)
            held = owner.get(row_id)
            if held is None or claim(held, seen):
                owner[row_id] = index
                return True
        return False

    for _distance, _paragraph, index in sorted(queue):
        claim(index, set())
    for row_id, index in sorted(owner.items()):
        out[index] = rows_by_id[row_id]
    return out


def _scene_ordinals(rows: list[SoundMarker]) -> list[tuple[int, SoundMarker]]:
    """Сцены прогона с разобранным абзацем, по (абзац, id) — порядок не должен зависеть от
    того, как строки вернула СУБД.

    Строка с битым `segment_id` не валит главу целиком: её пропускаем с предупреждением (та
    же защита, что у `chapter_delivery.active_scene_rows`). Узнана она не будет и удалится —
    ровно как удалялась до появления сопоставления.
    """
    out = []
    for row in rows:
        try:
            out.append((_ordinal(row.segment_id), row))
        except (ValueError, IndexError):
            logger.warning("сцена %s с битым segment_id %r пропущена при сопоставлении",
                           row.id, row.segment_id)
    out.sort(key=lambda item: (item[0], item[1].id))
    return out


def apply_chapter(db, *, book_id: str, chapter_id: str, text_sha256: str, checked: Checked,
                  run_id: str, texts: dict[int, str]) -> dict:
    """Применить прогон к главе: людское не трогаем, отклонённое не воскрешаем, сцену узнаём.

    Сцены не переписываются с нуля. На сцене может висеть оплаченный трек эмбиента
    (`AmbientTrack.marker_id` смотрит на `id` маркера), и новая строка с новым id отвязала бы
    его: файл остался бы в папке главы, но выпал из `.sesx`, и глава снова просила бы платить
    ElevenLabs. Поэтому сцена прошлого прогона сопоставляется сцене нового (`_scene_reuse`) и
    правится на месте, сохраняя `id`. Переходы и звуки трека не держат — они, как и раньше,
    удаляются и пишутся заново.

    `added` — сколько маркеров именно ДОБАВЛЕНО: узнанная сцена не новая и в счёт не идёт
    (её трек остался на месте, платить и генерировать нечего).
    """
    reanchor_chapter(db, chapter_id)  # правки и отказы — на актуальные места до сравнения с прогоном
    kept = (db.query(SoundMarker).filter(SoundMarker.chapter_id == chapter_id)
            .filter((SoundMarker.source == "human") | (SoundMarker.status == "dismissed")).all())
    blocked = set()
    for r in kept:
        blocked.add(_key(r.kind, r.segment_id, r.quote))
        from_segment = (r.payload or {}).get("from_segment")
        if from_segment:
            blocked.add(_key(r.kind, from_segment, r.quote))  # человек перенёс — старое место тоже закрыто
    old = (db.query(SoundMarker).filter(SoundMarker.chapter_id == chapter_id, SoundMarker.source == "llm",
                                        SoundMarker.status.in_(("active", "lost"))).all())
    added = places_new = 0

    # Места разбираем до сопоставления: сцену узнают по абзацу и месту, а место новой сцены
    # известно только после `_place_for` (имя могло уехать в склейку).
    fresh_scenes: list[tuple[dict, str]] = []
    for scene in checked.scenes:
        place_id, fresh = _place_for(db, book_id, scene["place"])
        places_new += int(fresh)
        if _key("scene", _segment(chapter_id, scene["ordinal"]), "") in blocked:
            continue  # закрыто человеком или отказом — такая сцена и старую строку не занимает
        fresh_scenes.append((scene, place_id))
    reuse = _scene_reuse(_scene_ordinals([r for r in old if r.kind == "scene"]),
                         [(s["ordinal"], place_id) for s, place_id in fresh_scenes])
    reused = {row.id for row in reuse.values()}
    for row in old:
        if row.id not in reused:
            db.delete(row)  # по строкам, а не bulk-delete: сессия не должна держать удалённые

    def add(kind: str, ordinal: int, payload: dict, quote: str = "", place_id: str | None = None) -> None:
        nonlocal added
        segment_id = _segment(chapter_id, ordinal)
        if _key(kind, segment_id, quote) in blocked:
            return
        payload = dict(payload, anchor_text=texts.get(ordinal, "")[:ANCHOR_CHARS])
        db.add(SoundMarker(book_id=book_id, chapter_id=chapter_id, segment_id=segment_id, kind=kind,
                           place_id=place_id, payload=payload, quote=quote, text_sha256=text_sha256,
                           run_id=run_id))
        added += 1

    for index, (scene, place_id) in enumerate(fresh_scenes):
        payload = {k: scene[k] for k in SCENE_FIELDS}
        row = reuse.get(index)
        if row is None:
            add("scene", scene["ordinal"], payload, place_id=place_id)
            continue
        ordinal = scene["ordinal"]
        row.segment_id, row.place_id = _segment(chapter_id, ordinal), place_id
        row.payload = dict(payload, anchor_text=texts.get(ordinal, "")[:ANCHOR_CHARS])
        row.text_sha256, row.run_id, row.status = text_sha256, run_id, "active"
        row.updated_at = utcnow_naive()
    for item in checked.transitions:
        add("transition", item["ordinal"], {"what": item["what"]})
    for item in checked.sounds:
        add("sound", item["ordinal"], {"description": item["description"], "queries": item["queries"]},
            quote=item["quote"])
    db.flush()
    return {"added": added, "places_new": places_new}


def _chapter_texts(db, chapter_id: str) -> tuple[dict[int, str], str]:
    from app.services.consilium_engine import text_fingerprint
    from app.v2.store import load_chapter_segments

    paragraphs = [(int(s.ordinal), s.text or "") for s in load_chapter_segments(db, chapter_id=chapter_id)]
    return dict(paragraphs), text_fingerprint(paragraphs)


def reanchor_chapter(db, chapter_id: str) -> int:
    """Текст главы сменился — найти каждому маркеру его абзац заново или честно потерять.

    Отклонённое (`dismissed`) тоже переносится — иначе после сдвига текста прогон не
    узнает старый отказ по ключу и вернёт то же самое заново. Но статус отклонённого
    не меняется: не нашли — так и остаётся отклонённым, а не «потерянным».
    """
    texts, sha = _chapter_texts(db, chapter_id)
    moved = 0
    for row in db.query(SoundMarker).filter(SoundMarker.chapter_id == chapter_id,
                                            SoundMarker.status.in_(("active", "lost", "dismissed")),
                                            SoundMarker.text_sha256 != sha).all():
        old = _ordinal(row.segment_id)
        if row.kind == "sound" and row.quote:
            found = find_quote(texts, old, row.quote, reach=len(texts))
        else:
            # Звук без цитаты (добавлен руками без неё) ищет своё место так же, как
            # сцена/переход — по слепку начала абзаца, а не сразу уходит в потерянные.
            anchor = norm((row.payload or {}).get("anchor_text", ""))
            candidates = sorted((n for n, t in texts.items() if anchor and norm(t[:ANCHOR_CHARS]) == anchor),
                                key=lambda n: abs(n - old))
            found = candidates[0] if candidates else None
        if row.status == "dismissed":
            if found is None:
                continue  # ключ остаётся старым — и это правильно, отказ не «теряется»
            row.segment_id, row.text_sha256 = _segment(chapter_id, found), sha
        elif found is None:
            # Помечаем text_sha256 и на потере — иначе каждый GET заново перебирал бы
            # тот же непривязанный маркер: без этого фильтр `text_sha256 != sha` не
            # находит его исчерпанным, и строка переписывается на каждом чтении главы.
            row.status, row.text_sha256 = "lost", sha
        else:
            row.segment_id, row.status, row.text_sha256 = _segment(chapter_id, found), "active", sha
        row.updated_at = utcnow_naive()
        moved += 1
    db.flush()
    return moved


_HIDDEN_PAYLOAD_KEYS = ("anchor_text", "from_segment")


def _view(row: SoundMarker) -> dict:
    return {"id": row.id, "kind": row.kind, "segment_id": row.segment_id, "status": row.status,
            "source": row.source, "place_id": row.place_id or "", "quote": row.quote,
            "payload": {k: v for k, v in (row.payload or {}).items() if k not in _HIDDEN_PAYLOAD_KEYS}}


def place_views(db, book_id: str, place_ids: set[str] | None = None) -> list[dict]:
    """Место книги для интерфейса: имя, подложка и главы, где место активно сейчас.

    `place_ids=None` — все живые места книги (карточка книги); заданное множество —
    только они (список главы). Одна выборка глав книги и одна группированная выборка
    активных маркеров по месту — вместо запроса на каждое место (было в `chapter_markers`).
    """
    from app.models import ScriptChapter

    query = db.query(SoundPlace).filter(SoundPlace.book_id == book_id, SoundPlace.merged_into.is_(None))
    if place_ids is not None:
        query = query.filter(SoundPlace.id.in_(place_ids))
    places = query.order_by(SoundPlace.created_at.asc()).all()
    if not places:
        return []
    index_of = {c.id: int(c.chapter_index) for c in
               db.query(ScriptChapter.id, ScriptChapter.chapter_index).filter(ScriptChapter.book_id == book_id)}
    chapters_by_place: dict[str, set[int]] = {}
    marker_rows = (db.query(SoundMarker.place_id, SoundMarker.chapter_id)
                   .filter(SoundMarker.book_id == book_id, SoundMarker.status == "active",
                          SoundMarker.place_id.isnot(None)).distinct())
    for place_id, chapter_id in marker_rows:
        chapters_by_place.setdefault(place_id, set()).add(index_of.get(chapter_id, 0))
    return [{"id": p.id, "name": p.name, "description": p.description,
             "ambience_queries": list(p.ambience_queries or []),
             "chapters": sorted(chapters_by_place.get(p.id, set()))}
            for p in places]


def active_chapter_markers(db, chapter_id: str) -> tuple[list[dict], dict[str, SoundPlace]]:
    """Активные маркеры главы и их места — для сборки `.sesx`, без побочных записей в базу.

    В отличие от `chapter_markers`, не зовёт `reanchor_chapter`: сборка сессии читает
    состояние как есть, а не пересверяет текст заново — это дело фонового прогона и
    ручной правки, а не каждого открытия монтажёром вкладки «Скачать».

    Порядок в пределах одного абзаца и вида — по времени создания, а затем по `id`:
    без этого при двух звуках на одном абзаце сортировка держалась бы на том, в каком
    порядке их вернула СУБД (не гарантировано), и номер/порядок маркеров дрожал бы
    между пересборками одной и той же главы без единой правки.
    """
    rows = (db.query(SoundMarker).filter(SoundMarker.chapter_id == chapter_id,
                                         SoundMarker.status == "active").all())
    rows.sort(key=lambda r: (_ordinal(r.segment_id), ("scene", "transition", "sound").index(r.kind),
                             r.created_at, r.id))
    place_ids = {r.place_id for r in rows if r.place_id}
    places = ({p.id: p for p in db.query(SoundPlace).filter(SoundPlace.id.in_(place_ids))}
             if place_ids else {})
    return [_view(r) for r in rows], places


def chapter_markers(db, chapter_id: str) -> dict:
    reanchor_chapter(db, chapter_id)
    rows = (db.query(SoundMarker).filter(SoundMarker.chapter_id == chapter_id,
                                         SoundMarker.status.in_(("active", "lost"))).all())
    rows.sort(key=lambda r: (_ordinal(r.segment_id), ("scene", "transition", "sound").index(r.kind)))
    place_ids = {r.place_id for r in rows if r.place_id}
    book_id = rows[0].book_id if rows else ""
    places = place_views(db, book_id, place_ids) if place_ids else []
    ambient = scene_ambient(db, chapter_id)

    def view(row: SoundMarker) -> dict:
        out = _view(row)
        if row.kind == "scene":
            out["ambient"] = ambient.get(row.id)
        return out

    return {"markers": [view(r) for r in rows if r.status == "active"],
            "lost": [view(r) for r in rows if r.status == "lost"], "places": places}


def scene_ambient(db, chapter_id: str) -> dict[str, dict]:
    """Эмбиент каждой сцены главы для карточки: `marker_id` → `{id, updated_at, status, prompt,
    audio_file_id, file_name, error}` по последнему не-`replaced` треку сцены.

    Файл — готового трека сцены (`ambient_engine.done_tracks`), даже если последняя попытка
    упала: упавшая перегенерация не трогает трек, что лежит в проекте, и послушать его в
    карточке можно. Строка `done`, чей файл стёрт, готовой не считается и не показывается —
    сцена снова ждёт трека.
    """
    from app.models import AmbientTrack, AudioFile
    from app.services.ambient_engine import done_tracks

    rows = (db.query(AmbientTrack)
            .filter(AmbientTrack.chapter_id == chapter_id, AmbientTrack.status != "replaced")
            .order_by(AmbientTrack.created_at.asc(), AmbientTrack.id.asc()).all())
    if not rows:
        return {}
    ready = {track.id: name for track, name in
             done_tracks(db, AmbientTrack, AudioFile.canonical_filename)
             .filter(AmbientTrack.chapter_id == chapter_id)}
    latest: dict[str, AmbientTrack] = {}
    done: dict[str, AmbientTrack] = {}
    for row in rows:
        if row.status == "done" and row.id not in ready:
            continue
        latest[row.marker_id] = row
        if row.status == "done":
            done[row.marker_id] = row
    out = {}
    for marker_id, row in latest.items():
        track = done.get(marker_id)
        # `id` и `updated_at` — чтобы снимок в карточке менялся и от сбоя с тем же текстом:
        # по ним карточка понимает, что прогон кончился.
        out[marker_id] = {"id": row.id, "updated_at": iso_utc(row.updated_at) or "",
                          "status": row.status, "prompt": row.prompt or "",
                          "audio_file_id": str(track.audio_file_id or "") if track is not None else "",
                          "file_name": str(ready.get(track.id) or "") if track is not None else "",
                          "error": row.error or ""}
    return out


def _queries_list(value) -> list[str]:
    """Список запросов из поля правки: строку принимаем как один запрос, не как буквы."""
    if isinstance(value, str):
        value = [value]
    return [str(q).strip() for q in (value or []) if str(q).strip()][:3]


def _clean(kind: str, fields: dict) -> dict:
    out = {}
    for key in EDITABLE[kind]:
        if key in fields:
            value = fields[key]
            out[key] = _queries_list(value) if key.endswith("queries") else str(value or "").strip()
    return out


def edit_marker(db, marker_id: str, fields: dict):
    row = db.get(SoundMarker, str(marker_id or ""))
    if row is None or row.status == "dismissed":
        return "not_found"
    if "segment_id" in fields:
        segment_id = str(fields["segment_id"] or "")
        if not segment_id.startswith(f"{row.chapter_id}:"):
            return "bad_segment"
        try:
            ordinal = _ordinal(segment_id)
        except (ValueError, IndexError):
            return "bad_segment"
        texts, sha = _chapter_texts(db, row.chapter_id)
        if ordinal not in texts:
            return "bad_segment"
        quote = str(fields.get("quote") or "").strip() if "quote" in fields else row.quote
        if row.kind == "sound" and norm(quote) not in norm(texts[ordinal]):
            return "bad_quote"  # перенос звука без цитаты в целевом абзаце — брак, ничего не меняем
        if segment_id != row.segment_id and "from_segment" not in (row.payload or {}):
            row.payload = dict(row.payload or {}, from_segment=row.segment_id)  # запомнить, откуда унесли
        row.segment_id, row.status, row.text_sha256 = segment_id, "active", sha
        row.payload = dict(row.payload or {}, anchor_text=texts[ordinal][:ANCHOR_CHARS])
        if row.kind == "sound":
            row.quote = quote
    if "place_id" in fields and row.kind == "scene":
        place = db.get(SoundPlace, str(fields["place_id"] or ""))
        if place is None or place.book_id != row.book_id:
            return "bad_place"
        row.place_id = place.id
    row.payload = dict(row.payload or {}, **_clean(row.kind, fields))
    row.source, row.updated_at = "human", utcnow_naive()
    db.flush()
    return row


def add_marker(db, *, chapter_id: str, segment_id: str, kind: str, fields: dict):
    from app.models import ScriptChapter

    if kind not in EDITABLE:
        return "bad_kind"
    chapter = db.get(ScriptChapter, str(chapter_id or ""))
    if chapter is None:
        return "not_found"
    if not str(segment_id).startswith(f"{chapter.id}:"):
        return "bad_segment"
    try:
        ordinal = _ordinal(segment_id)
    except (ValueError, IndexError):
        return "bad_segment"
    texts, sha = _chapter_texts(db, chapter.id)
    if ordinal not in texts:
        return "bad_segment"
    quote = str(fields.get("quote") or "").strip()
    if quote and norm(quote) not in norm(texts[ordinal]):
        return "bad_quote"
    row = SoundMarker(book_id=chapter.book_id, chapter_id=chapter.id, segment_id=str(segment_id), kind=kind,
                      source="human", quote=quote, text_sha256=sha,
                      payload=dict(_clean(kind, fields), anchor_text=texts[ordinal][:ANCHOR_CHARS]))
    if kind == "scene" and fields.get("place_id"):
        place = db.get(SoundPlace, str(fields["place_id"]))
        if place is None or place.book_id != chapter.book_id:
            return "bad_place"
        row.place_id = place.id
    db.add(row)
    db.flush()
    return row


def dismiss_marker(db, marker_id: str) -> bool:
    row = db.get(SoundMarker, str(marker_id or ""))
    if row is None:
        return False
    row.status, row.updated_at = "dismissed", utcnow_naive()
    db.flush()
    return True


def edit_place(db, place_id: str, fields: dict):
    place = db.get(SoundPlace, str(place_id or ""))
    if place is None:
        return None
    if "name" in fields and not str(fields["name"] or "").strip():
        return "bad_name"  # без имени карточка места неотличима от прочих
    for key in ("name", "description"):
        if key in fields:
            setattr(place, key, str(fields[key] or "").strip())
    if "ambience_queries" in fields:
        place.ambience_queries = _queries_list(fields["ambience_queries"])
    place.updated_at = utcnow_naive()
    db.flush()
    return place


def record_pairs(db, book_id: str, pairs: list[dict]) -> int:
    known = {frozenset((p.place_a, p.place_b)) for p in db.query(SoundPlacePair).filter_by(book_id=book_id)}
    live = {p["id"] for p in place_rows(db, book_id)}
    added = 0
    for pair in pairs:
        key = frozenset((pair.get("a"), pair.get("b")))
        if len(key) != 2 or not key <= live or key in known:
            continue
        db.add(SoundPlacePair(book_id=book_id, place_a=pair["a"], place_b=pair["b"],
                              reason=str(pair.get("reason") or "")))
        known.add(key)
        added += 1
    db.flush()
    return added


def _rewrite_pairs_after_merge(db, book_id: str, gone: str, keep: str, merged_pair_id: str) -> None:
    """После склейки `gone`→`keep` кандидаты, смотревшие на `gone`, переезжают на `keep`.

    Самопара (обе стороны — теперь `keep`) или дубликат уже существующей пары — гасим
    переехавшую как `obsolete`, не предлагаем оператору решать несуществующий выбор.
    """
    others = (db.query(SoundPlacePair)
              .filter(SoundPlacePair.book_id == book_id, SoundPlacePair.status == "candidate",
                      SoundPlacePair.id != merged_pair_id)
              .filter((SoundPlacePair.place_a == gone) | (SoundPlacePair.place_b == gone)).all())
    for other in others:
        if other.place_a == gone:
            other.place_a = keep
        if other.place_b == gone:
            other.place_b = keep
        if other.place_a == other.place_b:
            other.status = "obsolete"
            continue
        duplicate = (db.query(SoundPlacePair)
                     .filter(SoundPlacePair.book_id == book_id, SoundPlacePair.id != other.id)
                     .filter(((SoundPlacePair.place_a == other.place_a) &
                              (SoundPlacePair.place_b == other.place_b)) |
                             ((SoundPlacePair.place_a == other.place_b) &
                              (SoundPlacePair.place_b == other.place_a))).first())
        if duplicate is not None:
            other.status = "obsolete"


def decide_pair(db, pair_id: str, merge: bool) -> str:
    pair = db.get(SoundPlacePair, str(pair_id or ""))
    if pair is None or pair.status != "candidate":
        return "not_found"
    place_a = db.get(SoundPlace, pair.place_a)
    place_b = db.get(SoundPlace, pair.place_b)
    if (place_a is None or place_b is None or place_a.book_id != pair.book_id
            or place_b.book_id != pair.book_id
            or place_a.merged_into is not None or place_b.merged_into is not None):
        return "not_found"  # одно из мест уже удалено или склеено в обход этой пары
    if merge:
        keep, gone = pair.place_a, pair.place_b
        db.query(SoundMarker).filter(SoundMarker.place_id == gone).update({"place_id": keep})
        place_b.merged_into = keep
        pair.status = "merged"
        _rewrite_pairs_after_merge(db, pair.book_id, gone, keep, pair.id)
    else:
        pair.status = "apart"
    db.flush()
    return pair.status


def pair_rows(db, book_id: str) -> list[dict]:
    names = {p["id"]: p["name"] for p in place_rows(db, book_id)}
    return [{"id": p.id, "a": {"id": p.place_a, "name": names.get(p.place_a, "")},
             "b": {"id": p.place_b, "name": names.get(p.place_b, "")}, "reason": p.reason}
            for p in db.query(SoundPlacePair).filter_by(book_id=book_id, status="candidate")
            if p.place_a in names and p.place_b in names]

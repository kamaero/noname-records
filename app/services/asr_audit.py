"""Строгая проверка рассказчика собранной главы.

Когда глава собралась, владелец хочет знать, дословно ли рассказчик прочитал Сценарий:
пропуски слов, предложений, абзацев — недопустимы; перестановка слов и актёрские
приёмы голосом (смешки, кряхтения, вздохи) — допустимы. Боевая сверка (`asr_align`)
для этого не годится: она построчная, нечёткая и ловит реплики, а не слова. Bake-off
2026-11-06 (проверка точности) показал второе:
услышанное любой моделью — не дословно, а с перефразами («зовется»→«называется»,
«не уверен»→«неуверен») и фонетическими искажениями имён. Поэтому судья — LLM со
сценарием и глоссарием имён в контексте, а обвинять рассказчика можно только после
подтверждения находки второй моделью: иначе письмо о «пропуске» сказало бы диктору
неправду об его работе и отправило бы перезаписывать уже сданное.

Необязательные строки: простые авторские ремарки-репризы («- сказал он», жесты,
реакции) рассказчик пропускает намеренно — в микс они не входят; титулы глав читает
другой диктор. Аудит их не требует (см. `classify_narrator_line`).
"""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import math
import hashlib
from difflib import SequenceMatcher
from types import SimpleNamespace


from app.config import settings
from app.services.asr_run import _role_pieces_in_segments, _transcribe_retrying, book_phrases
from app.services.asr_transcribe import compress_for_asr, transcribe_file
from app.services.shared_runtime import is_narrator_name
from app.time_utils import iso_utc, utcnow_naive

logger = logging.getLogger(__name__)

#: Сколько строк сценария за один вызов судьи. Глава на 100+ строк — 4–5 вызовов,
#: каждый с полным контекстом своих строк: расширять батч дешевле, чем резать точность.
JUDGE_BATCH = 30
#: Окно подтверждения, секунды от краёв найденного фрагмента. Пропущенное слово
#: обычно слито с соседним, а не вырезано чисто: запас нужен, чтобы второй слух
#: услышал спорное место целиком, и меньше окно — тем чаще он услышит обрыв.
CONFIRM_PAD_SECONDS = 12

#: Глаголы речи, жеста и реакции, которыми заканчивается простая авторская ремарка.
#: Собраны с боевых реплик книги и разметки bake-off: ремарка —
#: «- <наречие> <глагол> <говорящий>, <манера>». Сравнение по токенам, а не префиксу:
#: «без тени сожаления сказал» и «промедлив секунду, признался» — тоже ремарки.
#: Ё нормализуется в е до сравнения, поэтому здесь только формы на е.
_TAG_VERBS = frozenset({
    "сказал", "сказала", "сказано", "произнес", "произнесла", "произнесло",
    "произнесли", "спросил", "спросила", "спросило", "спросили", "ответил", "ответила",
    "ответило", "ответили", "молвил", "молвила", "промолвил", "промолвила", "бросил",
    "бросила", "добавил", "добавила", "добавило", "уточнил", "уточнила", "пояснил",
    "пояснила", "объяснил", "объяснила", "объяснял", "объясняла", "поведал", "поведала",
    "продолжил", "продолжила", "продолжал", "продолжала", "перебил", "перебила",
    "переспросил", "переспросила", "осведомился", "осведомилась", "поинтересовался",
    "поинтересовалась", "посоветовал", "посоветовала", "предложил", "предложила",
    "повторил", "повторила", "отозвался", "отозвалась", "воскликнул", "воскликнула",
    "провозгласил", "провозгласила", "заявил", "заявила", "признался", "призналась",
    "солгал", "учил", "учила", "говорил", "говорила", "шепнул", "шепнула", "прошипел",
    "прошипела", "взревел", "пролепетал", "пролепетала", "пробормотал", "пробормотала",
    "буркнул", "буркнула", "рыкнул", "рыкнула", "провибрировал", "передразнил",
    "отметил", "отметила", "подытожил", "подытожила", "предположил", "предположила",
    "хмыкнул", "хмыкнула", "фыркнул", "фыркнула", "вздохнул", "вздохнула", "усмехнулся",
    "усмехнулась", "ухмыльнулся", "ухмыльнулась", "осклабился", "осклабилась",
    "оскалился", "оскалилась", "улыбнулся", "улыбнулась", "рассмеялся", "рассмеялась",
    "засмеялся", "засмеялась", "изумился", "изумилась", "удивился", "удивилась",
    "опешил", "опешила", "смутился", "смутилась", "озадачился", "озадачилась",
    "восхитился", "восхитилась", "одобрил", "одобрила", "похвалил", "похвалила",
    "согласился", "согласилась", "посерьезнел", "понял", "поняла", "дернулся", "дрогнул",
    "вздрогнул", "вздрогнула", "кивнул", "кивнула", "закивал", "закивала", "покивал",
    "покивала", "прищурился", "прищурилась", "потер", "потерла", "взялся", "взялась",
    "обвел", "промокнул", "сунул", "раздвинул", "раздвинула", "поправил",
    "поправила", "повернулся", "повернулась", "отступил", "отступила", "оперся",
    "пожал", "принял", "приняла", "отшвырнул", "отшвырнула", "разомкнул",
    "разомкнула", "собрался", "собралась", "выделил", "выделила", "задумался",
    "задумалась", "задумались", "сузил", "сузила", "подтвердил", "подтвердила",
    "сверкнул", "сверкнула", "скрывать", "соглашался", "соглашалась", "закончил",
    "закончила", "закончилось", "убрал", "убрала", "рассуждал", "рассуждала",
    "глядя", "разглядывая", "разминая", "провозгласил",
})
#: Ремарка-оповещение без глагола речи: «- раздался звонкий голосок».
_TAG_VOICE_RE = re.compile(r"^(?:раздался|раздалось|прозвучал[ао]?|послышал[аося]+)\b")
#: Обстоятельство-событие превращает ремарку в мини-сюжет («…, когда персонаж вошел»):
#: событие надо читать, иначе в миксе пропадёт факт.
_TAG_EVENT_CLAUSE_RE = re.compile(r"\b(когда|после|прежде чем|пока)\b|[.!?…]\s*\S")
_TITLE_RE = re.compile(r"^\s*(глава|chapter)\s+\d", re.IGNORECASE)
_WORD = re.compile(r"[а-яёa-z0-9]+")


class AsrAuditError(RuntimeError):
    """Аудит не запустился. Текст — код для лога, не фраза для человека."""


def classify_narrator_line(text: str) -> str:
    """Как аудит относится к строке рассказчика: required | tag | title.

    Правило владельца (2026-11-06): простые ремарки-репризы и титулы в микс не входят,
    рассказчик их не читает. Пограничные случаи решаем в пользу required — недотребовать
    безопаснее: владелец увидит флаг и решит сам, а недоглядеть пропуск хуже лишнего письма.
    """
    raw = str(text or "").strip()
    if not raw:
        return "required"
    if _TITLE_RE.match(raw):
        return "title"
    # Хвостовое тире («- осклабился демон. –») — артефакт границы цитаты, а не вторая фраза.
    # Ремарка всегда начинается с тире: строка без тире — повествование или цитата,
    # глагол речи внутри не делает её репризой («И персонаж сказал «нет»» — текст).
    if not raw.lstrip().startswith(("-", "–", "—")):
        return "required"
    body = raw.lstrip("-–— ").rstrip("-–— ").strip()
    body = body.lower().replace("ё", "е")
    # Ремарка короткая: длинная тирада с тире — уже повествование, а не реприза.
    if len(_WORD.findall(body)) > 14:
        return "required"
    # Вторая фраза или событийная клауза — в строке есть что читать.
    if _TAG_EVENT_CLAUSE_RE.search(body):
        return "required"
    tokens = set(_WORD.findall(body))
    if tokens & _TAG_VERBS:
        return "tag"
    if _TAG_VOICE_RE.match(body):
        return "tag"
    return "required"


def narrator_lines(db, chapter_id: str) -> dict:
    """Строки рассказчика главы, классифицированные для аудита.

    Тот же источник, что у `expected_lines` (`_role_pieces_in_segments`): куски
    атрибуции в порядке сценария. Индексы в ответе — индексы в полном списке кусков,
    чтобы находки связывались со сценарием по адресу сегмента, а не по номеру строки
    (вставка реплики выше сдвигает номер, но не адрес).
    """
    narrator, pieces = _role_pieces_in_segments(db, chapter_id, "Рассказчик")
    if not narrator:
        raise AsrAuditError("narrator_role_not_found")
    out = {"required": [], "tags": [], "titles": []}
    for index, (text, segment_id) in enumerate(pieces):
        kind = classify_narrator_line(text)
        out[{"tag": "tags", "title": "titles"}.get(kind, "required")].append(
            {"index": index, "segment_id": str(segment_id), "text": text}
        )
    return out


def latest_narrator_take(db, chapter) -> "object | None":
    """Свежайший дубль рассказчика главы — по `uploaded_at`, как решил владелец.

    Проверяем именно роль строки: рассказчик книги — «Рассказчик», но имя файла
    и строка роли связаны через `is_narrator_name`, чтобы «Автор»/«От автора»
    не проскочили мимо.
    """
    from app.models import AudioFile, ScriptBook, ScriptChapter
    from app.services.audio_uploads import TAKE, derive_book_code

    book = db.get(ScriptBook, str(chapter.book_id))

    if book is None:
        raise AsrAuditError("chapter_book_not_found")
    from app.services.audio_naming import book_token
    code = book_token(derive_book_code(str(book.title or "")))
    # В старой схеме файлы не связаны с книгой и главой по ID. Совпадение
    # кодов делает источник неоднозначным: запрещаем платный запуск.
    books = db.query(ScriptBook).all()
    if not code or sum(book_token(derive_book_code(str(b.title or ""))) == code for b in books) != 1:
        raise AsrAuditError("ambiguous_book_code")
    label = str(chapter.chapter_title or "").strip() or f"Глава {chapter.chapter_index}"
    chapters = db.query(ScriptChapter).filter(ScriptChapter.book_id == book.id).all()
    if sum((str(item.chapter_title or "").strip() or f"Глава {item.chapter_index}") == label for item in chapters) != 1:
        raise AsrAuditError("ambiguous_chapter_label")
    takes = (
        db.query(AudioFile)
        .filter(AudioFile.kind == TAKE, AudioFile.chapter == label, AudioFile.book_code == derive_book_code(str(book.title or "")))
        .order_by(AudioFile.uploaded_at.desc())
        .all()
    )
    for take in takes:
        if is_narrator_name(str(take.role or "")):
            return take
    return None


def _narrator_snapshot(db, chapter_id: str) -> dict:
    from app.models import AudioFile
    from app.services.chapter_delivery import _script_layout
    from app.services.asr_run import chapter_replicas
    from app.services.author_profile import normalize_name
    from app.services.audio_uploads import derive_book_code
    from app.services.audio_naming import book_token

    lines = narrator_lines(db, chapter_id)
    layout = _script_layout(db, chapter_id)
    if layout is None:
        raise AsrAuditError("chapter_not_found")
    all_lines = sorted([r for rows in lines.values() for r in rows], key=lambda r: r["index"])
    replicas = [row for row in chapter_replicas(db, chapter_id) if is_narrator_name(row["role"])]
    addresses = {(normalize_name(row["role"]), row["role_index"], row["segment_id"]): i
                 for i, row in enumerate(replicas)}
    clips, sources = [], {}
    for role, clip in layout[3]:
        if not is_narrator_name(role):
            continue
        role_index = getattr(clip, "role_index", None)
        index = addresses.get((normalize_name(role), role_index, clip.segment_id))
        if role_index is None:
            candidates = [r["index"] for r in all_lines if r["segment_id"] == clip.segment_id
                          and clip.name.startswith(r["text"][:80])]
            if len(candidates) != 1:
                raise AsrAuditError("ambiguous_narrator_clip")
            index = candidates[0]
        if index is None or index not in {r["index"] for r in all_lines}:
            raise AsrAuditError("unknown_narrator_clip_index")
        source_id = str(clip.audio_file_id)
        audio = db.get(AudioFile, source_id)
        if audio is None:
            raise AsrAuditError("narrator_source_not_found")
        # Старые записи ищутся и по заголовку главы, который повторяется в разных
        # книгах. Такой кандидат может попасть в саму раскладку; оплачивать и
        # обвинять чужую книгу при этом нельзя.
        book = layout[1]
        if book_token(str(audio.book_code or "")) != book_token(derive_book_code(str(book.title or ""))):
            raise AsrAuditError(f"narrator_source_scope_mismatch:{source_id}")
        sources[source_id] = {key: getattr(audio, key) for key in
            ("id", "stored_key", "location", "actor_name", "role", "uploaded_at", "md5", "size_bytes", "verify_state")}
        clips.append({"index": int(index), "role": role, "role_index": role_index,
                      "segment_id": str(clip.segment_id),
                      "source_audio_file_id": source_id, "start": float(clip.source_in),
                      "end": float(clip.source_out)})
    signature = {"lines": lines, "clips": clips, "sources": [
        {"id": key, "uploaded_at": str(item["uploaded_at"] or ""),
         "md5": str(item["md5"] or ""), "size_bytes": int(item["size_bytes"] or 0)}
        for key, item in sorted(sources.items())]}
    fingerprint = hashlib.sha256(json.dumps(signature, ensure_ascii=False, sort_keys=True,
                                           separators=(",", ":")).encode()).hexdigest()
    return {"lines": lines, "clips": clips, "sources": sources, "fingerprint": fingerprint}


def narrator_input_fingerprint(db, chapter_id: str) -> str:
    """Отпечаток звука рассказчика не зависит от сдвигов таймлайна других ролей."""
    return _narrator_snapshot(db, chapter_id)["fingerprint"]


def _preflight_sources(sources: dict, source_paths: dict) -> None:
    from app.services import audio_integrity, nas_health

    if any(sources[key]["location"] == "nas" for key in source_paths):
        if not nas_health.reachable_now():
            raise AsrAuditError("narrator_storage_unavailable")
    # Проверяем весь набор до первого списания: иначе целый исходник уже оплачен,
    # а следующий отсутствующий фикс делает проверку главы незавершимой.
    for key, path in source_paths.items():
        item = sources[key]
        if item.get("verify_state") in {audio_integrity.MISSING, audio_integrity.MISMATCH}:
            raise AsrAuditError(f"narrator_source_integrity_failed:{key}")
        try:
            size = os.path.getsize(path)
            if size <= 0 or (item["size_bytes"] and size != int(item["size_bytes"])):
                raise AsrAuditError(f"narrator_source_size_mismatch:{key}")
            with open(path, "rb") as handle:
                handle.read(1)
            if item.get("md5") and audio_integrity.file_digest(path) != item["md5"]:
                raise AsrAuditError(f"narrator_source_checksum_mismatch:{key}")
        except OSError as exc:
            raise AsrAuditError(f"narrator_source_unreadable:{key}") from exc


def _assembled_transcript(snapshot: dict, transcripts: dict) -> tuple[dict, dict]:
    from app.services.asr_align import word_stream

    segments, clock = [], 0.0
    by_index = {}
    for clip in snapshot["clips"]:
        window = {**clip, "audit_start": clock}
        by_index.setdefault(clip["index"], []).append(window)
        source = transcripts.get(clip["source_audio_file_id"], {})
        words = [{"word": word, "start": clock + max(0., start - clip["start"]),
                  "end": clock + min(clip["end"], end) - clip["start"]}
                 for word, start, end in word_stream(source.get("segments") or [])
                 if start >= clip["start"] and end <= clip["end"]]
        duration = max(0., clip["end"] - clip["start"])
        segments.append({"text": " ".join(w["word"] for w in words), "start": clock,
                         "end": clock + duration, "words": words})
        clock += duration + .35
    alignment = {"lines": []}
    for item in snapshot["lines"]["required"]:
        windows = by_index.get(item["index"], [])
        alignment["lines"].append({"index": item["index"], "matched": bool(windows),
            "start": windows[0]["audit_start"] if windows else None,
            "end": windows[-1]["audit_start"] + windows[-1]["end"] - windows[-1]["start"] if windows else None,
            "windows": windows})
    return {"text": " ".join(s["text"] for s in segments), "segments": segments,
            "sources": transcripts}, alignment


def _attach_source_windows(flags: list, alignment: dict) -> None:
    for flag in flags:
        line = next(r for r in alignment["lines"] if r["index"] == flag["index"])
        windows = line.get("windows") or []
        flag["source_windows"] = windows
        flag["source_audio_file_id"] = windows[0]["source_audio_file_id"] if windows else ""
        mapped_anchors = []
        for anchor in flag.get("word_anchors") or []:
            window = next((w for w in windows if w["audit_start"] <= anchor["start"] <=
                           w["audit_start"] + w["end"] - w["start"]), None)
            if window:
                anchor["source_audio_file_id"] = window["source_audio_file_id"]
                anchor["start"] += window["start"] - window["audit_start"]
                anchor["end"] = min(window["end"], anchor["end"] + window["start"] - window["audit_start"])
                mapped_anchors.append(anchor)
        flag["word_anchors"] = mapped_anchors
        # Позиции пропусков приблизительны; таймкод исходника нельзя путать с
        # координатой проекта или выдавать отсутствующему слову точное время.
        if windows:
            flag["anchor"] = {"start": windows[0]["start"], "end": windows[0]["end"], "approximate": True,
                              "source_audio_file_id": windows[0]["source_audio_file_id"]}
        else:
            flag["anchor"] = None


def _transcribe_fresh(path: str, *, model: str, phrases: list[str]) -> "tuple[dict, float | None]":
    """Свежий платный прогон аудит-моделью. Своя обёртка, а не `_transcribe_with_service`:
    нужен `usage.cost` ответа для цены прогона в отчёте владельцу."""
    from app.services.asr_run import OPENAI_BASE_URL
    from app.services.provider_keys import provider_key
    from app.services import spend
    from app.services.audio_probe import probe_audio_file

    descriptor, compressed = tempfile.mkstemp(prefix="asr-audit-", suffix=".mp3")
    os.close(descriptor)
    try:
        compress_for_asr(path, compressed)
        seconds = float(probe_audio_file(compressed).get("duration_seconds") or 0)
        if not math.isfinite(seconds) or seconds < 0:
            seconds = 0
        try:
            payload = transcribe_file(
                compressed,
                api_key=provider_key("routerai"),
                base_url=settings.routerai_base_url or OPENAI_BASE_URL,
                model=model,
                language=settings.asr_language,
                prompt=settings.asr_prompt or ", ".join((phrases or [])[:80]),
            )
        except Exception:
            spend.record_call("routerai", model, unit="seconds", input_units=math.ceil(seconds), estimated=True)
            raise
        cost = _usage_cost_rub(payload.get("usage") or {})
        spend.record_call("routerai", model, unit="seconds", input_units=math.ceil(seconds),
                          estimated=cost is None, actual_rub=cost)
    finally:
        if os.path.exists(compressed):
            os.remove(compressed)
    usage = payload.get("usage") or {}
    cost = _usage_cost_rub(usage)
    return payload, cost


def _cut_window(source: str, start: float, end: float, target: str, bounds: dict | None = None) -> str:
    """Вырез окна из исходного файла для подтверждения находки второй моделью."""
    import subprocess

    pad = float(CONFIRM_PAD_SECONDS)
    at = max(0.0, float(start) - pad)
    until = float(end) + pad
    if bounds:
        at = max(at, float(bounds["start"]))
        until = min(until, float(bounds["end"]))
    duration = max(.01, until - at)
    result = subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-ss", f"{at:.2f}", "-t", f"{duration:.2f}",
         "-i", str(source), "-ac", "1", "-ar", "16000", "-b:a", "32k", str(target)],
        capture_output=True, timeout=600, check=False,
    )
    if result.returncode != 0 or not os.path.isfile(target) or os.path.getsize(target) == 0:
        raise AsrAuditError("window_cut_failed")
    return str(target)


def default_judge_ask(system: str, user: str, schema: dict, *, model: str | None = None) -> dict:
    """Тот же путь к модели, что у консилиума: RouterAI + строгий JSON."""
    from app.pipeline.llm_client import _resolve_provider, call_chat

    api_key, base_url, mode = _resolve_provider("routerai")
    if not api_key:
        raise RuntimeError("ROUTERAI_API_KEY не задан")
    out = call_chat(base_url, api_key, model or settings.llm_audit_model, system, user,
                    mode=mode, force_json=True, json_schema=schema)
    content = (out or {}).get("content") if isinstance(out, dict) else out
    if isinstance(content, str):
        content = json.loads(content.strip() or "{}")
    return content or {}


_VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "status": {"type": "string", "enum": ["ok", "missing", "partial"]},
                    "missing_words": {"type": "array", "items": {"type": "string"}},
                    "note": {"type": "string"},
                },
                "required": ["index", "status", "missing_words", "note"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["verdicts"],
    "additionalProperties": False,
}

_CONFIRM_SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "confirmed": {"type": "boolean"},
                    "note": {"type": "string"},
                },
                "required": ["index", "confirmed", "note"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["results"],
    "additionalProperties": False,
}

_JUDGE_SYSTEM = """Ты — редактор аудиокниги. Проверяешь, дословно ли рассказчик прочитал строки сценария. Тебе дают строки сценария и фрагменты расшифровки записи, найденные по таймкодам.

Правила:
- Перестановка слов допустима — не отмечать.
- Звуки без слов (смешки, кряхтения, вздохи) в расшифровке помечены тегами вроде [смеется] — они всегда допустимы.
- Имена сверяй с глоссарием: любое фонетически близкое написание допустимо («Кысяти» = «Савита», «Мюсвпынюмрэям» = «Мюсвынюмрэмм»). Пропуск имени недопустим.
- Каждое слово с содержанием строки должно прозвучать в той же лексеме и той же форме. Пропуск слова, замена синонимом, другая грамматическая форма («получала» вместо «получила» и наоборот) — отметка partial с этими словами в missing_words.
- «missing» — только если фрагмента записи нет или в нём нет текста строки совсем.
- Если у строки несколько подходов, достаточно одного полностью прочитанного подхода. Слова из разных неполных подходов не склеивай в воображаемую полную реплику.
Вердикт строго по схеме, по каждой строке из запроса."""

_CONFIRM_SYSTEM = """Ты — редактор аудиокниги. По свежей расшифровке проблемного места записи решаешь, подтверждается ли ранее отмеченный пропуск. Перестановка слов допустима, имена допустимы любым фонетически близким написанием, звуки без слов ([смеется], вздохи) допустимы. Слова из missing_words в той же форме присутствуют — пропуск НЕ подтверждён. Отвечай строго по схеме."""


def _quote(text: str, limit: int = 120) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def _judge_user_prompt(required: list[dict], excerpts: dict[int, str], glossary: list[str]) -> str:
    lines_out = [f"Глоссарий имён: {', '.join(glossary[:80])}" if glossary else "Глоссарий имён: —", "", "Строки сценария:"]
    for item in required:
        lines_out.append(f"[{item['index']}] {item['text']}")
    lines_out += ["", "Расшифровка записи по строкам:"]
    for item in required:
        excerpt = excerpts.get(item["index"])
        lines_out.append(f"[{item['index']}] {excerpt if excerpt else '<не найдена в записи>'}")
    return "\n".join(lines_out)


def _valid_cost(value) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        cost = float(value)
    except (TypeError, ValueError):
        return None
    return cost if math.isfinite(cost) and cost >= 0 else None


def _usage_cost_rub(usage) -> float | None:
    # Поле cost само по себе не задаёт валюту: нельзя подписывать доллары рублями.
    if not isinstance(usage, dict) or str(usage.get("currency") or usage.get("cost_currency") or "").upper() != "RUB":
        return None
    return _valid_cost(usage.get("cost"))


def _transcription_result(result) -> tuple[dict, float | None]:
    if isinstance(result, tuple) and len(result) == 2:
        payload, cost = result
    else:
        payload = result
        cost = _usage_cost_rub(payload.get("usage")) if isinstance(payload, dict) else None
    if not isinstance(payload, dict):
        raise AsrAuditError("invalid_transcription_response")
    return payload, _valid_cost(cost)


def _verdicts(answer, key: str, expected: set[int], *, confirmation=False) -> dict[int, dict]:
    # Строгая схема провайдера не заменяет проверку: неполный ответ не означает,
    # что пропусков нет, а чужой индекс не подтверждает нашу строку.
    if not isinstance(answer, dict) or not isinstance(answer.get(key), list):
        raise AsrAuditError("invalid_judge_response")
    by_index = {}
    for verdict in answer[key]:
        if not isinstance(verdict, dict) or type(verdict.get("index")) is not int:
            raise AsrAuditError("invalid_judge_index")
        index = verdict["index"]
        if index not in expected or index in by_index:
            raise AsrAuditError("uncorrelated_judge_response")
        if not isinstance(verdict.get("note"), str):
            raise AsrAuditError("invalid_judge_note")
        if confirmation:
            if type(verdict.get("confirmed")) is not bool:
                raise AsrAuditError("invalid_confirmation")
        elif (verdict.get("status") not in {"ok", "missing", "partial"}
              or not isinstance(verdict.get("missing_words"), list)
              or any(not isinstance(w, str) or not w.strip() for w in verdict["missing_words"])):
            raise AsrAuditError("invalid_judge_verdict")
        by_index[index] = verdict
    if set(by_index) != expected:
        raise AsrAuditError("incomplete_judge_response")
    return by_index


def _build_excerpts(alignment_lines: list[dict], stream: list[tuple[str, float, float]]) -> dict[int, str]:
    out = {}
    alignment = {"lines": alignment_lines}
    for line in alignment_lines:
        windows = line.get("windows")
        if windows:
            out[line["index"]] = "\n".join(
                f"Подход {number}: " + " ".join(word for word, start, end in stream
                    if start >= window["audit_start"] and end <= window["audit_start"] + window["end"] - window["start"])
                for number, window in enumerate(windows, 1))
            continue
        anchor = _anchor_of(alignment, line["index"], stream=stream)
        if anchor:
            out[line["index"]] = " ".join(w for w, start, end in stream
                if end >= anchor["start"] - 2 and start <= anchor["end"] + 2)
    return out


def _anchor_of(alignment: dict, index: int, *, stream=None) -> dict | None:
    lines = alignment.get("lines") or []
    position = next((i for i, line in enumerate(lines) if line.get("index") == index), None)
    if position is None:
        return None
    line = lines[position]
    if line.get("matched") and line.get("start") is not None and line.get("end") is not None:
        return {"start": float(line["start"]), "end": float(line["end"])}
    before = next((r for r in reversed(lines[:position]) if r.get("matched")), None)
    after = next((r for r in lines[position + 1:] if r.get("matched")), None)
    if before is None and after is None:
        return None
    start = float(before["end"]) if before else 0.0
    end = float(after["start"]) if after else max((e for _, _, e in (stream or [])), default=start)
    return {"start": start, "end": max(start, end), "approximate": True}


def _word_anchors(text: str, missing_words: list[str], anchor: dict | None, stream: list) -> list[dict]:
    from app.services.asr_align import normalize_words

    if not anchor or not missing_words:
        return []
    heard = [(token, start, end) for word, start, end in stream
             if end >= anchor["start"] and start <= anchor["end"]
             for token in normalize_words(word)]
    expected = normalize_words(text)
    matching = SequenceMatcher(None, expected, [w for w, _, _ in heard], autojunk=False)
    positions = {i + step: j + step for i, j, size in matching.get_matching_blocks() for step in range(size)}
    missing = {word for phrase in missing_words for word in normalize_words(phrase)}
    output = []
    for i, word in enumerate(expected):
        if word not in missing:
            continue
        before = next((positions[j] for j in range(i - 1, -1, -1) if j in positions), None)
        after = next((positions[j] for j in range(i + 1, len(expected)) if j in positions), None)
        if before is None and after is None:
            continue
        start = heard[before][2] if before is not None else heard[after][1]
        end = heard[after][1] if after is not None else start
        output.append({"word": word, "start": start, "end": max(start, end), "approximate": True})
    return output


def _judge_required(*, required: list[dict], alignment: dict, heard: dict,
                    glossary: list[str], judge) -> list[dict]:
    from app.services.asr_align import word_stream

    stream = word_stream(heard.get("segments") or [])
    excerpts = _build_excerpts(alignment.get("lines") or [], stream)
    flags = []
    for offset in range(0, len(required), JUDGE_BATCH):
        batch = required[offset:offset + JUDGE_BATCH]
        answer = judge(_JUDGE_SYSTEM, _judge_user_prompt(batch, excerpts, glossary), _VERDICT_SCHEMA)
        by_index = _verdicts(answer, "verdicts", {item["index"] for item in batch})
        for item in batch:
            verdict = by_index[item["index"]]
            aligned = next((line for line in alignment.get("lines") or []
                            if line.get("index") == item["index"]), {})
            if aligned.get("windows") == []:
                # Нейросеть не может оправдать строку, для которой в проекте нет
                # клипа: соседнее похожее предложение не звучит вместо неё.
                verdict = {"status": "missing", "missing_words": [],
                           "note": "обязательная строка не размещена в собранной главе"}
            if verdict["status"] == "ok":
                continue
            anchor = _anchor_of(alignment, item["index"], stream=stream)
            flags.append({**item, "status": verdict["status"],
                          "missing_words": verdict["missing_words"], "note": verdict["note"],
                          "anchor": anchor,
                          "word_anchors": _word_anchors(item["text"], verdict["missing_words"], anchor, stream),
                          "confirmed": False, "confirm_note": ""})
    return flags


def _confirm_flags(*, flags: list[dict], source_path: str = "", judge,
                   confirm_transcribe, record_cost=None, source_paths=None) -> list[dict]:
    from app.services.asr_align import normalize_words

    confirmed = []
    for flag in flags:
        windows = flag.get("source_windows")
        if windows is None:
            windows = [flag["anchor"]] if flag.get("anchor") else []
        if not windows:
            flag["confirm_note"] = "строка не размещена в собранной главе"
            continue
        all_confirmed = True
        notes = []
        for number, anchor in enumerate(windows, 1):
            descriptor, window = tempfile.mkstemp(prefix="asr-audit-win-", suffix=".mp3")
            os.close(descriptor)
            billed = False
            try:
                path = (source_paths or {}).get(anchor.get("source_audio_file_id"), source_path)
                # Подтверждаем только звук, который вошёл в проект: соседний фикс
                # вне клипа не может оправдать обрезанный при сборке пропуск.
                _cut_window(path, anchor["start"], anchor["end"], window,
                            anchor if flag.get("source_windows") else None)
                billed = True
                fresh, cost = _transcription_result(confirm_transcribe(window))
                if record_cost:
                    record_cost(cost)
                billed = False
                transcript = str(fresh.get("text") or "").strip()
                spoken_words = normalize_words(transcript)
                if not spoken_words:
                    raise AsrAuditError("empty_confirmation_transcript")
                answer = judge(
                    _CONFIRM_SYSTEM,
                    f"Индекс строки: {flag['index']}\nСтрока сценария: {flag['text']}\n"
                    f"Ранее отмечено как пропуск: {flag['status']}\n"
                    f"Слова: {', '.join(flag['missing_words'])}\nСвежая расшифровка места: {transcript}",
                    _CONFIRM_SCHEMA,
                )
                verdict = _verdicts(answer, "results", {flag["index"]}, confirmation=True)[flag["index"]]
                suspect_words = normalize_words(" ".join(flag["missing_words"]) or flag["text"])
                words_present = bool(suspect_words) and not (set(suspect_words) - set(spoken_words))
                accepted = verdict["confirmed"] and not words_present
                notes.append(f"Подход {number}: " + ("спорные слова присутствуют во второй расшифровке" if words_present else verdict["note"]))
                all_confirmed = all_confirmed and accepted
                if not accepted:
                    break
            except Exception as exc:
                if billed and record_cost:
                    record_cost(None)
                logger.warning("Подтверждение флага %s не вышло: %s", flag["index"], exc)
                notes.append(f"подтверждение не завершено: {exc}"[:200])
                all_confirmed = False
                break
            finally:
                if os.path.exists(window):
                    os.remove(window)
        flag["confirmed"] = all_confirmed
        flag["confirm_note"] = "; ".join(notes)
        if all_confirmed:
            confirmed.append(flag)
    return confirmed


def run_asr_audit(db, chapter_id: str, *, transcribe=None, judge=None,
                  confirm_transcribe=None, expected_narrator_fingerprint: str = "") -> "object":
    """Снимок входа и результат фиксируются короткими транзакциями.

    Платные вызовы могут идти минуты: незакрытый flush на SQLite блокировал бы
    загрузки дикторов и heartbeat воркера всё это время.
    """
    from app.models import AsrAudit, ScriptBook, ScriptChapter
    from app.services import audio_storage

    chapter_row = db.get(ScriptChapter, str(chapter_id or "").strip())
    if chapter_row is None:
        raise AsrAuditError("chapter_not_found")
    chapter = SimpleNamespace(id=str(chapter_row.id), book_id=str(chapter_row.book_id),
                              chapter_title=str(chapter_row.chapter_title or ""))
    try:
        snapshot = _narrator_snapshot(db, chapter.id)
        snapshot_error = None
    except Exception as exc:
        snapshot = None
        snapshot_error = exc
    audit = AsrAudit(chapter_id=chapter.id, status="running", model=settings.asr_audit_model,
                     confirm_model=settings.asr_audit_confirm_model, judge_model=settings.llm_audit_model)
    db.add(audit)
    if (expected_narrator_fingerprint and snapshot
            and snapshot["fingerprint"] != expected_narrator_fingerprint):
        audit.status = "skipped"
        audit.error_message = "narrator_input_changed"
        db.commit()
        return audit
    take_row = latest_narrator_take(db, chapter_row)
    if take_row is None and not (snapshot or {}).get("sources"):
        audit.status = "skipped"
        audit.error_message = "no_narrator_take"
        db.commit()
        return audit
    if snapshot and snapshot["sources"]:
        chosen = max(snapshot["sources"].values(), key=lambda row: str(row["uploaded_at"] or ""))
        take = SimpleNamespace(**chosen)
    else:
        take = SimpleNamespace(**{key: getattr(take_row, key) for key in
            ("id", "uploaded_at", "stored_key", "location", "actor_name", "role")})
    audit.audio_file_id = str(take.id)
    model, confirm_model, judge_model = audit.model, audit.confirm_model, audit.judge_model
    book_row = db.get(ScriptBook, chapter.book_id)
    book = SimpleNamespace(title=str(getattr(book_row, "title", "") or ""))
    lines = {"required": [], "tags": [], "titles": []}
    flags, confirmed = [], []
    meta = {"take_id": str(take.id), "take_uploaded_at": str(take.uploaded_at or ""),
            "classified_at": iso_utc(utcnow_naive()), "cost_complete": True,
            "cost_scope": "stt_only", "stt_calls": 0}
    costs = []

    def record_cost(cost):
        meta["stt_calls"] += 1
        if cost is None:
            meta["cost_complete"] = False
        else:
            costs.append(cost)

    try:
        if snapshot_error:
            raise snapshot_error
        lines = snapshot["lines"]
        meta["narrator_input_fingerprint"] = snapshot["fingerprint"]
        meta["source_audio_file_ids"] = list(snapshot["sources"])
        meta["counts"] = {kind: len(items) for kind, items in lines.items()}
        meta["lines"] = lines
        audit.lines_json = json.dumps(meta, ensure_ascii=False)
        glossary = book_phrases(db, chapter.book_id)
        db.commit()
        if not lines["required"]:
            raise AsrAuditError("no_required_lines")
        if not snapshot["clips"]:
            raise AsrAuditError("no_assembled_narrator_clips")
        required_indexes = {item["index"] for item in lines["required"]}
        required_sources = {clip["source_audio_file_id"] for clip in snapshot["clips"]
                            if clip["index"] in required_indexes}
        source_paths = {key: audio_storage.resolve_path(str(item["stored_key"] or ""),
                       location=str(item["location"] or "local")) for key, item in snapshot["sources"].items()
                       if key in required_sources}
        _preflight_sources(snapshot["sources"], source_paths)
        # Ошибки шлюза и таймауты не доказывают, что провайдер не списал деньги.
        transcribe_call = transcribe or (lambda path: _transcribe_retrying(
            lambda: _transcribe_fresh(path, model=model, phrases=glossary), retry_errors={"asr_http_429"}))
        transcripts = {}
        for source_id, source in source_paths.items():
            try:
                payload, cost = _transcription_result(transcribe_call(source))
            except Exception:
                record_cost(None)
                raise
            record_cost(cost)
            transcripts[source_id] = payload
            audit.transcript_json = json.dumps({"sources": transcripts}, ensure_ascii=False)
            audit.stt_cost_rub = sum(costs)
            audit.lines_json = json.dumps(meta, ensure_ascii=False)
            db.commit()
            from app.services.asr_align import word_stream
            if not word_stream(payload.get("segments") or []):
                raise AsrAuditError("audit_transcript_has_no_word_timestamps")
        heard, alignment = _assembled_transcript(snapshot, transcripts)
        audit.transcript_json = json.dumps(heard, ensure_ascii=False)
        db.commit()
        judge_call = judge or (lambda system, user, schema: default_judge_ask(system, user, schema, model=judge_model))
        flags = _judge_required(required=lines["required"], alignment=alignment, heard=heard,
                                glossary=glossary, judge=judge_call)
        _attach_source_windows(flags, alignment)
        confirm_call = confirm_transcribe or (lambda path: _transcribe_retrying(
            lambda: _transcribe_fresh(path, model=confirm_model, phrases=glossary), retry_errors={"asr_http_429"}))
        confirmed = _confirm_flags(flags=flags, source_paths=source_paths, judge=judge_call,
                                   confirm_transcribe=confirm_call, record_cost=record_cost)
        # Пока модели слушали, редактор мог заменить фикс или текст. Оплаченное
        # сохраняем, но старые выводы не должны обвинить уже исправившегося диктора.
        db.expire_all()
        current_fingerprint = narrator_input_fingerprint(db, chapter.id)
        db.commit()
        audit.status = "done" if current_fingerprint == snapshot["fingerprint"] else "skipped"
        if audit.status == "skipped":
            audit.error_message = "narrator_input_changed_after_payment"
    except Exception as exc:
        audit.status = "failed"
        audit.error_message = str(exc)[:255]
        logger.warning("Аудит главы %s не завершён: %s", chapter.id, exc)
    completed = audit.status == "done"
    audit.stt_cost_rub = sum(costs)
    audit.lines_json = json.dumps(meta, ensure_ascii=False)
    audit.findings_json = json.dumps({"flags": flags, "confirmed": len(confirmed)}, ensure_ascii=False)
    db.commit()
    if completed:
        try:
            from app.services.chapter_delivery import mark_session_outdated
            # Чистый повтор тоже меняет проект: старые подтверждённые метки
            # должны исчезнуть, иначе архив продолжит обвинять уже оправданную строку.
            mark_session_outdated(db, chapter.id)
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("Не отмечена устаревшая сессия главы %s", chapter.id)
    # Снимок не открывает новую read-транзакцию до сетевой отправки.
    notice_audit = SimpleNamespace(status=audit.status, error_message=audit.error_message,
                                  stt_cost_rub=audit.stt_cost_rub, lines_json=audit.lines_json)
    db.commit()
    try:
        _notify_owner(db, chapter=chapter, book=book, audit=notice_audit, lines=lines,
                      flags=flags, confirmed=confirmed)
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Не отправлен отчёт владельцу по главе %s", chapter.id)
    if confirmed and completed:
        recipients = {}
        for flag in confirmed:
            source_ids = {w["source_audio_file_id"] for w in flag.get("source_windows") or []}
            for source_id in source_ids:
                person = snapshot["sources"][source_id]
                actor = str(person["actor_name"] or "")
                entry = recipients.setdefault(actor, {"take": SimpleNamespace(**person), "flags": []})
                if flag not in entry["flags"]:
                    entry["flags"].append(flag)
        for entry in recipients.values():
            try:
                entry["take"].role = "Рассказчик"
                _notify_narrator(db, chapter=chapter, book=book, take=entry["take"], confirmed=entry["flags"])
                db.commit()
            except Exception:
                db.rollback()
                logger.exception("Не отправлен отчёт рассказчику по главе %s", chapter.id)
    return audit


def _audit_findings(audit) -> "tuple[list[dict], int]":
    try:
        payload = json.loads(audit.findings_json or "{}") or {}
    except ValueError:
        return [], 0
    return list(payload.get("flags") or []), int(payload.get("confirmed") or 0)


def _chapter_link(book_id: str, chapter_id: str) -> str:
    from urllib.parse import quote

    base = (settings.app_base_url or "").strip().rstrip("/")
    if not base:
        return ""
    return f"{base}/app/recording?book_id={quote(str(book_id))}&chapter_id={quote(str(chapter_id))}"


def _notify_owner(db, *, chapter, book, audit, lines: dict, flags: list[dict],
                  confirmed: list[dict]) -> None:
    """Отчёт владельцу — всегда: чистый результат тоже новость, он закрывает контур."""
    from app.services.telegram import send_telegram_message

    header = f"🔍 Строгая проверка рассказчика: {getattr(book, 'title', '') or ''} · {chapter.chapter_title}".strip(" ·")
    try:
        meta = json.loads(audit.lines_json or "{}")
    except (TypeError, ValueError):
        meta = {}
    price = f"{audit.stt_cost_rub:.2f} ₽" if meta.get("cost_complete", True) else "не подтверждена"
    counts = (f"Проверено строк: {len(lines['required'])} · отклонений: {len(flags)} "
              f"(подтверждено {len(confirmed)}) · цена STT {price} (без LLM)")
    if audit.status == "failed":
        counts = f"Проверка не завершена: {audit.error_message} · цена STT {price} (без LLM)"
    elif audit.status == "skipped":
        counts = f"Результат устарел: состав рассказчика изменился во время проверки · цена STT {price} (без LLM)"
    if not flags:
        text = f"{header}\n{counts}"
    else:
        rows = []
        for flag in flags:
            mark = "подтверждено" if flag.get("confirmed") else "нет подтверждения"
            words = ", ".join(flag.get("missing_words") or []) or flag.get("status") or ""
            rows.append(f"• ({mark}) [{words}] {_quote(flag.get('text'), 90)}")
        text = "\n".join([header, counts, "", *rows])
    if confirmed and audit.status == "done":
        text += "\n\nМаркеры будут в новой сборке главы. Уже скачанный проект не изменится — скачайте архив заново."
    link = _chapter_link(str(chapter.book_id), str(chapter.id))
    if link:
        text += f"\n\n{link}"
    # Без `direct`: адресат подменяется владельцем — ровно то, что нужно отчёту.
    # Telegram отвергает длинное сообщение целиком; владельцу нужны все находки,
    # поэтому дробим отчёт, а не скрываем хвост после шестой строки.
    remaining = text
    while remaining:
        boundary = min(len(remaining), 3500)
        if boundary < len(remaining):
            boundary = remaining.rfind("\n", 0, boundary) or boundary
        send_telegram_message(db, remaining[:boundary])
        remaining = remaining[boundary:].lstrip("\n")


def _notify_narrator(db, *, chapter, book, take, confirmed: list[dict]) -> dict | None:
    """Письмо рассказчику только о подтверждённых пропусках.

    Неподтверждённый флаг — это пока обвинение без второго свидетеля; диктору о нём
    не пишут, смотрит владелец. Письмо своё (`audit_findings_notice`), а не боевое
    «не нашлось реплик»: та — про целые реплики, эта — про дословность чтения.
    """
    from app.services import asr_notice

    return asr_notice.notify_audit_findings(
        db,
        actor_name=str(take.actor_name or ""),
        role=str(take.role or ""),
        chapter=str(chapter.chapter_title or ""),
        book_title=str(getattr(book, "title", "") or ""),
        book_id=str(chapter.book_id),
        texts=[item["text"] for item in confirmed],
    )


def latest_audits(db, chapter_ids: list[str]) -> "dict[str, dict]":
    """Последний прогон аудита на каждую главу — для бейджа на экране `/asr`."""
    from app.models import AsrAudit, BackgroundRun

    ids = [str(cid) for cid in (chapter_ids or []) if str(cid or "").strip()]
    if not ids:
        return {}
    rows = (
        db.query(AsrAudit)
        .filter(AsrAudit.chapter_id.in_(ids))
        .order_by(AsrAudit.created_at.asc())
        .all()
    )
    latest: dict[str, object] = {}
    for row in rows:
        latest[str(row.chapter_id)] = row
    out: dict[str, dict] = {}
    for cid, row in latest.items():
        flags, confirmed = _audit_findings(row)
        try:
            metadata = json.loads(row.lines_json or "{}")
            cost_complete = metadata.get("cost_complete", True)
        except (ValueError, AttributeError):
            metadata = {}
            cost_complete = False
        audit_status = str(row.status or "")
        if audit_status == "done":
            try:
                saved = str(metadata.get("narrator_input_fingerprint") or "")
                if not saved or saved != narrator_input_fingerprint(db, cid):
                    audit_status = "stale"
            except Exception:
                # Нельзя выдавать прошлые находки за текущие, если сравнение входа
                # не удалось: редактор мог изменить сценарий или исходный дубль.
                logger.debug("Не удалось проверить актуальность аудита главы %s", cid, exc_info=True)
                audit_status = "stale"
        out[cid] = {
            "status": audit_status,
            "flags": len(flags),
            "confirmed": confirmed,
            "model": str(row.model or ""),
            "stt_cost_rub": round(float(row.stt_cost_rub or 0.0), 2) if cost_complete else None,
            "updated_at": iso_utc(row.updated_at) or "",
            "error_message": str(row.error_message or ""),
        }
    runs = db.query(BackgroundRun).filter(BackgroundRun.job_kind == "asr_audit",
        BackgroundRun.entity_id.in_(ids)).order_by(BackgroundRun.created_at.asc()).all()
    latest_runs = {str(run.entity_id): run for run in runs}
    for cid, run in latest_runs.items():
        previous = latest.get(cid)
        if previous is not None and previous.created_at >= run.created_at:
            continue
        error = str(run.error_message or "")
        status = "unknown" if error.startswith("queue_outcome_unknown") else str(run.status)
        if status == "done":
            status = "unknown"
            error = "Результат проверки не сохранён; требуется проверка владельцем"
        out[cid] = {"status": status, "flags": 0, "confirmed": 0, "model": "",
                    "stt_cost_rub": None, "updated_at": iso_utc(run.updated_at) or "",
                    "error_message": error}
    return out


def needs_run(db, chapter_id: str) -> bool:
    from app.models import AsrAudit

    last = db.query(AsrAudit).filter(AsrAudit.chapter_id == str(chapter_id)).order_by(AsrAudit.created_at.desc()).first()
    if last is None or last.status == "failed":
        return True
    try:
        meta = json.loads(last.lines_json or "{}")
        return meta.get("narrator_input_fingerprint") != narrator_input_fingerprint(db, chapter_id)
    except (ValueError, AsrAuditError):
        return True


def enqueue_asr_audit_if_due(db, chapter_id: str) -> str | None:
    """Платный прогон допускается только по зафиксированной полной сборке."""
    return enqueue_asr_audit(chapter_id)


def enqueue_asr_audit(chapter_id: str, *, force: bool = False) -> str | None:
    from app.services.asr_audit_lifecycle import enqueue_audit

    return enqueue_audit(chapter_id, force=force)

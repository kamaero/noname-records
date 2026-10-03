"""Один отчёт владельцу на собранную главу — вместо письма на каждый дубль.

Диктору о пропуске сообщают сразу (`asr_notice`), пока он ещё у микрофона.
Владельцу это неинтересно на каждый дубль: он смотрит на главу целиком, когда
она собралась. Здесь — сбор картины по уже посчитанным `AsrJob` главы и текст,
который из неё получится.

Сбор ничего не пересчитывает: расшифровка и сверка уже прошли при приёме дубля
(`asr_run.run_asr_for_take`), здесь только читается `alignment_json`.
"""
from __future__ import annotations

import json
from urllib.parse import quote

from app.config import settings
from app.services.asr_notice import LINES_SHOWN, QUOTE_CHARS
from app.services.asr_run import narration_only_lines
from app.v2.cast_ops import chapter_role_counts

#: Выше этого совпадение считается уверенным. Выбрано по живым данным «Крыльев
#: полумрака»: из 273 совпавших реплик 94% сходятся на 0.90 и выше, а ниже 0.62
#: нет ничего. Полоса от порога совпадения (0.62, откуда берётся из
#: asr_align.MATCH_THRESHOLD) до 0.75 отмечает около 3% — восемь реплик на
#: двадцать три дубля. Это не «неизвестно, кто говорит» (таких меток в книге не
#: осталось), а «прочитано, но разошлось с текстом».
WEAK_MATCH_CEILING = 0.75


def _chapter_link(book_id: str, chapter_id: str) -> str:
    """Ссылка на запись именно этой главы.

    `chapter_id` — необязательный параметр экрана `/app/recording`: он берёт его
    для автовыбора главы (см. `frontend/src/pages/RecordingPage.tsx`). Без него
    ссылка открывала бы книгу, а не главу, о которой этот отчёт.
    """
    base = (settings.app_base_url or "").strip().rstrip("/")
    if not base or not str(book_id or "").strip():
        return ""
    link = f"{base}/app/recording?book_id={quote(str(book_id))}"
    chapter_id = str(chapter_id or "").strip()
    if chapter_id:
        link += f"&chapter_id={quote(chapter_id)}"
    return link


def build_chapter_report(db, chapter_id: str, *, lost_takes: list[dict] | None = None) -> dict:
    """Собрать картину по главе: что не нашлось, что легло плохо, что не так в разметке.

    Разбор идёт по строкам `AsrJob` этой главы — расшифровка и сверка там уже
    посчитаны при приёме дубля. Задание со статусом `done` разбирается по
    `alignment_json`; со статусом `failed` — само по себе сигнал: расшифровка не
    состоялась, и роль не проверена, даже если файл на месте. Такое задание не
    должно молча выпадать из отчёта — иначе глава с одним упавшим распознаванием
    выглядела бы «всё на месте», а её никто не слушал. Задания в очереди
    (`queued`, `running`) в отчёт не попадают: по ним пока ничего не известно.

    `lost_takes` — дубли, упавшие ещё до того, как строка `AsrJob` появилась
    (`audio_not_found`, `not_a_take`, `chapter_not_found`). Второй путь к тому же
    молчанию: строки нет, значит по базе такой дубль неотличим от нераспознанного
    вовсе, а число ролей с записью считается по файлам — файл есть, роль
    «записана», разделов нет, «всё на месте». Знает о них только прогон
    (`asr_run.run_asr_for_chapter`), поэтому они приходят сюда списком, а не
    вычитываются заново.

    Роли внутри каждого раздела идут в порядке имени роли — не порядка строк в
    базе, — чтобы всё про одного актёра стояло рядом.
    """
    from app.models import AsrJob, ScriptBook, ScriptChapter
    from app.services.audio_integrity import chapter_files

    chapter_id = str(chapter_id or "").strip()
    chapter = db.get(ScriptChapter, chapter_id)
    book = db.get(ScriptBook, str(chapter.book_id)) if chapter is not None else None

    roles = chapter_role_counts(db, chapter_id)
    takes = chapter_files(db, chapter_id) if chapter is not None else []
    recorded_roles = {str(item.role or "").strip() for item in takes if str(item.role or "").strip()}

    missing_by_role: dict[str, list[str]] = {}
    weak: list[dict] = []
    failed: list[dict] = []
    jobs = (
        db.query(AsrJob)
        .filter(AsrJob.chapter_id == chapter_id)
        .order_by(AsrJob.expected_role.asc(), AsrJob.created_at.asc())
        .all()
    )
    for job in jobs:
        role = str(job.expected_role or "").strip()
        if job.status == "failed":
            failed.append({"role": role, "error": str(job.error_message or "").strip()})
            continue
        if job.status != "done":
            continue
        payload = json.loads(job.alignment_json or "{}")
        for line in payload.get("lines") or []:
            text = str(line.get("text") or "").strip()
            if not text:
                continue
            if not line.get("matched"):
                missing_by_role.setdefault(role, []).append(text)
            elif float(line.get("score") or 0.0) < WEAK_MATCH_CEILING:
                weak.append({"role": role, "text": text, "score": float(line.get("score") or 0.0)})

    # Оба пути ведут к одному: роль не проверена. Разделять их в письме владельцу
    # нечем — причина у обоих ложится в ту же строку.
    for item in lost_takes or []:
        failed.append({
            "role": str(item.get("role") or "").strip(),
            "error": str(item.get("error") or "").strip(),
        })

    markup: list[dict] = []
    for role in sorted(roles):
        for text in narration_only_lines(db, chapter_id, role):
            markup.append({"role": role, "text": text})

    return {
        "chapter": str(getattr(chapter, "chapter_title", "") or ""),
        "book_title": str(getattr(book, "title", "") or ""),
        "roles": len(roles),
        "recorded_roles": len(recorded_roles),
        "takes": len(takes),
        "missing": [{"role": role, "lines": lines} for role, lines in missing_by_role.items()],
        "weak": weak,
        "failed": failed,
        "markup": markup,
        "link": _chapter_link(str(getattr(chapter, "book_id", "") or ""), chapter_id),
    }


def _quote(text: str) -> str:
    """Первые слова реплики: они, а не номер, узнаются с одного взгляда."""
    text = str(text or "").strip()
    return text if len(text) <= QUOTE_CHARS else text[:QUOTE_CHARS].rstrip() + "…"


def _section(title: str, items: list[str]) -> list[str]:
    if not items:
        return []
    out = ["", title]
    for item in items[:LINES_SHOWN]:
        out.append(f"• {item}")
    if len(items) > LINES_SHOWN:
        out.append(f"…и ещё {len(items) - LINES_SHOWN}")
    return out


def render_chapter_report(payload: dict) -> str:
    """Текст сообщения владельцу — по образцу письма диктору (`asr_notice`).

    Чистый отчёт — две строки: глава и состав со словами «всё на месте». Дальше
    идут только те разделы, для которых есть что сказать; списки обрезаны так же,
    как в письме диктору (`LINES_SHOWN`, «…и ещё N»).

    «Всё на месте» — обещание, что главу проверили и придраться не к чему. Если
    хоть одно распознавание главы упало (`failed`), это неправда: роль могла
    остаться непроверенной, а не безупречной, — и отчёт должен показать это, а
    не молчать до следующего письма.
    """
    chapter = str(payload.get("chapter") or "").strip()
    book_title = str(payload.get("book_title") or "").strip()
    roles = int(payload.get("roles") or 0)
    recorded_roles = int(payload.get("recorded_roles") or 0)
    takes = int(payload.get("takes") or 0)
    missing = payload.get("missing") or []
    weak = payload.get("weak") or []
    markup = payload.get("markup") or []
    failed = payload.get("failed") or []
    link = str(payload.get("link") or "").strip()

    header = f"{book_title} · {chapter}".strip(" ·")
    composition = f"Роли {recorded_roles}/{roles} · дублей {takes}"

    if not missing and not weak and not markup and not failed:
        return f"📖 {header}\n{composition} · всё на месте"

    out = [f"📖 {header}", composition]
    out += _section("Не дочитано:", [
        f"{item.get('role')}: {_quote(line)}"
        for item in missing for line in (item.get("lines") or [])
    ])
    out += _section("Нашлось плохо:", [
        f"{item.get('role')}: {_quote(item.get('text'))}" for item in weak
    ])
    out += _section("Похоже на ошибку разметки:", [
        f"{item.get('role')}: {_quote(item.get('text'))}" for item in markup
    ])
    out += _section("Не распозналось:", [
        f"{item.get('role')}: {_quote(item.get('error')) or 'без сообщения об ошибке'}" for item in failed
    ])
    if link:
        out += ["", link]
    return "\n".join(out)

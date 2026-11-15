"""Диктору сообщают, каких его реплик не нашлось.

Владельцу хватает числа на экране: он туда и так смотрит. Диктору нужно письмо — на
экран он заходит, когда сам захочет, а перезаписывать надо, пока он ещё у микрофона.

Реплики приводятся текстом, а не номерами: номер реплики ничего не значит для того,
кто читал вслух, а первые слова он узнаёт сразу.

Имя актёра ищется тем же нечётким `names_match`, что и в «ты утверждён на роль»
(`app/services/role_approval.py`), и подвержено той же беде: короткому или частому
имени может отозваться не одна учётка. Писать наугад нельзя — письмо о пропущенных
репликах адресовано конкретному человеку у микрофона, — поэтому неоднозначное имя
уходит релеем владельцу и агентам со своей причиной и списком, кто подошёл. Учётки
нет совсем — как и раньше, письма нет и релея нет, только причина в ответе.
"""
from __future__ import annotations

from app.services.role_approval import relay_to_owner_and_agents, resolve_actor_candidates, role_script_link
from app.services.telegram import send_telegram_message
from app.services import studio

#: сколько реплик перечислить, прежде чем перейти на счёт
LINES_SHOWN = 6
#: длина цитаты: первых слов хватает, чтобы узнать реплику
QUOTE_CHARS = 60


def _lines_letter(*, header: str, role: str, chapter: str, book_title: str, texts: list[str],
                  call_to_action: str, link: str = "") -> str:
    """Общий каркас письма о репликах: шапка со счётом, цитаты, призыв к действию, ссылка.

    `missing_lines_notice` и `moved_lines_notice` рассказывают о разном (не нашлось /
    правка отдала роль другому), но письмо устроено одинаково — этот каркас и есть их
    общая часть, вынесенная в одно место, чтобы менять формат письма один раз, а не два.
    """
    texts = [str(text or "").strip() for text in texts or [] if str(text or "").strip()]
    if not texts:
        return ""
    out = [
        header.format(count=len(texts)),
        f"Role: {str(role or '').strip()}",
        f"{str(book_title or '').strip()} · {str(chapter or '').strip()}".strip(" ·"),
        "",
    ]
    for text in texts[:LINES_SHOWN]:
        quote = text if len(text) <= QUOTE_CHARS else text[:QUOTE_CHARS].rstrip() + "…"
        out.append(f"• {quote}")
    if len(texts) > LINES_SHOWN:
        out.append(f"…and {len(texts) - LINES_SHOWN}")
    out += ["", call_to_action]
    if str(link or "").strip():
        out.append(link.strip())
    return "\n".join(out)


def _ambiguous_relay_text(*, about: str, role: str, chapter: str, book_title: str,
                          actor_name: str, candidates: list[str]) -> str:
    """Что прочтут владелец и агенты, когда имени актёра подошло больше одной учётки.

    Тот же каркас (роль, книга/глава), что у письма диктору, плюс список кандидатов —
    чтобы владелец сам разобрался, кому передать, а не система угадывала."""
    return "\n".join([
        f"📣 {studio.name()} — {about}",
        f"Role: {str(role or '').strip()}",
        f"{str(book_title or '').strip()} · {str(chapter or '').strip()}".strip(" ·"),
        f"The name «{str(actor_name or '').strip()}» matches multiple people: {', '.join(candidates)}; please contact them directly.",
    ])


def _deliver(db, *, actor_name: str, text: str, count: int, about: str, role: str, chapter: str, book_title: str) -> dict:
    """Отправить готовый текст письма тому, кто читал. Общий хвост обоих `notify_*`.

    Учётка не найдена вовсе (`candidates` пуст) — поведение то же, что было: тихий
    `no_telegram`, без релея (об этом до сих пор узнавал только счётчик на экране
    владельца — трогать не просили). Найдено больше одной — новая ветка: писать
    наугад нельзя, релей с именами кандидатов вместо личного письма.
    """
    if not text:
        return {"notified": False, "reason": "nothing_missing", "actor_name": str(actor_name or ""), "missing": 0}
    candidates = resolve_actor_candidates(db, actor_name)
    if len(candidates) > 1:
        relay_text = _ambiguous_relay_text(
            about=about, role=role, chapter=chapter, book_title=book_title,
            actor_name=actor_name, candidates=[label for _, label in candidates],
        )
        relayed = relay_to_owner_and_agents(db, relay_text)
        return {"notified": False, "reason": "ambiguous", "actor_name": str(actor_name or ""), "missing": count, "relayed": relayed}
    chat_id = candidates[0][0] if candidates else ""
    if not chat_id:
        return {"notified": False, "reason": "no_telegram", "actor_name": str(actor_name or ""), "missing": count}
    if not send_telegram_message(db, text, chat_ids=[chat_id], direct=True):
        return {"notified": False, "reason": "not_sent", "actor_name": str(actor_name or ""), "missing": count}
    return {"notified": True, "reason": "sent", "actor_name": str(actor_name or ""), "missing": count}


def missing_lines_notice(*, role: str, chapter: str, book_title: str, lines: list[dict], link: str = "",
                         language: str = "ru", narrator: bool | None = None) -> str:
    """Что диктор прочтёт. Пусто — сообщать не о чем, и незачем беспокоить."""
    missing = [str(line.get("text") or "").strip() for line in _coverage_lines(role, lines, language=language, narrator=narrator) if not line.get("matched")]
    missing = [text for text in missing if text]
    return _lines_letter(
        header="🎧 Lines not found in the recording: {count}", role=role, chapter=chapter, book_title=book_title,
        texts=missing, call_to_action="Please check the recording and send any missing lines.", link=link,
    )


def audit_findings_notice(*, role: str, chapter: str, book_title: str, items=None,
                          texts=None, link: str = "") -> str:
    """Нельзя восстановить услышанное слово из старого списка строк сценария."""
    return "\n\n".join(audit_findings_messages(role=role, chapter=chapter, book_title=book_title,
                                              items=items or [], link=link))


def notify_audit_findings(db, *, actor_name: str, role: str, chapter: str, book_title: str,
                          book_id: str, items=None, texts=None, send=None) -> dict:
    """Адресат разрешается один раз: многосоставное письмо не дублирует релей."""
    link = role_script_link(book_id, role)
    messages = audit_findings_messages(role=role, chapter=chapter, book_title=book_title,
                                       items=items or [], link=link)
    count = len([item for item in items or [] if item.get("expected") and item.get("heard")])
    result = {"notified": False, "reason": "nothing_missing", "actor_name": str(actor_name or ""), "missing": count}
    if not messages:
        return result
    candidates = resolve_actor_candidates(db, actor_name)
    if len(candidates) > 1:
        relay = _ambiguous_relay_text(about="please contact the actor about confirmed spoken errors; their name is ambiguous",
                                     role=role, chapter=chapter, book_title=book_title,
                                     actor_name=actor_name, candidates=[label for _, label in candidates])
        result.update(reason="ambiguous", relayed=relay_to_owner_and_agents(
            db, relay))
        return result
    if not candidates or not candidates[0][0]:
        result["reason"] = "no_telegram"
        return result
    for message in messages:
        if not (send or send_telegram_message)(db, message, chat_ids=[candidates[0][0]], direct=True):
            result["reason"] = "not_sent"
            return result
    result.update(notified=True, reason="sent")
    return result


def notify_missing_lines(
    db,
    *,
    actor_name: str,
    role: str,
    chapter: str,
    book_title: str,
    book_id: str,
    lines: list[dict],
    language: str = "ru",
    narrator: bool | None = None,
) -> dict:
    """Отправить письмо тому, кто читал. Возвращает, дошло ли, — и почему нет.

    Тем же путём, что «ты утверждён на роль»: адресат ищется по имени в касте, письмо
    идёт лично и не подменяется владельцем.
    """
    text = missing_lines_notice(
        role=role, chapter=chapter, book_title=book_title, lines=lines,
        link=role_script_link(book_id, role), language=language, narrator=narrator,
    )
    missing = sum(1 for line in _coverage_lines(role, lines, language=language, narrator=narrator)
                  if not line.get("matched") and str(line.get("text") or "").strip())
    return _deliver(
        db, actor_name=actor_name, text=text, count=missing,
        about="please contact the actor about missing lines; their name is ambiguous",
        role=role, chapter=chapter, book_title=book_title,
    )


def moved_lines_notice(*, role: str, chapter: str, book_title: str, texts: list[str], link: str = "") -> str:
    """Письмо о репликах, которые правка разметки отдала этой роли, а в записи их нет.

    Отдельный текст, а не «не нашлось реплик»: диктор записал всё, что было в его
    сценарии, и упрёк в пропуске сказал бы ему неправду. Поменялся сценарий, а не он.
    """
    return _lines_letter(
        header="✏️ The role attribution changed. These lines are now yours and were not found in the recording: {count}",
        role=role, chapter=chapter, book_title=book_title, texts=texts,
        call_to_action="Record these lines in a separate file.", link=link,
    )


def notify_moved_lines(db, *, actor_name: str, role: str, chapter: str, book_title: str,
                       book_id: str, texts: list[str]) -> dict:
    """Отправить письмо о перенесённых репликах тому, кто читает роль в этой главе."""
    text = moved_lines_notice(role=role, chapter=chapter, book_title=book_title, texts=texts,
                              link=role_script_link(book_id, role))
    count = len([t for t in texts or [] if str(t or "").strip()])
    return _deliver(
        db, actor_name=actor_name, text=text, count=count,
        about="please contact the actor about reassigned lines; their name is ambiguous",
        role=role, chapter=chapter, book_title=book_title,
    )


def _coverage_lines(role: str, lines: list[dict], *, language: str = "ru", narrator: bool | None = None) -> list[dict]:
    from app.services.shared_runtime import is_narrator_name

    if not (is_narrator_name(role) if narrator is None else narrator):
        return lines or []
    # Поздний импорт разрывает зависимость аудита от собственного письма.
    from app.services.asr_audit import classify_narrator_line
    return [line for line in lines or [] if classify_narrator_line(
        "- " + str(line.get("text") or "").strip("-–— ,;:.!?… "), language=language) != "tag"]


def _utf16_size(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def _short(text: str, limit: int) -> str:
    text = str(text or "").strip()
    if _utf16_size(text) <= limit:
        return text
    while _utf16_size(text) > limit - 1:
        text = text[:max(0, len(text) - max(1, (_utf16_size(text) - limit) // 2))]
    return text.rstrip() + "…"


def _source_time(value) -> str:
    import math
    try:
        seconds = float(value)
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError
        seconds = int(seconds)
        return f"{seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"
    except (TypeError, ValueError, OverflowError):
        return "time unknown"


def _error_row(item: dict) -> str:
    start, end = _source_time(item.get("start")), _source_time(item.get("end"))
    time = start if start == end else f"{start}–{end}"
    return "\n".join([
        f"• {_short(item.get('filename') or 'Source file not specified', 180)} · ≈ {time}",
        f"Expected: «{_short(item.get('expected'), 600)}»",
        f"Heard: «{_short(item.get('heard'), 600)}»",
        f"Context: {_short(item.get('context'), 350)}",
    ])


def audit_findings_messages(*, role: str, chapter: str, book_title: str, items: list[dict],
                            link: str = "", summary: str = "") -> list[str]:
    """Все оговорки доходят отдельными целыми пунктами в пределах Telegram UTF-16."""
    items = [item for item in items or [] if item.get("expected") and item.get("heard")]
    if not items and not summary:
        return []
    header = _short(summary or "\n".join([
        f"🔍 Confirmed spoken errors: {len(items)}",
        f"Role: {role}", f"{book_title} · {chapter}".strip(" ·"),
    ]), 750)
    footer = ("Times refer approximately to the source recording. "
              "Listen to these passages and check the linked script. "
              "For a full corrected chapter, upload the chapter file as _fix. For a short correction, record the whole line and explicitly choose Short correction only when uploading.") if items and not summary else ""
    if link:
        footer += "\n" + _short(link, 400)
    # Место под нумерацию резервируется до упаковки; длина emoji — две единицы.
    capacity = 3500 - _utf16_size(header + footer) - 80
    groups, current = [], []
    for item in items:
        row = _error_row(item)
        if current and _utf16_size("\n\n".join([*current, row])) > capacity:
            groups.append(current)
            current = []
        current.append(row)
    groups.append(current)
    return ["\n\n".join(part for part in [header + (f"\nPart {number}/{len(groups)}" if len(groups) > 1 else ""),
                                            "\n\n".join(group), footer] if part)
            for number, group in enumerate(groups, 1)]

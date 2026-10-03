from __future__ import annotations

WORDING = {"mismatch": "не сходится сумма", "missing": "нет на месте"}


def integrity_notice(chapter_label: str, rows: list[dict]) -> str:
    """Строка в Телеграм о файлах главы, не прошедших сверку."""
    if not rows:
        return ""
    lines = [f"⚠️ Целостность: {chapter_label}"]
    for row in rows:
        what = WORDING.get(str(row.get("verify_state") or ""), "проблема")
        who = str(row.get("actor_name") or "").strip() or "актёр не указан"
        lines.append(f"• {row.get('role') or '?'} — {who}: {what} ({row.get('canonical_filename') or ''})")
    lines.append("Файл не удалён. Нужно попросить перезалить.")
    return "\n".join(lines)


def notify_integrity_problem(db, chapter_id: str) -> bool:
    """Сообщить владельцу о битых файлах главы. `False` — сообщать не о чем."""
    from app.models import ScriptChapter
    from app.services.audio_integrity import MISMATCH, MISSING, chapter_files
    from app.services.chapter_delivery import _chapter_label
    from app.services.telegram import send_telegram_message

    chapter = db.get(ScriptChapter, str(chapter_id or "").strip())
    if chapter is None:
        return False
    rows = [
        {
            "role": item.role,
            "actor_name": item.actor_name,
            "canonical_filename": item.canonical_filename,
            "verify_state": item.verify_state,
        }
        for item in chapter_files(db, chapter_id)
        if str(item.verify_state or "") in {MISMATCH, MISSING}
    ]
    text = integrity_notice(_chapter_label(chapter), rows)
    if not text:
        return False
    send_telegram_message(db, text)
    return True


def mirror_notice(chapter_label: str, rows: list[dict]) -> str:
    """Строка в Телеграм о дублях главы, чья копия на NAS не сошлась по сумме.

    Отдельно от `integrity_notice`: та говорит про целостность оригинала на сервере,
    эта — про целостность его копии на NAS. Смешать их в одном сообщении значило бы
    сказать владельцу, что испорчен оригинал, и погнать его просить у актёра перезалив,
    тогда как оригинал цел и чинить нужно только зеркало.
    """
    if not rows:
        return ""
    lines = [f"⚠️ Зеркало: {chapter_label}"]
    for row in rows:
        who = str(row.get("actor_name") or "").strip() or "актёр не указан"
        lines.append(f"• {row.get('role') or '?'} — {who}: не сошлась копия на NAS ({row.get('canonical_filename') or ''})")
    # Не обещаем повтора: файл с расхождением исключён из кругов зеркалирования
    # насовсем и ждёт человека. Обещать кнопку, которой нет, хуже, чем сказать,
    # что запись пока живёт в одном экземпляре.
    lines.append("Файл на сервере цел, перезаливать его не нужно. Копирование само не повторится: "
                 "пока зеркало не починено, запись существует в одном экземпляре.")
    return "\n".join(lines)


def notify_mirror_problem(db, chapter_id: str) -> bool:
    """Сообщить владельцу о копиях главы на NAS, не сошедшихся по сумме.

    `False` — сообщать не о чем.
    """
    from app.models import ScriptChapter
    from app.services.audio_integrity import chapter_files
    from app.services.audio_mirror import MISMATCH
    from app.services.chapter_delivery import _chapter_label
    from app.services.telegram import send_telegram_message

    chapter = db.get(ScriptChapter, str(chapter_id or "").strip())
    if chapter is None:
        return False
    rows = [
        {
            "role": item.role,
            "actor_name": item.actor_name,
            "canonical_filename": item.canonical_filename,
        }
        for item in chapter_files(db, chapter_id)
        if str(item.mirror_state or "") == MISMATCH
    ]
    text = mirror_notice(_chapter_label(chapter), rows)
    if not text:
        return False
    send_telegram_message(db, text)
    return True

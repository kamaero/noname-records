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
        f"Роль: {str(role or '').strip()}",
        f"{str(book_title or '').strip()} · {str(chapter or '').strip()}".strip(" ·"),
        "",
    ]
    for text in texts[:LINES_SHOWN]:
        quote = text if len(text) <= QUOTE_CHARS else text[:QUOTE_CHARS].rstrip() + "…"
        out.append(f"• {quote}")
    if len(texts) > LINES_SHOWN:
        out.append(f"…и ещё {len(texts) - LINES_SHOWN}")
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
        f"Роль: {str(role or '').strip()}",
        f"{str(book_title or '').strip()} · {str(chapter or '').strip()}".strip(" ·"),
        f"Имя «{str(actor_name or '').strip()}» подходит нескольким людям: {', '.join(candidates)} — сообщите сами.",
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


def missing_lines_notice(*, role: str, chapter: str, book_title: str, lines: list[dict], link: str = "") -> str:
    """Что диктор прочтёт. Пусто — сообщать не о чем, и незачем беспокоить."""
    missing = [str(line.get("text") or "").strip() for line in lines or [] if not line.get("matched")]
    missing = [text for text in missing if text]
    return _lines_letter(
        header="🎧 В записи не нашлось реплик: {count}", role=role, chapter=chapter, book_title=book_title,
        texts=missing, call_to_action="Проверьте, всё ли записано, и дошлите недостающее.", link=link,
    )


def notify_missing_lines(
    db,
    *,
    actor_name: str,
    role: str,
    chapter: str,
    book_title: str,
    book_id: str,
    lines: list[dict],
) -> dict:
    """Отправить письмо тому, кто читал. Возвращает, дошло ли, — и почему нет.

    Тем же путём, что «ты утверждён на роль»: адресат ищется по имени в касте, письмо
    идёт лично и не подменяется владельцем.
    """
    text = missing_lines_notice(
        role=role, chapter=chapter, book_title=book_title, lines=lines,
        link=role_script_link(book_id, role),
    )
    missing = sum(1 for line in lines or [] if not line.get("matched"))
    return _deliver(
        db, actor_name=actor_name, text=text, count=missing,
        about="актёра с неоднозначным именем надо предупредить о пропущенных репликах самим",
        role=role, chapter=chapter, book_title=book_title,
    )


def moved_lines_notice(*, role: str, chapter: str, book_title: str, texts: list[str], link: str = "") -> str:
    """Письмо о репликах, которые правка разметки отдала этой роли, а в записи их нет.

    Отдельный текст, а не «не нашлось реплик»: диктор записал всё, что было в его
    сценарии, и упрёк в пропуске сказал бы ему неправду. Поменялся сценарий, а не он.
    """
    return _lines_letter(
        header="✏️ В разметке поправили роль — эти реплики теперь ваши, а в записи их нет: {count}",
        role=role, chapter=chapter, book_title=book_title, texts=texts,
        call_to_action="Допишите их отдельным файлом.", link=link,
    )


def notify_moved_lines(db, *, actor_name: str, role: str, chapter: str, book_title: str,
                       book_id: str, texts: list[str]) -> dict:
    """Отправить письмо о перенесённых репликах тому, кто читает роль в этой главе."""
    text = moved_lines_notice(role=role, chapter=chapter, book_title=book_title, texts=texts,
                              link=role_script_link(book_id, role))
    count = len([t for t in texts or [] if str(t or "").strip()])
    return _deliver(
        db, actor_name=actor_name, text=text, count=count,
        about="актёра с неоднозначным именем надо предупредить о перенесённых репликах самим",
        role=role, chapter=chapter, book_title=book_title,
    )

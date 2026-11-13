from __future__ import annotations

import re

from fastapi import Request

from app.auth import session_payload
from app.models import Character
from app.services.telegram import names_match


def recording_identity(request: Request, default_actor_name: str = "") -> tuple[str, str]:
    payload = session_payload(request)
    user_id = str(payload.get("uid") or payload.get("sub") or "").strip()
    user_name = str(payload.get("display_name") or default_actor_name or payload.get("sub") or "").strip()
    return user_id, user_name


def resolve_recording_my_roles(db, book_id: str, actor_name: str, role_line_counts: dict[str, int]) -> list[dict]:
    if not book_id:
        return []
    actor_name = (actor_name or "").strip()
    characters = db.query(Character).filter(Character.book_id == book_id).order_by(Character.name.asc()).all()
    matched: list[dict] = []
    for ch in characters:
        if not (ch.name or "").strip():
            continue
        if actor_name and names_match(actor_name, (ch.actor_name or "").strip()):
            matched.append(
                {
                    "name": ch.name.strip(),
                    "actor_name": (ch.actor_name or "").strip(),
                    "lines": int(role_line_counts.get(ch.name.strip(), 0)),
                }
            )
    from app.models import ScriptBook
    from app.services.chapter_titles import TITLE_ROLE, title_actor
    assigned = title_actor(db.get(ScriptBook, book_id))
    if assigned and actor_name and names_match(actor_name, assigned):
        matched.append({"name": TITLE_ROLE, "actor_name": assigned, "lines": int(role_line_counts.get(TITLE_ROLE, 0))})
    matched.sort(key=lambda item: (-int(item["lines"]), str(item["name"])))
    return matched


def upload_notice(
    user_name: str,
    files: list[dict],
    *,
    login: str = "",
    places_shown: int = 3,
    confirmed_chapters: list[tuple[int, int]] | None = None,
) -> str:
    """The Telegram line the owner reads when takes arrive.

    It used to say «Пользователь: tg_900000102» — the synthetic login Telegram sign-in
    invents, which names nobody. Six of those in a row told the owner that something
    was happening and nothing else, so the line now carries the person and the place:
    who is working, on which chapter, in which role.

    `confirmed_chapters` — пары «глава в имени файла, выбранная глава» тех файлов, где
    диктор снял отказ галочкой. Без этой строки подтверждённая вручную загрузка выглядит
    в точности как обычная, а она — ровно тот случай, ради которого сверку и завели:
    один такой файл стоил 82 МБ платного распознавания и разбора вручную.
    """
    who = str(user_name or "").strip() or str(login or "").strip() or 'Unknown actor'
    # проба и дубль приходят разными пачками: одна — заявка на роль, другая — работа
    auditions = bool(files) and all(str(item.get("kind") or "take") == "audition" for item in files)
    places: list[str] = []
    for item in files:
        chapter = "" if auditions else str(item.get("chapter") or "").strip()
        role = str(item.get("role") or "").strip()
        where = " · ".join(part for part in (f'chapter {chapter}' if chapter else "", role) if part)
        if where and where not in places:
            places.append(where)

    lines = ['🎧 Role auditions' if auditions else '📦 Studio takes', who]
    lines.extend(places[:places_shown])
    if len(places) > places_shown:
        lines.append(f'…and {len(places) - places_shown}')
    if len(files) != 1:
        lines.append(f'Files: {len(files)}')
    for filename_chapter, chosen_chapter in confirmed_chapters or []:
        lines.append(
            f'⚠️ Chapter confirmed manually: filename chapter {filename_chapter}, selected chapter {chosen_chapter}'
        )
    return "\n".join(lines)


def notify_author_about_auditions(db, files: list[dict], *, user_name: str, login: str = "") -> int:
    """Сказать автору книги, что кто-то пробуется на роль. Возвращает число доставок.

    Пробы адресованы автору: чей голос подходит его героям, решает он. Дубли — нет, это ход
    записи, и автор на него не подписан; поэтому из пачки берутся только пробы.

    `direct=True` здесь обязателен. Без него `OWNER_TELEGRAM_ID` подменит адресата
    владельцем — так задумано для уведомлений о ходе работ, — и автор не узнает ничего, а
    владелец получит второй экземпляр того, что ему и так пришло.

    Адресат ищется по РОЛИ, а не по имени: сменится автор или появится второй — код менять
    не придётся.
    """
    from app.models import TelegramAuthAccount
    from app.services.telegram import send_telegram_message

    auditions = [item for item in files if str(item.get("kind") or "take") == "audition"]
    if not auditions:
        return 0
    chat_ids = [
        str(row.telegram_user_id or "").strip()
        for row in db.query(TelegramAuthAccount)
        .filter(TelegramAuthAccount.role == "author", TelegramAuthAccount.is_active == "true")
        .all()
        if str(row.telegram_user_id or "").strip()
    ]
    if not chat_ids:
        return 0
    return send_telegram_message(
        db, upload_notice(user_name, auditions, login=login), chat_ids=chat_ids, direct=True,
    )

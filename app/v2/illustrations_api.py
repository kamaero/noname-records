"""Ручки иллюстраций: кто на картинке и где этот портрет показывать.

В книге картинки не подписаны, поэтому привязку делает человек — экран
`/app/books/{id}/illustrations` показывает картинку, текст вокруг неё и кандидатов из
каста. Подсказка считается здесь же, на лету: каст меняется, и замороженная при
загрузке подсказка начала бы врать.

Привязка — работа редактора; смотреть получившийся портрет может любой вошедший, в том
числе диктор: ради него всё и делается.
"""
from __future__ import annotations

import os

from fastapi import Request
from fastapi.responses import JSONResponse

from app.db import SessionLocal
from app.models import AuthorCharacter, BookIllustration, ScriptBook
from app.services.book_images import candidates, cast_index, image_response
from app.time_utils import utcnow_naive
from app.v2.api import (
    _actor,
    _can_edit,
    _is_authenticated,
    _json_body,
    bad_request_response,
    forbidden_response,
    not_found_response,
    unauthorized_response,
)

#: сколько картинок отдавать за раз — экран листает их по одной, но список нужен весь
MAX_ITEMS = 500


def image_url(illustration_id: str) -> str:
    return f"/api/v2/illustrations/{illustration_id}/image"


def _row_dict(row: BookIllustration, index, names: dict[str, str]) -> dict:
    bound = str(row.character_id or "")
    return {
        "id": str(row.id),
        "ordinal": int(row.ordinal or 0),
        "url": image_url(str(row.id)),
        "context": str(row.context or ""),
        "chapter": str(row.chapter_hint or ""),
        "status": str(row.status or "new"),
        "character": {"id": bound, "name": names.get(bound, "")} if bound else None,
        "candidates": candidates(str(row.context or ""), index),
    }


async def api_v2_book_illustrations(request: Request, book_id: str):
    """Все иллюстрации книги с подсказками — вход экрана привязки."""
    if not _is_authenticated(request):
        return unauthorized_response()
    if not _can_edit(request):
        return forbidden_response()
    with SessionLocal() as db:
        book = db.get(ScriptBook, str(book_id or "").strip())
        if book is None:
            return not_found_response("book_not_found")
        if request.query_params.get("summary"):
            # Хабу нужны только цифры — показывать ли строку «Портреты» и сколько осталось.
            statuses = [s for (s,) in db.query(BookIllustration.status)
                        .filter(BookIllustration.book_id == book.id).all()]
            return JSONResponse({"counts": {
                "total": len(statuses),
                "bound": statuses.count("bound"),
                "skipped": statuses.count("skipped"),
            }})
        rows = (
            db.query(BookIllustration)
            .filter(BookIllustration.book_id == book.id)
            .order_by(BookIllustration.ordinal.asc())
            .limit(MAX_ITEMS)
            .all()
        )
        index = cast_index(db, book.id)
        names = {
            str(row.id): str(row.canonical_name or "")
            for row in db.query(AuthorCharacter)
            .filter(AuthorCharacter.author_id == str(book.author_id or "")).all()
        }
        items = [_row_dict(row, index, names) for row in rows]
        return JSONResponse({
            "book": {"id": book.id, "title": str(book.display_title or book.title or "")},
            "counts": {
                "total": len(items),
                "bound": len([i for i in items if i["status"] == "bound"]),
                "skipped": len([i for i in items if i["status"] == "skipped"]),
            },
            # Весь каст книги с канон-карточкой — для поиска, когда подсказка промахнулась.
            # В индексе по строке на каждое написание имени, а в поиске роль одна.
            "cast": sorted(
                ({"character_id": cid, "name": name}
                 for cid, name in {cid: name for _key, name, cid in index}.items()),
                key=lambda item: item["name"],
            ),
            "items": items,
        })


async def api_v2_bind_illustration(request: Request, illustration_id: str):
    """Сказать, кто на картинке, или отметить её сценой без портрета."""
    if not _is_authenticated(request):
        return unauthorized_response()
    if not _can_edit(request):
        return forbidden_response()
    body = await _json_body(request)
    if body is None:
        return bad_request_response()
    character_id = str(body.get("character_id") or "").strip()
    skip = bool(body.get("skip"))
    actor_uid, actor_name = _actor(request)
    with SessionLocal() as db:
        row = db.get(BookIllustration, str(illustration_id or "").strip())
        if row is None:
            return not_found_response("illustration_not_found")
        if skip:
            row.character_id, row.status = "", "skipped"
        elif character_id:
            canon = db.get(AuthorCharacter, character_id)
            if canon is None:
                return bad_request_response("unknown_character")
            row.character_id, row.status = character_id, "bound"
        else:
            # Пустой выбор — снятие привязки: человек передумал, а не ошибся.
            row.character_id, row.status = "", "new"
        row.bound_by = actor_name or actor_uid
        row.bound_at = utcnow_naive()
        db.commit()
        return JSONResponse({"ok": True, "id": str(row.id), "status": row.status,
                             "character_id": str(row.character_id or "")})


async def api_v2_illustration_image(request: Request, illustration_id: str):
    """Сама картинка. Видит любой вошедший — портрет для того и нужен."""
    if not _is_authenticated(request):
        return unauthorized_response()
    with SessionLocal() as db:
        row = db.get(BookIllustration, str(illustration_id or "").strip())
        if row is None or not os.path.isfile(str(row.stored_path or "")):
            return not_found_response("image_not_found")
        return image_response(str(row.stored_path))


def portraits_for(db, character_ids: list[str]) -> dict[str, str]:
    """id канон-карточки → ссылка на её портрет. Первый по порядку в книге.

    Одному персонажу может достаться несколько иллюстраций; портретом становится самая
    ранняя — она обычно и есть «знакомство» с героем.
    """
    wanted = [cid for cid in character_ids if cid]
    if not wanted:
        return {}
    rows = (
        db.query(BookIllustration)
        .filter(BookIllustration.character_id.in_(wanted), BookIllustration.status == "bound")
        .order_by(BookIllustration.ordinal.asc())
        .all()
    )
    out: dict[str, str] = {}
    for row in rows:
        out.setdefault(str(row.character_id), image_url(str(row.id)))
    return out

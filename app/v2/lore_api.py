"""Ручки лора: шпаргалка по миру книги — статьи энциклопедии и карточки персонажей.

Видит любой вошедший, кто видит книгу. Это единственный слой студии, сделанный не для
редактора, а прежде всего для диктора: актёр приходит на книгу, которой не читал, и
озвучивает расу, о которой не слышал.

Лор привязан к АВТОРУ книги, а не к книге: у Белозёровых «Крылья» хронологически стоят
между четвёртой и пятой «Семьёй волшебников», персонажи и арки сквозные.

Тела статей не ходят списком: энциклопедия — 1,2 МБ на 57 разделов, одна «Магия» весит
131 КБ. Списком идут оглавление и карточки (они короткие и нужны сразу, по ним же
работает поиск на клиенте), тело статьи приходит по клику, поиск по телам — на сервере.
"""
from __future__ import annotations

import os

from fastapi import Request
from fastapi.responses import JSONResponse

from app.db import SessionLocal
from app.models import Author, AuthorCharacter, LoreArticle, LoreImage, ScriptBook
from app.services.book_images import image_response
from app.v2.api import (
    _is_authenticated,
    not_found_response,
    unauthorized_response,
)

#: сколько знаков показать вокруг найденного слова
SNIPPET_RADIUS = 120
#: больше полусотни совпадений в списке никто не читает
MAX_HITS = 50


def _split_aliases(raw: str) -> list[str]:
    from app.v2.cast_ops import split_aliases

    return split_aliases(raw)


def _book_author(db, book_id: str):
    """(книга, автор) или (книга, None), если книга не привязана к автору."""
    book = db.get(ScriptBook, str(book_id or "").strip())
    if book is None:
        return None, None
    author_id = str(getattr(book, "author_id", "") or "")
    if not author_id:
        return book, None
    return book, db.get(Author, author_id)


def _articles(db, author_id: str) -> list[LoreArticle]:
    return (
        db.query(LoreArticle)
        .filter(LoreArticle.author_id == author_id)
        .order_by(LoreArticle.ordinal.asc())
        .all()
    )


def _entities(db, author_id: str, topics: set[str]) -> list[dict]:
    """Карточки сущностей по темам, у которых есть лор-статья.

    Берутся из профиля автора, а не из своей таблицы: те же строки кормят разметку и
    каст, и раздвоить их значит однажды разойтись. Карточка без описания пришла из
    каста, а не из энциклопедии, и показывать в шпаргалке пустое имя незачем.
    """
    from app.v2.illustrations_api import portraits_for

    rows = (
        db.query(AuthorCharacter)
        .filter(AuthorCharacter.author_id == author_id)
        .order_by(AuthorCharacter.canonical_name.asc())
        .all()
    )
    out = []
    for row in rows:
        topic = str(row.source_topic or "").strip()
        description = str(row.description or "").strip()
        if not description or (topics and topic not in topics):
            continue
        out.append({
            "id": str(row.id),
            "name": str(row.canonical_name or "").strip(),
            "aliases": _split_aliases(str(row.aliases or "")),
            "topic": topic,
            "description": description,
        })
    # Портрет — авторская иллюстрация, которую человек привязал к этой карточке.
    portraits = portraits_for(db, [item["id"] for item in out])
    for item in out:
        item["portrait"] = portraits.get(item["id"], "")
    return out


async def api_v2_book_lore(request: Request, book_id: str):
    """Оглавление статей и карточки сущностей для книги этого автора."""
    if not _is_authenticated(request):
        return unauthorized_response()
    with SessionLocal() as db:
        book, author = _book_author(db, book_id)
        if book is None:
            return not_found_response("book_not_found")
        if author is None:
            return JSONResponse({"author": None, "articles": [], "entities": []})
        articles = _articles(db, author.id)
        if not articles:
            return JSONResponse({"author": None, "articles": [], "entities": []})
        topics = {str(row.topic or "") for row in articles}
        return JSONResponse({
            "author": {"id": str(author.id), "name": str(author.name or "")},
            "articles": [
                {
                    "id": str(row.id),
                    "topic": str(row.topic or ""),
                    "title": str(row.title or ""),
                    "chars": len(str(row.body or "")),
                    "images": len([k for k in str(row.image_keys or "").split(",") if k.strip()]),
                }
                for row in articles
            ],
            "entities": _entities(db, author.id, topics),
        })


async def api_v2_lore_article(request: Request, article_id: str):
    """Тело одной статьи — приходит по клику, а не списком."""
    if not _is_authenticated(request):
        return unauthorized_response()
    with SessionLocal() as db:
        row = db.get(LoreArticle, str(article_id or "").strip())
        if row is None:
            return not_found_response("article_not_found")
        keys = [k.strip() for k in str(row.image_keys or "").split(",") if k.strip()]
        return JSONResponse({
            "id": str(row.id),
            "topic": str(row.topic or ""),
            "title": str(row.title or ""),
            "body": str(row.body or ""),
            "images": [f"/api/v2/lore/images/{str(row.author_id)}/{key}" for key in keys],
        })


async def api_v2_lore_search(request: Request, book_id: str):
    """Поиск по телам статей: слово и то, что вокруг него.

    Карточки ищутся на клиенте — они уже пришли списком; сюда приходит только то, что
    в браузере искать не по чему.
    """
    if not _is_authenticated(request):
        return unauthorized_response()
    query = " ".join(str(request.query_params.get("q") or "").split())
    if len(query) < 2:
        return JSONResponse({"query": query, "hits": []})
    with SessionLocal() as db:
        book, author = _book_author(db, book_id)
        if book is None:
            return not_found_response("book_not_found")
        if author is None:
            return JSONResponse({"query": query, "hits": []})
        needle = query.casefold()
        hits = []
        for row in _articles(db, author.id):
            body = str(row.body or "")
            position = body.casefold().find(needle)
            if position < 0:
                continue
            start = max(0, position - SNIPPET_RADIUS)
            end = min(len(body), position + len(query) + SNIPPET_RADIUS)
            hits.append({
                "id": str(row.id),
                "title": str(row.title or ""),
                "snippet": ("…" if start else "") + body[start:end].strip() + ("…" if end < len(body) else ""),
                "count": body.casefold().count(needle),
            })
            if len(hits) >= MAX_HITS:
                break
        hits.sort(key=lambda item: -item["count"])
        return JSONResponse({"query": query, "hits": hits})


async def api_v2_lore_image(request: Request, author_id: str, image_key: str):
    """Карта или схема из энциклопедии."""
    if not _is_authenticated(request):
        return unauthorized_response()
    with SessionLocal() as db:
        row = (
            db.query(LoreImage)
            .filter(LoreImage.author_id == str(author_id or "").strip(),
                    LoreImage.image_key == str(image_key or "").strip())
            .first()
        )
        if row is None or not os.path.isfile(str(row.stored_path or "")):
            return not_found_response("image_not_found")
        return image_response(str(row.stored_path))

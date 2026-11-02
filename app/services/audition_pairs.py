"""Пробы, собранные в пары «актёр + роль» (с 03.10).

Оценивают и утверждают актёра на роль, а не файл, поэтому экран показывает строку на
пару, а файлы — внутри неё. Здесь же — всё, что решается по правам смотрящего: оценки
видят только автор и студия (`sees_scores`), диктор — лишь отправленный отказ на своей
паре. Резать на сервере, а не на экране: ручки отдают пробы любому вошедшему.
"""
from __future__ import annotations

from app.models import AuditionRejection, Character
from app.services.audition_rejections import SENT, character_for_role, rejection_view, similar_character
from app.services.audition_scores import LANES, lane_labels, scores_by_pair
from app.services.auditions import book_auditions
from app.services.telegram import names_match
from app.v2.cast_ops import parse_actor_name


def _guess(characters, role: str) -> dict | None:
    """Роли пробы нет в касте — но, может быть, она там под другим написанием."""
    ch = similar_character(characters, role)
    if ch is None:
        return None
    return {"character_id": ch.id, "name": str(ch.name or ""), "actor_name": str(ch.actor_name or "")}


def _approved_by_character(characters) -> dict[str, tuple[str, str]]:
    """Персонаж → (имя роли, утверждённый актёр). Имя со «?» — предложение и занятым не делает."""
    approved: dict[str, tuple[str, str]] = {}
    for ch in characters:
        name, tentative = parse_actor_name(str(ch.actor_name or ""))
        if name and not tentative:
            approved[str(ch.id)] = (str(ch.name or ""), name)
    return approved


def book_pairs(db, book, *, sees_scores: bool, viewer_name: str) -> list[dict]:
    files = book_auditions(db, book_id=book.id) or []
    grouped: dict[tuple[str, str], list[dict]] = {}
    for item in files:  # свежие сверху — порядок пар идёт по самой свежей пробе
        grouped.setdefault((item["role"], item["actor_name"]), []).append(item)

    # Роль пробы сопоставляется с кастом по всем написаниям (регистр, псевдонимы):
    # из имени файла приходит «тик-так», а в касте — «Тик-Так».
    characters = db.query(Character).filter(Character.book_id == book.id).all()
    approved = _approved_by_character(characters)
    scores = scores_by_pair(db, book.id) if sees_scores else {}
    labels = lane_labels(db, book) if sees_scores else None
    rejections = {
        (str(rej.role), str(rej.actor_name)): rej
        for rej in db.query(AuditionRejection).filter_by(book_id=book.id)
    }
    per_role: dict[str, int] = {}
    for role, _actor in grouped:
        per_role[role] = per_role.get(role, 0) + 1

    pairs: list[dict] = []
    for (role, actor), auditions in grouped.items():
        ch = character_for_role(characters, role)
        here = approved.get(str(ch.id), ("", ""))[1] if ch is not None else ""
        rej = rejections.get((role, actor))
        if sees_scores:
            rejection = rejection_view(rej)
        elif rej is not None and rej.status == SENT and names_match(actor, viewer_name):
            rejection = rejection_view(rej)
        else:
            rejection = None
        pairs.append({
            "key": f"{book.id}|{role}|{actor}",
            "book_id": book.id,
            "book_title": str(book.display_title or book.title or ""),
            "role": role,
            "actor_name": actor,
            "character_id": ch.id if ch is not None else "",
            "role_actor": str(getattr(ch, "actor_name", "") or ""),
            "role_guess": _guess(characters, role) if ch is None else None,
            "role_auditions": per_role[role],
            "approved": bool(here and actor and names_match(here, actor)),
            "busy_on": sorted(
                role_name for char_id, (role_name, name) in approved.items()
                if (ch is None or char_id != str(ch.id)) and actor and names_match(name, actor)
            ),
            "auditions": auditions,
            "uploaded_at": auditions[0]["uploaded_at"],
            "scores": (scores.get((role, actor)) or {lane: None for lane in LANES}) if sees_scores else None,
            "lane_labels": labels,
            "rejection": rejection,
        })
    return pairs

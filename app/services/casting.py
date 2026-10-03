"""Назначение актёра на роль: одна точка входа для ячейки каста, выбора, рекаста и импорта.

Профиль автора — источник правды: утверждённый актёр персонажа стоит на нём во всех
книгах автора, где роль ещё не записана. Замена — только рекастом, «вперёд»: где
прежний актёр уже прислал дубли, он остаётся. Предложение «Имя?» — приглашение на
пробу в одной книге и никуда не переносится.
"""
from __future__ import annotations

import json

from app.models import (
    AudioFile,
    AuthorCharacter,
    BookBudget,
    Character,
    DictorAssignment,
    Recast,
    ScriptBook,
    User,
    UserRole,
)
from app.services.audio_uploads import derive_book_code
from app.services.role_votes import cast_role_vote
from app.services.shared_runtime import is_narrator_name
from app.services.telegram import names_match
from app.time_utils import utcnow_naive
from app.v2.cast_ops import approved_actor, parse_actor_name


def role_recorded(db, character: Character) -> bool:
    """Есть ли у роли в её книге хоть один дубль."""
    book = db.get(ScriptBook, character.book_id)
    if book is None:
        return False
    return db.query(AudioFile.id).filter(
        AudioFile.kind == "take",
        AudioFile.book_code == derive_book_code(str(book.title or "")),
        AudioFile.role == str(character.name or ""),
    ).first() is not None


def cycle_siblings(db, character: Character) -> list[Character]:
    """Тот же персонаж профиля в других книгах того же автора, по порядку книг."""
    profile_id = str(character.author_character_id or "").strip()
    if not profile_id:
        return []
    profile = db.get(AuthorCharacter, profile_id)
    if profile is None:
        return []
    return (
        db.query(Character)
        .join(ScriptBook, ScriptBook.id == Character.book_id)
        .filter(Character.author_character_id == profile_id,
                Character.id != character.id,
                ScriptBook.author_id == profile.author_id)
        .order_by(ScriptBook.created_at.asc(), ScriptBook.id.asc())
        .all()
    )


def propagate_approval(db, character: Character, *, voter_uid: str, voter_name: str, weight: int) -> dict:
    """Утверждённого в этой книге актёра — в профиль автора и во все книги цикла.

    Зовётся после голоса в самой книге: `character.actor_name` уже пересчитан. Если там
    не утверждение (пусто, «Имя?», «???»), ничего не переносится. В книге цикла, где
    роль записана, актёр не меняется — она возвращается в `kept`. Голос в каждой книге
    подаётся тем же голосующим и с тем же весом, что и исходный: решение в книге
    по-прежнему считают голоса, а не прямая запись поля. Поэтому голос может и
    проиграть (там автор весом два выбрал другого) — такая книга уходит в `outvoted`,
    а не в `changed`: письмо и журнал называют только книги, где актёр правда встал.
    Рассказчик по циклу не ходит никогда, даже связанный с профилем.
    """
    actor = approved_actor(str(character.actor_name or ""))
    result = {"changed": [], "kept": [], "outvoted": [], "profile_actor": ""}
    if not actor or is_narrator_name(str(character.name or "")):
        return result
    profile_id = str(character.author_character_id or "").strip()
    profile = db.get(AuthorCharacter, profile_id) if profile_id else None
    if profile is None:
        return result
    profile.actor_name = actor
    result["profile_actor"] = actor
    for sibling in cycle_siblings(db, character):
        book = db.get(ScriptBook, sibling.book_id)
        entry = {"book_id": sibling.book_id, "character_id": sibling.id,
                 "book_title": str(getattr(book, "title", "") or "")}
        current = approved_actor(str(sibling.actor_name or ""))
        if current == actor:
            continue
        if current and role_recorded(db, sibling):
            result["kept"].append({**entry, "actor": current})
            continue
        _vote_into(db, sibling, entry, actor, result, voter_uid=voter_uid, voter_name=voter_name, weight=weight)
    db.flush()
    return result


def _vote_into(db, target: Character, entry: dict, actor: str, result: dict, *,
               voter_uid: str, voter_name: str, weight: int) -> None:
    """Голос за актёра в книге цикла; в отчёт — по тому, чем голос кончился."""
    vote = cast_role_vote(db, character=target, voter_uid=voter_uid, voter_name=voter_name,
                          weight=weight, actor_name=actor)
    if vote["actor_name"] == actor:
        result["changed"].append(entry)
    else:
        result["outvoted"].append({**entry, "actor": approved_actor(vote["actor_name"]) or vote["actor_name"],
                                   "by": vote["overridden_by"]})


RECAST_REASONS = ("left", "overlap", "removed", "audition_failed", "other")


def recast_preview(db, character: Character, *, to_actor: str) -> dict:
    """Что сделает `recast` — без записи: окно рекаста показывает это до решения.

    Тот же обход цикла и то же правило «записано — остаётся прежний». Чем кончится голос
    в каждой книге, заранее не известно (там может перевесить автор), поэтому «сменится»
    здесь — «сменится, если голос пройдёт»; итог `recast` называет и проигранные книги.
    """
    actor = " ".join(str(to_actor or "").split())
    if not actor or actor.endswith("?"):
        raise ValueError("no_actor")
    if is_narrator_name(str(character.name or "")):
        raise ValueError("narrator")
    profile = db.get(AuthorCharacter, str(character.author_character_id or "")) if character.author_character_id else None
    from_actor = str((profile.actor_name if profile else "") or approved_actor(str(character.actor_name or "")) or "")
    changed, kept = [], []
    for target in [character, *cycle_siblings(db, character)]:
        book = db.get(ScriptBook, target.book_id)
        entry = {"book_id": target.book_id, "character_id": target.id, "book_title": str(getattr(book, "title", "") or "")}
        current = approved_actor(str(target.actor_name or ""))
        if current and current != actor and role_recorded(db, target):
            kept.append({**entry, "actor": current})
        elif current != actor:
            changed.append(entry)
    return {"from_actor": from_actor, "changed": changed, "kept": kept}


def recast(db, character: Character, *, to_actor: str, reason: str, comment: str,
           voter_uid: str, voter_name: str, weight: int) -> dict:
    """Заменить актёра персонажа во всём цикле «вперёд» и записать это в журнал.

    Новый актёр ставится в эту книгу и во все книги цикла, где роль не записана; где
    прежний уже прислал дубли, он остаётся. В отличие от утверждения, запись в журнале
    `recasts` обязательна: это решение, о котором спросят «почему».
    """
    actor = " ".join(str(to_actor or "").split())
    if reason not in RECAST_REASONS:
        raise ValueError("bad_reason")
    if not actor or actor.endswith("?"):
        raise ValueError("no_actor")
    if is_narrator_name(str(character.name or "")):
        raise ValueError("narrator")  # рассказчик свой в каждой книге, по циклу не ходит
    profile = db.get(AuthorCharacter, str(character.author_character_id or "")) if character.author_character_id else None
    from_actor = str((profile.actor_name if profile else "") or approved_actor(str(character.actor_name or "")) or "")
    result = {"changed": [], "kept": [], "outvoted": []}
    kept, changed = result["kept"], result["changed"]
    targets = [character, *cycle_siblings(db, character)]
    for target in targets:
        book = db.get(ScriptBook, target.book_id)
        entry = {"book_id": target.book_id, "character_id": target.id,
                 "book_title": str(getattr(book, "title", "") or "")}
        current = approved_actor(str(target.actor_name or ""))
        if current and current != actor and role_recorded(db, target):
            kept.append({**entry, "actor": current})
            continue
        if current != actor:
            _vote_into(db, target, entry, actor, result, voter_uid=voter_uid, voter_name=voter_name, weight=weight)
    if profile is not None:
        profile.actor_name = actor
    row = Recast(author_character_id=str(character.author_character_id or ""), role_name=str(character.name or ""),
                 from_actor=from_actor, to_actor=actor, reason=reason, comment=str(comment or "").strip(),
                 books_changed=json.dumps(changed, ensure_ascii=False), books_kept=json.dumps(kept, ensure_ascii=False),
                 actor_user_id=str(voter_uid or ""))
    db.add(row)
    db.flush()
    return {"recast_id": row.id, "from_actor": from_actor, **result}


def cycle_discrepancies(db, author_id: str) -> list[dict]:
    """Персонажи профиля, у которых утверждённые актёры по книгам (или профиль) расходятся.

    Книга, которую рекаст нарочно оставил прежнему актёру (роль там записана), —
    не расхождение, а итог решения: она видна в списке с пометкой `kept_by_recast`,
    но в счёт актёров не идёт, иначе рекаст висел бы в отчёте вечно.
    """
    out = []
    profiles = db.query(AuthorCharacter).filter(AuthorCharacter.author_id == author_id).all()
    for profile in profiles:
        kept_by_recast = set()
        for recast_row in db.query(Recast).filter(Recast.author_character_id == profile.id).all():
            for kept in json.loads(recast_row.books_kept or "[]"):
                kept_by_recast.add((kept.get("character_id"), kept.get("actor")))
        rows = (db.query(Character, ScriptBook)
                .join(ScriptBook, ScriptBook.id == Character.book_id)
                .filter(Character.author_character_id == profile.id, ScriptBook.author_id == author_id)
                .order_by(ScriptBook.created_at.asc()).all())
        books = []
        for ch, book in rows:
            actor = approved_actor(str(ch.actor_name or ""))
            books.append({"book_id": book.id, "book_title": str(book.title or ""), "actor": actor,
                          "kept_by_recast": (ch.id, actor) in kept_by_recast and role_recorded(db, ch)})
        actors = {b["actor"] for b in books if b["actor"] and not b["kept_by_recast"]}
        if profile.actor_name:
            actors.add(profile.actor_name)
        if len(actors) > 1:
            out.append({"author_character_id": profile.id, "role_name": str(profile.canonical_name or ""),
                        "profile_actor": str(profile.actor_name or ""), "books": books})
    return out


def _dictor_for(db, name: str) -> str:
    """Id единственного диктора, чьё имя подходит; пусто — нет или неоднозначно."""
    clean, _ = parse_actor_name(name)
    if not clean:
        return ""
    ids = [u.id for u in db.query(User).join(UserRole, UserRole.user_id == User.id)
           .filter(UserRole.role == "dictor").all() if names_match(clean, str(u.display_name or ""))]
    return ids[0] if len(ids) == 1 else ""


def rebuild_assignments(db, book_id: str) -> int:
    """Пересобрать `dictor_assignments` книги по касту и рассказчику. Возвращает число строк."""
    db.query(DictorAssignment).filter(DictorAssignment.book_id == book_id).delete(synchronize_session=False)
    rows = 0
    for character in db.query(Character).filter(Character.book_id == book_id).all():
        if is_narrator_name(str(character.name or "")):
            continue  # рассказчик книги — из сметы, ниже, одной строкой
        raw = str(character.actor_name or "").strip()
        user_id = _dictor_for(db, raw)
        if not user_id:
            continue
        _, tentative = parse_actor_name(raw)
        db.add(DictorAssignment(user_id=user_id, book_id=book_id, character_id=character.id,
                                role_name=str(character.name or ""),
                                state="proposed" if tentative else "approved",
                                recorded=role_recorded(db, character), updated_at=utcnow_naive()))
        rows += 1
    budget = db.query(BookBudget).filter(BookBudget.book_id == book_id).first()
    narrator = str(getattr(budget, "narrator_actor_name", "") or "")
    user_id = _dictor_for(db, narrator)
    if user_id:
        db.add(DictorAssignment(user_id=user_id, book_id=book_id, character_id="", role_name="Рассказчик",
                                state="approved", recorded=False, updated_at=utcnow_naive()))
        rows += 1
    db.flush()
    # Сроки проб и ролей — по тому же касту: сюда приходят все пути смены актёра.
    from app.services import role_deadlines

    role_deadlines.sync_book(db, book_id)
    return rows

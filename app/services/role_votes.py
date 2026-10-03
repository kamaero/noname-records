"""Кто утверждает актёра на роль, когда голосов два.

Владелец студии и автор смотрят на роль с разных сторон: один думает о загрузке и
сроках, другой — о том, как персонаж должен звучать. Голос автора весит два, голос
владельца — один, и это не вежливость, а способ разрешить спор: книгу написал он.

Голос — не действие, а мнение, которое остаётся. Поэтому голоса складываются, а
результат пересчитывается заново после каждого: передумал автор — решение уходит туда,
куда указывает уцелевший расклад, а не туда, где последний раз нажали. Пустой голос —
не отказ голосовать, а голос за то, чтобы роль осталась ничьей.
"""
from __future__ import annotations

from datetime import timedelta

from app.models import Character, RoleVote
from app.time_utils import utcnow_naive

#: вес голоса по роли в студии; диктор здесь не голосует вовсе
VOTE_WEIGHTS = {"author": 2, "admin": 1}

#: Кем подписана затравка — назначение, сделанное до того, как завели голоса.
#: Голос это не человека, поэтому uid — не чей-то, а служебный, и имя читается как
#: подлежащее: `overridden_by` попадает в фразу «так решил …», и человек должен
#: понять, что его перевесила запись в касте, а не кто-то из студии.
PRE_VOTE_UID = "cast:pre-vote"
PRE_VOTE_NAME = "прежний каст"
#: Вес затравки — один, как у владельца, и не два. Откуда взялось имя в карточке,
#: система не знает: оно старше таблицы голосов. Поэтому это самый слабый из
#: настоящих голосов — агент (0) ему проигрывает, автор (2) перебивает всегда, а
#: владелец (1) сравнивается и выигрывает по свежести, сохраняя право переназначить.
PRE_VOTE_WEIGHT = 1


def vote_weight(roles) -> int:
    """Вес самого весомого из голосов, что даёт набор ролей. 0 — голоса нет."""
    return max((VOTE_WEIGHTS.get(str(role), 0) for role in (roles or ())), default=0)


def role_votes(db, character_id: str) -> list[dict]:
    """Голоса по роли, тяжёлые сверху — в том порядке, в каком их читает человек."""
    rows = (
        db.query(RoleVote)
        .filter(RoleVote.character_id == str(character_id or "").strip())
        .order_by(RoleVote.weight.desc(), RoleVote.updated_at.desc())
        .all()
    )
    return [
        {
            "voter_uid": str(row.voter_uid or ""),
            "voter_name": str(row.voter_name or ""),
            "actor_name": str(row.actor_name or ""),
            "weight": int(row.weight or 0),
        }
        for row in rows
    ]


def _winner(rows: list[RoleVote]) -> tuple[str, str]:
    """Актёр с наибольшим суммарным весом и имя того, чей голос перевесил.

    При равенстве весов побеждает свежий голос: спор двух равных решает тот, кто
    высказался последним, — иначе решение замерло бы навсегда на первом.
    """
    if not rows:
        return "", ""
    totals: dict[str, int] = {}
    latest: dict[str, object] = {}
    for row in rows:
        actor = str(row.actor_name or "").strip()
        totals[actor] = totals.get(actor, 0) + int(row.weight or 0)
        seen = latest.get(actor)
        if seen is None or row.updated_at > getattr(seen, "updated_at", row.updated_at):
            latest[actor] = row
    actor = max(totals, key=lambda key: (totals[key], latest[key].updated_at))
    heaviest = max(
        (row for row in rows if str(row.actor_name or "").strip() == actor),
        key=lambda row: (int(row.weight or 0), row.updated_at),
    )
    return actor, str(heaviest.voter_name or "")


def cast_role_vote(db, *, character: Character, voter_uid: str, voter_name: str, weight: int, actor_name: str) -> dict:
    """Записать голос и пересчитать, за кем роль.

    Возвращает, кто теперь играет роль, изменилось ли это и чьим голосом — чтобы
    проголосовавший увидел не «сохранено», а что из его голоса вышло.

    Перед пересчётом стоящее в карточке имя, за которым нет ни одного голоса,
    заводится голосом весом `PRE_VOTE_WEIGHT`. Затравка нужна потому, что таблица
    голосов появилась позже касты: миграция `0017_role_votes` завела её пустой, а
    `app/services/author_profile.py` пишет имя актёра прямо в `Character.actor_name`.
    На боевой базе так сделано подавляющее большинство назначений — голосов за ними
    нет. Без затравки первый же голос оказывается единственным, и предложение агента
    весом 0 побеждает: молча стирает или (пустой строкой) снимает настоящее
    назначение, которое всего лишь старше таблицы. Затравка возвращает арифметике
    то, на что она с самого начала опиралась, — что за именем в карточке кто-то стоит.
    """
    uid = str(voter_uid or "").strip()
    actor = " ".join(str(actor_name or "").split())
    was = str(character.actor_name or "").strip()

    if was and not db.query(RoleVote.id).filter(RoleVote.character_id == character.id).first():
        db.add(RoleVote(
            character_id=character.id,
            voter_uid=PRE_VOTE_UID,
            voter_name=PRE_VOTE_NAME,
            actor_name=was,
            weight=PRE_VOTE_WEIGHT,
            # Не «сейчас»: назначение состоялось раньше голоса, который сейчас пишется,
            # и при равном весе свежим обязан считаться живой голос, а не затравка.
            updated_at=utcnow_naive() - timedelta(seconds=1),
        ))
        db.flush()

    row = (
        db.query(RoleVote)
        .filter(RoleVote.character_id == character.id, RoleVote.voter_uid == uid)
        .one_or_none()
    )
    if row is None:
        row = RoleVote(character_id=character.id, voter_uid=uid)
        db.add(row)
    row.voter_name = str(voter_name or "").strip()
    row.actor_name = actor
    row.weight = max(0, int(weight or 0))
    row.updated_at = utcnow_naive()
    db.flush()

    rows = db.query(RoleVote).filter(RoleVote.character_id == character.id).all()
    decided, by_whom = _winner(rows)
    character.actor_name = decided
    character.updated_at = utcnow_naive()

    return {
        "actor_name": decided,
        "changed": decided != was,
        # чей голос стоит за решением, если он не твой — тогда это и есть отказ
        "overridden_by": by_whom if decided != actor else "",
        "votes": role_votes(db, character.id),
    }

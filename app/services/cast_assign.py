"""Назначение актёра на роль — одна цепочка для сайта и бота (пакет C1, 05.10).

Утверждение — не запись поля, а голос и его последствия: профиль автора и книги цикла,
отказы остальным пробовавшимся, письмо утверждённому, пересборка назначений и сроков. Жило
в ручке каста; бот обязан звать ту же цепочку, а не копию — копия разошлась бы при первой
правке сайта. `notify`/`schedule` передаёт ручка: её тесты подменяют их в модуле ручки.
"""
from __future__ import annotations

from app.services.audition_rejections import schedule_rejections_for_approval
from app.services.casting import propagate_approval, rebuild_assignments
from app.services.role_approval import notify_role_approved, should_notify_actor_change
from app.services.role_votes import cast_role_vote, vote_weight
from app.services.shared_runtime import get_book
from app.v2.cast_ops import as_tentative, parse_actor_name


def assign_actor(db, *, character, actor_name: str, voter_uid: str, voter_name: str, roles,
                 agent_only: bool = False, notify=None, schedule=None) -> dict:
    """Голос за актёра роли и все последствия решения. Не коммитит.

    Возвращает `{"vote", "cycle", "approval"}` — то, что ручка каста отдаёт клиенту."""
    notify = notify or notify_role_approved
    schedule = schedule or schedule_rejections_for_approval
    weight = vote_weight(roles)
    was_actor = str(character.actor_name or "").strip()
    # Агент назначает предварительно всегда: знак дописывается за него.
    wanted = as_tentative(actor_name) if agent_only else actor_name
    vote = cast_role_vote(db, character=character, voter_uid=voter_uid, voter_name=voter_name,
                          weight=weight, actor_name=wanted)
    cycle = None
    if not agent_only and vote["actor_name"] == " ".join(str(actor_name or "").split()):
        # Утверждение — на персонажа цикла, а не на строку одной книги: профиль
        # автора ставит того же актёра во все книги, где роль не записана. Агент
        # только предлагает («Имя?»), и явная проверка не даст будущей правке
        # `as_tentative` молча открыть ему рекаст целого цикла. Несётся только
        # победивший голос: проигравший в этой книге не должен ставить победителя
        # в другие книги от имени проигравшего.
        cycle = propagate_approval(db, character, voter_uid=voter_uid, voter_name=voter_name, weight=weight)
    # Отказ пробовавшимся — только на событие утверждения, не на состояние:
    # иначе выкатка разослала бы письма по ролям, утверждённым месяц назад.
    # Агент утверждать не может (его имя всегда со «?»), цикл и профиль автора
    # отказов не заводят — за ними нет решения человека по пробам этой роли.
    # Голос агента весит ноль, но и его ход явно исключён: если бы он когда-нибудь
    # перевернул решение на чужое имя без «?», письмо ушло бы без решения человека.
    if vote.get("changed") and not agent_only:
        decided, tentative = parse_actor_name(str(character.actor_name or ""))
        book_row = get_book(db, character.book_id) if decided and not tentative else None
        if book_row is not None:
            schedule(db, book=book_row, role=str(character.name or ""), aliases=str(character.aliases or ""),
                     approved_actor=decided)
    # Утверждение на роль — новость для диктора, и до сих пор он узнавал о ней сам.
    approval = None
    cycle_changed = (cycle or {}).get("changed", [])
    if should_notify_actor_change(was_actor, str(character.actor_name or "").strip()):
        book = get_book(db, character.book_id)
        approval = notify(
            db, book_id=str(character.book_id), book_title=str(getattr(book, "title", "") or ""),
            role=str(character.name or ""), actor_name=str(character.actor_name or ""),
            also_in=[row["book_title"] for row in cycle_changed],
        )
    elif cycle_changed:
        # Здесь актёр уже стоял, но цикл поставил его в другие книги — о них и письмо.
        approval = notify(
            db, book_id=cycle_changed[0]["book_id"], book_title=cycle_changed[0]["book_title"],
            role=str(character.name or ""), actor_name=str(character.actor_name or ""),
            also_in=[row["book_title"] for row in cycle_changed[1:]],
        )
    # Карточка диктора читает свои роли из агрегата: пересобрать все книги,
    # которых коснулось назначение, — эту и те, куда оно ушло по циклу.
    for touched_book in {str(character.book_id), *[row["book_id"] for row in cycle_changed]}:
        rebuild_assignments(db, touched_book)
    return {"vote": vote, "cycle": cycle, "approval": approval}

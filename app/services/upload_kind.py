"""Проба это или дубль — решает система, а не галочка в форме.

Галочку дикторы ставили неверно, и это не их вина: они не обязаны помнить, на какие
роли утверждены. Система обязана — она же их и утверждала.

Правило одно: диктор записывает роль, которая за ним, — это дубль. Записывает любую
другую — проба, что бы он ни выбрал в форме. Роль считается его и тогда, когда он
читал этого персонажа в другой книге: голос закреплён за персонажем, а не за книгой.

Предложенный агентом («Натали Ким?») не утверждён, и его запись — проба. Это тот самый
случай, ради которого роль агента и заведена: предложить актрису, принести её пробу,
дать владельцу выбрать. Прочтись предложение как утверждение — проба уйдёт дублем: без
главы её отвергнет `app/api/recording.py` (`chapter_required`), с главой она ляжет
готовым дублем и в лист проб не попадёт вовсе.
"""
from __future__ import annotations

from app.models import Character
from app.services.audio_uploads import AUDITION, TAKE
from app.services.author_profile import normalize_name
from app.services.telegram import names_match
from app.v2.cast_ops import approved_actor


def is_approved_for_role(db, *, role: str, actor_name: str) -> bool:
    """Утверждён ли этот человек на эту роль — в любой книге студии."""
    wanted = normalize_name(str(role or "").strip())
    reader = str(actor_name or "").strip()
    if not wanted or not reader:
        return False
    rows = db.query(Character.name, Character.actor_name).filter(Character.actor_name != "").all()
    for name, actor in rows:
        if normalize_name(str(name or "")) != wanted:
            continue
        # Предварительное имя `approved_actor` возвращает пустым — сравнивать нечего.
        approved = approved_actor(str(actor or ""))
        if approved and names_match(approved, reader):
            return True
    return False


def classify_upload_kind(db, *, role: str, actor_name: str) -> str:
    return TAKE if is_approved_for_role(db, role=role, actor_name=actor_name) else AUDITION

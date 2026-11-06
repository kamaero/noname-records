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

from app.models import Character, ScriptBook
from app.seat import one_seat
from app.services.audio_naming import book_token
from app.services.audio_uploads import AUDITION, TAKE, derive_book_code
from app.services.author_profile import normalize_name
from app.services.telegram import names_match
from app.v2.cast_ops import approved_actor, split_aliases


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


def _book_verdict(db, *, book_code: str, role: str, actor_name: str) -> bool | None:
    """Что говорит о роли сама книга загрузки: True — роль за ним, False — нет, None — молчит.

    Книга молчит, когда роли в ней нет или актёр не назначен вовсе: тогда и работает
    «голос за персонажем» — читала Ао в первой книге цикла, читает и в следующей.
    Предложение («Мелвис Моркен?») или чужое имя — это ответ книги, и он сильнее
    утверждения в другой книге. 10.10 Беккер прислала пробу на Ао в «Карте», где она
    только предложена; утверждение из «Семьи волшебников 3» сделало пробу дублем главы 5,
    и в лист проб она не попала.
    """
    wanted = normalize_name(str(role or "").strip())
    code = book_token(str(book_code or ""))
    if not wanted or not code:
        return None
    verdict = None
    for book in db.query(ScriptBook).all():
        if book_token(derive_book_code(str(book.title or ""))) != code:
            continue
        for character in db.query(Character).filter(Character.book_id == book.id).all():
            names = {normalize_name(item) for item in [str(character.name or ""), *split_aliases(character.aliases)] if item}
            if wanted not in names or not str(character.actor_name or "").strip():
                continue
            approved = approved_actor(str(character.actor_name or ""))
            if approved and names_match(approved, str(actor_name or "")):
                return True
            verdict = False
    return verdict


def classify_upload_kind(db, *, role: str, actor_name: str, book_code: str = "") -> str:
    # В «одном месте» проб нет: режиссёр сам раздал роли и грузит то, что ему прислали.
    # Сверка с кастом здесь только вредна — не утверждённый в касте актёр или рассказчик,
    # чей актёр записан в смете, превращали запись в пробу, а пробы там скрыты и сверку
    # не проходят. Одна точка на загрузку, подсказку имени и предпросмотр.
    if one_seat():
        return TAKE
    verdict = _book_verdict(db, book_code=book_code, role=role, actor_name=actor_name)
    if verdict is None:
        verdict = is_approved_for_role(db, role=role, actor_name=actor_name)
    return TAKE if verdict else AUDITION


def role_taken_by_other(db, *, book_code: str, role: str, actor_name: str) -> bool:
    """Роль этой книги утверждена за другим человеком — пробы на неё не принимаются.

    Решение владельца 05.10: Пухютпохов прислал пробу на Хдыбдяра, накрепко утверждённого
    за Приваловым, — форма предлагала весь каст, а сервер видел только «не твоя роль —
    значит проба». Предложение агента («Имя?») и свободная роль пробы принимают: ради
    них проба и существует. Роль узнаётся по регистру и псевдонимам, как везде.
    """
    wanted = normalize_name(str(role or "").strip())
    code = book_token(str(book_code or ""))
    if not wanted or not code:
        return False
    for book in db.query(ScriptBook).all():
        if book_token(derive_book_code(str(book.title or ""))) != code:
            continue
        for character in db.query(Character).filter(Character.book_id == book.id).all():
            names = {normalize_name(item) for item in [str(character.name or ""), *split_aliases(character.aliases)] if item}
            if wanted not in names:
                continue
            approved = approved_actor(str(character.actor_name or ""))
            if approved and not names_match(approved, str(actor_name or "")):
                return True
    return False


def role_taken_text(role: str) -> str:
    return f"роль «{role}» уже утверждена — пробы на неё не принимаются"

"""Раздел «Дикторы»: список, карточка и правки владельца над `dictor_*` и учётками.

Диктор — пользователь с ролью `dictor`; «Пользователи» — технический доступ, здесь —
творческий пульт (спека 2026-09-30, решение 5). Назначения (`dictor_assignments`) только
читаются: их пересчитывает `casting.rebuild_assignments`.

Переименование расходится по касту, профилю автора и рассказчику: актёр в них — строка
имени, и без этого роли отвалились бы от учётки, как только она сменила имя.
"""
from __future__ import annotations

import os
import tempfile
import uuid

from app.models import (
    AuthorCharacter, BookBudget, Character, DictorAssignment, DictorDemo, DictorLink, DictorProfile, Recast,
    ScriptBook, TelegramAuthAccount, User, UserRole,
)
from app.services import audio_storage
from app.services.telegram import names_match
from app.time_utils import iso_utc, utcnow_naive

NOTE_MAX = 4000


class DictorError(Exception):
    def __init__(self, code: str, detail=None):
        super().__init__(code)
        self.code = code
        self.detail = detail


def _fold(text: str) -> str:
    return str(text or "").lower().replace("ё", "е")


def _clean_name(name: str) -> str:
    return " ".join(str(name or "").split())[:120]


def _dictor_ids(db) -> set[str]:
    return {uid for (uid,) in db.query(UserRole.user_id).filter(UserRole.role == "dictor").all()}


def _telegram_of(db, user_ids) -> dict[str, str]:
    rows = db.query(TelegramAuthAccount).filter(TelegramAuthAccount.user_id.in_(list(user_ids))).all()
    return {row.user_id: str(row.telegram_user_id or "") for row in rows}


def _demo_row(demo: DictorDemo) -> dict:
    return {"id": demo.id, "title": str(demo.title or ""), "duration_seconds": float(demo.duration_seconds or 0)}


def _main_demo(profile: DictorProfile | None, demos: list[DictorDemo]) -> DictorDemo | None:
    """Выбранное звёздочкой; не выбрано или удалено — первое по дате."""
    chosen = str(getattr(profile, "main_demo_id", "") or "")
    for demo in demos:
        if demo.id == chosen:
            return demo
    return demos[0] if demos else None


def _demos_by_user(db, user_ids) -> dict[str, list[DictorDemo]]:
    out: dict[str, list[DictorDemo]] = {}
    for demo in (db.query(DictorDemo).filter(DictorDemo.user_id.in_(list(user_ids)))
                 .order_by(DictorDemo.created_at.asc(), DictorDemo.id.asc()).all()):
        out.setdefault(demo.user_id, []).append(demo)
    return out


def list_dictors(db) -> list[dict]:
    ids = _dictor_ids(db)
    if not ids:
        return []
    users = db.query(User).filter(User.id.in_(list(ids))).all()
    telegram = _telegram_of(db, ids)
    profiles = {p.user_id: p for p in db.query(DictorProfile).filter(DictorProfile.user_id.in_(list(ids))).all()}
    demos = _demos_by_user(db, ids)
    roles: dict[str, list[DictorAssignment]] = {}
    for row in db.query(DictorAssignment).filter(DictorAssignment.user_id.in_(list(ids))).all():
        roles.setdefault(row.user_id, []).append(row)

    items = []
    for user in users:
        profile = profiles.get(user.id)
        own = demos.get(user.id, [])
        main = _main_demo(profile, own)
        assigned = roles.get(user.id, [])
        items.append({
            "user_id": user.id,
            "name": str(user.display_name or ""),
            "telegram_user_id": telegram.get(user.id, ""),
            "username": str(getattr(profile, "telegram_username", "") or ""),
            "demos": len(own),
            "main_demo": _demo_row(main) if main else None,
            "roles": len(assigned),
            "books": len({row.book_id for row in assigned}),
            "note": str(getattr(profile, "note", "") or ""),
        })
    items.sort(key=lambda item: _fold(item["name"]))
    return items


def _dictor(db, user_id: str) -> User:
    user = db.get(User, str(user_id or "").strip())
    if user is None or user.id not in _dictor_ids(db):
        raise DictorError("not_found")
    return user


def dictor_card(db, user_id: str) -> dict:
    user = _dictor(db, user_id)
    name = str(user.display_name or "")
    profile = db.get(DictorProfile, user.id)
    demos = _demos_by_user(db, [user.id]).get(user.id, [])
    main = _main_demo(profile, demos)
    titles = {book.id: str(book.title or "") for book in db.query(ScriptBook).all()}
    from app.services import role_deadlines

    deadlines = {}
    for row in db.query(DictorAssignment).filter(DictorAssignment.user_id == user.id).all():
        if row.character_id:
            open_row = role_deadlines.open_deadline(db, row.character_id)
            if open_row is not None:
                deadlines[row.character_id] = role_deadlines.deadline_view(db, open_row)
    roles = sorted(
        ({"book_id": row.book_id, "book": titles.get(row.book_id, ""), "role": str(row.role_name or ""),
          "state": str(row.state or ""), "recorded": bool(row.recorded),
          "deadline": deadlines.get(row.character_id)}
         for row in db.query(DictorAssignment).filter(DictorAssignment.user_id == user.id).all()),
        key=lambda r: (_fold(r["book"]), _fold(r["role"])),
    )
    recasts = [
        {"role_name": r.role_name, "from_actor": r.from_actor, "to_actor": r.to_actor, "reason": r.reason,
         "comment": r.comment, "created_at": iso_utc(r.created_at)}
        for r in db.query(Recast).order_by(Recast.created_at.desc()).all()
        if names_match(str(r.from_actor or ""), name) or names_match(str(r.to_actor or ""), name)
    ]
    return {
        "user_id": user.id,
        "name": name,
        "login": str(user.login or ""),
        "telegram_user_id": _telegram_of(db, [user.id]).get(user.id, ""),
        "username": str(getattr(profile, "telegram_username", "") or ""),
        "note": str(getattr(profile, "note", "") or ""),
        "main_demo_id": main.id if main else "",
        "demos": [_demo_row(d) | {"trimmed": bool(d.trimmed), "source": str(d.source or ""),
                                  "created_at": iso_utc(d.created_at)} for d in demos],
        "links": [{"id": link.id, "url": link.url, "title": str(link.title or "")}
                  for link in db.query(DictorLink).filter(DictorLink.user_id == user.id).all()],
        "roles": roles,
        "recasts": recasts,
    }


def _profile(db, user_id: str) -> DictorProfile:
    profile = db.get(DictorProfile, user_id)
    if profile is None:
        profile = DictorProfile(user_id=user_id)
        db.add(profile)
    profile.updated_at = utcnow_naive()
    return profile


def set_note(db, user_id: str, note: str) -> None:
    user = _dictor(db, user_id)
    _profile(db, user.id).note = str(note or "").strip()[:NOTE_MAX]
    db.flush()


def set_main_demo(db, user_id: str, demo_id: str) -> None:
    user = _dictor(db, user_id)
    demo = db.get(DictorDemo, str(demo_id or ""))
    if demo is None or demo.user_id != user.id:
        raise DictorError("demo_not_found")
    _profile(db, user.id).main_demo_id = demo.id
    db.flush()


def delete_demo(db, demo_id: str) -> str:
    demo = db.get(DictorDemo, str(demo_id or ""))
    if demo is None:
        raise DictorError("demo_not_found")
    user_id = demo.user_id
    try:
        os.remove(audio_storage.resolve_path(demo.stored_key))
    except OSError:
        pass  # файла уже нет — строку всё равно убираем
    profile = db.get(DictorProfile, user_id)
    if profile is not None and profile.main_demo_id == demo.id:
        profile.main_demo_id = ""
    db.delete(demo)
    db.flush()
    return user_id


def add_demo(db, user_id: str, source_path: str, title: str) -> dict:
    """Любое аудио или видео → MP3 192 кбит/с, не длиннее двух минут (как импорт)."""
    from app.services import demo_audio as media

    user = _dictor(db, user_id)
    demo_id = str(uuid.uuid4())
    key = f"demos/{user.id}/{demo_id}.mp3"
    with tempfile.TemporaryDirectory() as tmp:
        target = os.path.join(tmp, "demo.mp3")
        info = media.to_demo_mp3(source_path, target)
        if db.query(DictorDemo.id).filter_by(user_id=user.id, md5=info["md5"]).first():
            raise DictorError("duplicate")
        with open(target, "rb") as handle:
            audio_storage.write_file(key, handle.read())
    demo = DictorDemo(id=demo_id, user_id=user.id, title=_clean_name(title) or "демо", stored_key=key,
                      duration_seconds=info["duration_seconds"], size_bytes=info["size_bytes"], md5=info["md5"],
                      trimmed=info["trimmed"], source="upload")
    db.add(demo)
    db.flush()
    return _demo_row(demo)


def _same_person(a: str, b: str) -> bool:
    """`names_match` в обе стороны: «Кира» входит в «Кира Иванова», но не наоборот."""
    return names_match(a, b) or names_match(b, a)


def _name_taken(db, name: str, *, except_id: str = "") -> bool:
    return any(
        _same_person(str(user.display_name or ""), name)
        for user in db.query(User).filter(User.is_active == "true", User.id != except_id).all()
    )


def create_dictor(db, name: str, telegram_user_id: str = "", username: str = "") -> dict:
    """Новый диктор. С Telegram — учётка `tg_<id>` без пароля, как у импорта и бота; без него —
    обычная учётка, пароль показывается один раз."""
    from app.services.login_identity import TELEGRAM_PASSWORD_HASH
    from app.services.user_admin import TELEGRAM_LOGIN_PREFIX, UserAdminError, attach_telegram, create_user

    clean = _clean_name(name)
    if not clean:
        raise DictorError("bad_name")
    tid = str(telegram_user_id or "").strip()
    if tid and not tid.isdigit():
        raise DictorError("bad_telegram_id")
    if _name_taken(db, clean):
        raise DictorError("name_taken")
    try:
        if tid:
            made = create_user(db, display_name=clean, roles=["dictor"], login=f"{TELEGRAM_LOGIN_PREFIX}{tid}",
                               password_hash=TELEGRAM_PASSWORD_HASH)
            attach_telegram(db, user_id=made["user"]["id"], telegram_user_id=tid)
        else:
            made = create_user(db, display_name=clean, roles=["dictor"])
    except UserAdminError as exc:
        raise DictorError(str(exc.args[0] if exc.args else exc)) from exc
    user_id = made["user"]["id"]
    _profile(db, user_id).telegram_username = str(username or "").strip().lstrip("@")[:64]
    db.flush()
    return {"user_id": user_id, "password": str(made.get("password") or "")}


def rename_dictor(db, user_id: str, new_name: str) -> dict:
    """Новое имя — всем местам, где актёр записан строкой имени, затем учётке и её записям.

    Порядок важен (ревью 01.10): вид записи (дубль или проба) пересчитывается по касту, и
    если записи переименовать раньше каста, дубли под новым именем станут «пробами».
    Переписываются только строки, которые сопоставление относит именно к этому диктору
    (`casting._dictor_for`): голое «Иван» при двух Иванах — не его, и чужую роль не трогаем.
    """
    from app.services import casting
    from app.services.user_admin import update_user
    from app.v2.cast_ops import parse_actor_name

    user = _dictor(db, user_id)
    new = _clean_name(new_name)
    if not new:
        raise DictorError("bad_name")
    if _name_taken(db, new, except_id=user.id):
        raise DictorError("name_taken")
    old = str(user.display_name or "")

    def renamed(raw: str) -> str | None:
        actor, tentative = parse_actor_name(raw)
        if not actor or casting._dictor_for(db, actor) != user.id:
            return None
        return new + ("?" if tentative else "")

    books: set[str] = set()
    counts = {"characters": 0, "profiles": 0, "narrators": 0}
    changes = []
    for ch in db.query(Character).filter(Character.actor_name != "").all():
        value = renamed(str(ch.actor_name or ""))
        if value is not None and value != ch.actor_name:
            changes.append((ch, "actor_name", value, "characters", ch.book_id))
    for profile in db.query(AuthorCharacter).filter(AuthorCharacter.actor_name != "").all():
        value = renamed(str(profile.actor_name or ""))
        if value is not None and value != profile.actor_name:
            changes.append((profile, "actor_name", value, "profiles", None))
    for budget in db.query(BookBudget).filter(BookBudget.narrator_actor_name != "").all():
        value = renamed(str(budget.narrator_actor_name or ""))
        if value is not None and value != budget.narrator_actor_name:
            changes.append((budget, "narrator_actor_name", value, "narrators", budget.book_id))
    # Сроки — вместе с именем: иначе сверка увидела бы «другого актёра», закрыла срок и
    # прислала переименованному письмо о рекасте (ревью сроков 01.10).
    from app.models import RoleDeadline
    from app.services.role_deadlines import same_person

    renamed_characters = {row.id for row, _, _, bucket, _ in changes if bucket == "characters"}
    for deadline in db.query(RoleDeadline).filter(RoleDeadline.character_id.in_(sorted(renamed_characters))).all():
        if same_person(deadline.actor_name, old):
            deadline.actor_name = new
    # Сопоставление считалось по старому имени учётки — менять строки только после обхода.
    for row, field, value, bucket, book_id in changes:
        setattr(row, field, value)
        counts[bucket] += 1
        if book_id:
            books.add(book_id)
    db.flush()
    update_user(db, user.id, display_name=new)  # записи — уже по новому касту
    db.flush()
    for book_id in sorted(books):
        casting.rebuild_assignments(db, book_id)
    return counts | {"books": sorted(books)}


def delete_dictor(db, user_id: str, *, actor_user_id: str) -> None:
    """С человеком больше не работаем: уходит учётка, вход через Telegram, карточка и демо.
    Пока за ним роли — отказ: их сперва переназначают (спека: удаление через рекаст)."""
    from app.services.user_admin import UserAdminError, delete_user

    user = _dictor(db, user_id)
    titles = {book.id: str(book.title or "") for book in db.query(ScriptBook).all()}
    roles = [{"book": titles.get(row.book_id, ""), "role": str(row.role_name or "")}
             for row in db.query(DictorAssignment).filter(DictorAssignment.user_id == user.id).all()]
    from app.v2.cast_ops import parse_actor_name

    name = str(user.display_name or "")
    known = {(r["book"], r["role"]) for r in roles}

    def his(raw: str) -> bool:
        actor, _ = parse_actor_name(raw)
        return bool(actor) and _same_person(actor, name)

    # Агрегат строится только из однозначных имён и без профиля автора — смотрим и сам каст.
    for ch in db.query(Character).filter(Character.actor_name != "").all():
        if his(str(ch.actor_name or "")) and (titles.get(ch.book_id, ""), str(ch.name or "")) not in known:
            roles.append({"book": titles.get(ch.book_id, ""), "role": str(ch.name or "")})
            known.add((titles.get(ch.book_id, ""), str(ch.name or "")))
    for budget in db.query(BookBudget).filter(BookBudget.narrator_actor_name != "").all():
        if his(str(budget.narrator_actor_name or "")) and (titles.get(budget.book_id, ""), "Рассказчик") not in known:
            roles.append({"book": titles.get(budget.book_id, ""), "role": "Рассказчик"})
    for profile in db.query(AuthorCharacter).filter(AuthorCharacter.actor_name != "").all():
        if his(str(profile.actor_name or "")):
            roles.append({"book": "профиль автора", "role": str(profile.canonical_name or "")})
    if roles:
        raise DictorError("has_roles", roles)
    from app.config import settings
    from app.services.telegram_auth_bootstrap import parse_telegram_auth_whitelist

    env_ids = {e.telegram_user_id for e in parse_telegram_auth_whitelist(settings.telegram_auth_whitelist)}
    own_ids = {tid for (tid,) in db.query(TelegramAuthAccount.telegram_user_id)
               .filter(TelegramAuthAccount.user_id == user.id).all()}
    if env_ids & own_ids:
        # Стартовый скрипт вернул бы строку из .env, а с ней и человека (ревью 01.10).
        raise DictorError("env_whitelist", sorted(env_ids & own_ids))
    db.query(TelegramAuthAccount).filter(TelegramAuthAccount.user_id == user.id).delete(synchronize_session=False)
    try:
        delete_user(db, user.id, actor_user_id=actor_user_id)
    except UserAdminError as exc:
        raise DictorError(str(exc.args[0] if exc.args else exc)) from exc

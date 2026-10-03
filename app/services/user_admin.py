"""Accounts as an operation, not a scratch script.

The studio's 49 dictors were let in by hand: logins transliterated from their names,
passwords generated once, roles inserted straight into the table. This is the same
work with the guards the script did not have.

Two of them are the reason this module exists rather than a handful of endpoints:
the last administrator cannot be demoted or switched off, and nobody can delete
himself. Both mistakes end the same way — the owner locked out of his own studio,
with no screen left to undo it from.

Passwords are hashed with the same `pbkdf2_sha256` the login route uses. The context
is built here instead of imported from `app.main` (which imports half the app back)
and instead of being threaded through the handler-factory chain, where a dependency
has to be declared at four levels before it arrives. Hashes carry their scheme, so
either context verifies the other's.
"""
from __future__ import annotations

import os
import re
import secrets
import uuid
from datetime import datetime, timedelta

from app.models import (
    AuditLog,
    DictorAssignment,
    DictorDemo,
    DictorLink,
    DictorProfile,
    LlmUsageLog,
    OperatorIntervention,
    ScriptBook,
    TelegramAuthAccount,
    User,
    UserRole,
)
from app.services import passwords as _passwords
from app.services.asr_coverage import transliterate
from app.services.telegram import names_match
from app.time_utils import utcnow_naive


# «Ъ» and soft signs vanish in transliteration, so a login can only lose characters;
# what is left is lowercase latin, digits and the single dot between the name parts.
_LOGIN_SAFE = re.compile(r"[^a-z0-9.]+")
# Read aloud over the phone, so no l/1, O/0, I/i.
_PASSWORD_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
PASSWORD_LENGTH = 12
TELEGRAM_LOGIN_PREFIX = "tg_"
TELEGRAM_PASSWORD_HASH = "telegram-login"


class UserAdminError(Exception):
    """A refusal the screen can explain: `code` names which guard said no."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def generate_password(length: int = PASSWORD_LENGTH) -> str:
    return "".join(secrets.choice(_PASSWORD_ALPHABET) for _ in range(length))


def hash_password(password: str) -> str:
    return _passwords.hash_password(password)


def verify_password(password: str, password_hash: str) -> bool:
    # `telegram-login` and other non-hashes: not a password anyone can present — «нет»
    return _passwords.verify_password(password, password_hash)


def suggest_login(db, display_name: str) -> str:
    """«Александр Широков» → `aleksandr.shirokov`, the shape the studio already uses.

    A name already taken gets a number rather than a different shape, so the second
    Александр Широков is still recognisably himself.
    """
    parts = [part for part in transliterate(display_name).split(" ") if part]
    base = _LOGIN_SAFE.sub("", ".".join(parts)).strip(".") or "user"
    taken = {
        row[0]
        for row in db.query(User.login).filter(User.login.like(f"{base}%")).all()
    }
    if base not in taken:
        return base
    number = 2
    while f"{base}{number}" in taken:
        number += 1
    return f"{base}{number}"


def _roles_of(db, user_id: str) -> list[str]:
    return sorted(row.role for row in db.query(UserRole).filter(UserRole.user_id == user_id).all())


def _other_admins(db, user_id: str) -> int:
    """Administrators who are neither this account nor switched off."""
    rows = (
        db.query(UserRole.user_id)
        .filter(UserRole.role == "admin", UserRole.user_id != user_id)
        .distinct()
        .all()
    )
    ids = [row[0] for row in rows]
    if not ids:
        return 0
    return db.query(User).filter(User.id.in_(ids), User.is_active == "true").count()


def _row(db, user: User, twins: dict[str, tuple[str, str]] | None = None, linked: dict[str, str] | None = None) -> dict:
    """Одна строка списка учёток — и все пути, которыми в неё входят.

    Путей может быть два сразу, и это главное, что должен показывать экран: вопрос
    «узнает ли система меня» решается тем, что и пароль, и телеграм ведут в одну строку.
    Пока `auth` говорил одно слово, привязанный телеграм был не виден.
    """
    telegram_born = str(user.login or "").startswith(TELEGRAM_LOGIN_PREFIX)
    telegram_id = str((linked or {}).get(user.id, "") or "")
    twin_id, twin_login = (twins or {}).get(user.id, ("", ""))
    ways = ["telegram"] if telegram_born else ["password"]
    if telegram_id and not telegram_born:
        ways.append("telegram")
    return {
        "twin_id": twin_id,
        "twin_login": twin_login,
        "id": user.id,
        "login": user.login,
        "display_name": user.display_name or user.login,
        "is_active": str(user.is_active or "").lower() == "true",
        "roles": _roles_of(db, user.id),
        "auth": "telegram" if telegram_born else "password",
        "ways_in": ways,
        "telegram_user_id": telegram_id,
    }




def list_users(db) -> list[dict]:
    """Every account, with how its owner gets in and whether he has a second way.

    The twin is the part worth reading: a person who logs in through Telegram *and*
    with a password holds two rows here, and until they can be joined the screen at
    least says so rather than showing what looks like a duplicate.
    """
    users = db.query(User).order_by(User.display_name.asc(), User.login.asc()).all()
    # Telegram shows the name its owner typed there, the login carries the one the
    # studio wrote down: «Зарина Мельникова» against «Зарина Мельникова (Яковлева)».
    # Same rule as the cast uses to recognise an actor — two common tokens, order-free.
    twins: dict[str, tuple[str, str]] = {}
    for index, user in enumerate(users):
        for other in users[index + 1 :]:
            if user.id in twins or not names_match(user.display_name, other.display_name):
                continue
            twins[user.id] = (other.id, other.login)
            twins.setdefault(other.id, (user.id, user.login))
    linked = {
        str(user_id): str(telegram_user_id)
        for user_id, telegram_user_id in db.query(
            TelegramAuthAccount.user_id, TelegramAuthAccount.telegram_user_id
        ).filter(TelegramAuthAccount.user_id != "").all()
        if str(user_id or "").strip()
    }
    return [_row(db, user, twins, linked) for user in users]


def create_user(
    db,
    *,
    display_name: str,
    roles: list[str],
    login: str = "",
    password_hash: str = "",
) -> dict:
    """A new account. Returns the generated password once — it is never readable again.

    `login` and `password_hash` are for accounts that are not password accounts (the
    `tg_*` row Telegram login makes); left empty, both are generated.
    """
    name = (display_name or "").strip()
    if not name:
        raise UserAdminError("display_name_required")
    chosen_login = (login or "").strip() or suggest_login(db, name)
    if db.query(User).filter(User.login == chosen_login).first() is not None:
        raise UserAdminError("login_taken")

    password = "" if password_hash else generate_password()
    user = User(
        id=str(uuid.uuid4()),
        login=chosen_login,
        password_hash=password_hash or hash_password(password),
        display_name=name[:120],
        is_active="true",
    )
    db.add(user)
    db.flush()
    for role in dict.fromkeys(role for role in roles if str(role or "").strip()):
        db.add(UserRole(id=str(uuid.uuid4()), user_id=user.id, role=role))
    db.flush()
    return {"user": _row(db, user), "password": password}


def update_user(
    db,
    user_id: str,
    *,
    display_name: str | None = None,
    is_active: bool | None = None,
    roles: list[str] | None = None,
) -> dict:
    """Rename, switch on or off, replace the roles. The login never changes.

    A login is what someone typed into their password manager in September; renaming
    «Галинов» to «Галанов» is a correction of the name, not a new account.
    """
    user = db.get(User, str(user_id or "").strip())
    if user is None:
        raise UserAdminError("user_not_found")

    losing_admin = roles is not None and "admin" in _roles_of(db, user.id) and "admin" not in roles
    switching_off = is_active is False and str(user.is_active or "").lower() == "true"
    if (losing_admin or switching_off) and "admin" in _roles_of(db, user.id) and _other_admins(db, user.id) == 0:
        raise UserAdminError("last_admin")

    if display_name is not None:
        name = display_name.strip()
        if not name:
            raise UserAdminError("display_name_required")
        was_name = str(user.display_name or "")
        user.display_name = name[:120]
        if was_name and was_name != user.display_name:
            # Записи подписаны именем, каким оно было при загрузке; после переименования
            # они говорили бы о человеке, которого в системе больше нет.
            from app.services.audio_rename import rename_actor_in_audio

            rename_actor_in_audio(db, was=was_name, now=user.display_name)
    if is_active is not None:
        user.is_active = "true" if is_active else "false"
    if roles is not None:
        db.query(UserRole).filter(UserRole.user_id == user.id).delete(synchronize_session=False)
        for role in dict.fromkeys(role for role in roles if str(role or "").strip()):
            db.add(UserRole(id=str(uuid.uuid4()), user_id=user.id, role=role))
    db.flush()
    return _row(db, user)


def reset_password(db, user_id: str) -> str:
    """A fresh password, shown once. The old one stops working immediately."""
    user = db.get(User, str(user_id or "").strip())
    if user is None:
        raise UserAdminError("user_not_found")
    password = generate_password()
    user.password_hash = hash_password(password)
    db.flush()
    return password


def delete_user(db, user_id: str, *, actor_user_id: str) -> None:
    """Remove an account and its roles.

    Deleting a `tg_*` row does not remove the person: Telegram login recreates it on
    the next visit. Until the two identities can be joined, the screen says so — this
    only refuses the two deletions that cannot be undone from inside the app.
    """
    user = db.get(User, str(user_id or "").strip())
    if user is None:
        raise UserAdminError("user_not_found")
    if user.id == str(actor_user_id or "").strip():
        raise UserAdminError("self_delete")
    if "admin" in _roles_of(db, user.id) and _other_admins(db, user.id) == 0:
        raise UserAdminError("last_admin")

    db.query(UserRole).filter(UserRole.user_id == user.id).delete(synchronize_session=False)
    _drop_dictor_card(db, user.id)
    db.delete(user)
    db.flush()


def _drop_dictor_card(db, user_id: str) -> None:
    """Удалить учётку — значит больше не работать с человеком: из «Дикторов» он уходит
    весь. Файлы демо остаются в резервной копии; назначения на роли живут в касте
    (`Character.actor_name`) и удалением учётки не снимаются."""
    from app.services import audio_storage

    for demo in db.query(DictorDemo).filter(DictorDemo.user_id == user_id).all():
        try:
            os.remove(audio_storage.resolve_path(demo.stored_key))
        except OSError:
            pass
        db.delete(demo)
    for model in (DictorLink, DictorAssignment, DictorProfile):
        db.query(model).filter(model.user_id == user_id).delete(synchronize_session=False)


def _move_dictor_card(db, keep_id: str, drop_id: str) -> tuple[int, set[str]]:
    """Карточка диктора — к оставляемой учётке. Её поля главнее, пустые добираются из
    второй; демо с тем же звуком (md5) и ссылки с тем же адресом не удваиваются.
    Назначения — не перенос, а пересборка книг: они выводятся из каста по имени."""
    moved = 0
    drop_profile = db.get(DictorProfile, drop_id)
    if drop_profile is not None:
        keep_profile = db.get(DictorProfile, keep_id)
        if keep_profile is None:
            keep_profile = DictorProfile(user_id=keep_id)
            db.add(keep_profile)
        for field in ("telegram_username", "note", "main_demo_id"):
            if not getattr(keep_profile, field) and getattr(drop_profile, field):
                setattr(keep_profile, field, getattr(drop_profile, field))
        db.delete(drop_profile)
    known_md5 = {md5 for (md5,) in db.query(DictorDemo.md5).filter(DictorDemo.user_id == keep_id).all()}
    for demo in db.query(DictorDemo).filter(DictorDemo.user_id == drop_id).all():
        if demo.md5 and demo.md5 in known_md5:
            db.delete(demo)
            continue
        demo.user_id = keep_id
        known_md5.add(demo.md5)
        moved += 1
    known_urls = {url for (url,) in db.query(DictorLink.url).filter(DictorLink.user_id == keep_id).all()}
    for link in db.query(DictorLink).filter(DictorLink.user_id == drop_id).all():
        if link.url in known_urls:
            db.delete(link)
            continue
        link.user_id = keep_id
        known_urls.add(link.url)
    books = {book_id for (book_id,) in db.query(DictorAssignment.book_id)
             .filter(DictorAssignment.user_id.in_([keep_id, drop_id])).all()}
    db.query(DictorAssignment).filter(DictorAssignment.user_id == drop_id).delete(synchronize_session=False)
    db.flush()
    return moved, books


# Every place a user id is written down. A merge moves all of them, because the row
# that goes away answers «кто это сделал» for work that still exists — production
# already carries 19 references to accounts that are gone.
_HISTORY: tuple[tuple[type, str], ...] = (
    (AuditLog, "user_id"),
    (LlmUsageLog, "created_by_user_id"),
    (OperatorIntervention, "actor_user_id"),
    (ScriptBook, "created_by_user_id"),
)


def merge_accounts(db, *, keep_id: str, drop_id: str, actor_user_id: str) -> dict:
    """Two accounts of one person become one: the keeper.

    Everything the dropped account was — its Telegram identity, its roles, its trail
    through the logs — moves across before it is removed. The caller commits.
    """
    keep_id = str(keep_id or "").strip()
    drop_id = str(drop_id or "").strip()
    if keep_id and keep_id == drop_id:
        raise UserAdminError("same_account")
    keep = db.get(User, keep_id)
    drop = db.get(User, drop_id)
    if keep is None or drop is None:
        raise UserAdminError("user_not_found")
    # Dropping the account whose session is making the request would sign the operator
    # out mid-merge, into a uid that no longer exists.
    if drop.id == str(actor_user_id or "").strip():
        raise UserAdminError("self_delete")

    moved = {"telegram": 0, "roles": 0, "history": 0, "audio": 0}

    for row in db.query(TelegramAuthAccount).filter(TelegramAuthAccount.user_id == drop.id).all():
        row.user_id = keep.id
        moved["telegram"] += 1

    kept_roles = {row.role for row in db.query(UserRole).filter(UserRole.user_id == keep.id).all()}
    for row in db.query(UserRole).filter(UserRole.user_id == drop.id).all():
        if row.role in kept_roles:
            db.delete(row)
            continue
        row.user_id = keep.id
        kept_roles.add(row.role)
        moved["roles"] += 1

    for model, column in _HISTORY:
        moved["history"] += (
            db.query(model)
            .filter(getattr(model, column) == drop.id)
            .update({column: keep.id}, synchronize_session=False)
        )

    # Имя актёра в записях — это имя учётки на момент загрузки. Оно должно догнать
    # человека, иначе его же дубли останутся лежать пробами: вид записи решается именем.
    from app.services.audio_rename import rename_actor_in_audio

    moved["audio"] = rename_actor_in_audio(db, was=str(drop.display_name or ""), now=str(keep.display_name or ""))

    moved["dictor_demos"], touched_books = _move_dictor_card(db, keep.id, drop.id)

    dropped_login = drop.login
    db.delete(drop)
    db.flush()
    from app.services.casting import rebuild_assignments

    for book_id in sorted(touched_books):
        rebuild_assignments(db, book_id)
    return {"user": _row(db, keep), "dropped_login": dropped_login, "moved": moved}


def accounts_without_password(db) -> list[User]:
    """Живые учётки, в которые нельзя войти паролем.

    Так выглядят учётки, рождённые входом через телеграм: пароля им никто не выдавал,
    вместо хеша стоит заглушка. Выключенные не в счёт — их выключили нарочно.
    """
    from app.services.login_identity import TELEGRAM_PASSWORD_HASH

    return [
        user
        for user in db.query(User).order_by(User.display_name.asc()).all()
        if str(user.is_active or "").lower() == "true" and str(user.password_hash or "") == TELEGRAM_PASSWORD_HASH
    ]


def issue_passwords(db, user_ids: list[str]) -> list[dict]:
    """Выдать пароль тем из них, у кого его нет. Показывается один раз — здесь.

    Тем, у кого пароль есть, новый не выдаётся: восемнадцать человек входят паролем, и
    «выдать всем новые» означало бы запереть их снаружи. Вызывающий фиксирует.
    """
    from app.services.login_identity import TELEGRAM_PASSWORD_HASH

    issued: list[dict] = []
    for user_id in user_ids or []:
        user = db.get(User, str(user_id or "").strip())
        if user is None or str(user.password_hash or "") != TELEGRAM_PASSWORD_HASH:
            continue
        password = generate_password()
        user.password_hash = hash_password(password)
        user.updated_at = utcnow_naive()
        issued.append({
            "id": str(user.id),
            "login": str(user.login or ""),
            "display_name": str(user.display_name or ""),
            "password": password,
        })
    return issued


#: Пароль, выданный кнопкой в боте. Журнал хранит факт выдачи — без самого пароля — и
#: по нему же считается лимит: таблица уже есть, и «кто, когда» видно на экране журнала.
BOT_PASSWORD_ACTION = "password_issued_by_bot"
BOT_PASSWORD_LIMIT_PER_HOUR = 5


def bot_passwords_issued_last_hour(db, user_id: str, *, now: datetime) -> int:
    return (
        db.query(AuditLog)
        .filter(AuditLog.user_id == user_id, AuditLog.action == BOT_PASSWORD_ACTION,
                AuditLog.created_at > now - timedelta(hours=1))
        .count()
    )


def issue_password_by_bot(db, user_id: str, *, now: datetime) -> dict:
    """Новый пароль по кнопке в боте — и тем, у кого его не было, и взамен старого.

    В отличие от `issue_passwords` старый пароль заменяется: человек сам подтвердил это
    в боте, а Telegram подписал нажатие его id. Вызывающий проверяет, что учётка живая и
    не админская, и коммитит.
    """
    user = db.get(User, user_id)
    password = generate_password()
    user.password_hash = hash_password(password)
    user.updated_at = now
    db.add(AuditLog(user_id=user.id, entity_type="user", entity_id=user.id,
                    action=BOT_PASSWORD_ACTION, payload_json="{}", created_at=now))
    db.flush()
    return {"login": str(user.login or ""), "password": password}


def export_roster(db, *, issued: list[dict] | None = None) -> str:
    """Реестр доступов текстом: кто, под каким логином и какими путями входит.

    Паролей здесь нет и быть не может: они хранятся односторонними хешами, и с момента
    выдачи их не видел никто, включая нас. Это не недоделка, а то, ради чего хеши и
    заведены — украденная база не отдаёт ничьих паролей.

    Пароль попадает в файл единственным способом: если его выдали прямо сейчас и
    передали сюда в `issued`.
    """
    fresh = {str(row.get("id")): str(row.get("password") or "") for row in issued or []}
    linked = {
        str(user_id): str(telegram_user_id)
        for user_id, telegram_user_id in db.query(
            TelegramAuthAccount.user_id, TelegramAuthAccount.telegram_user_id
        ).filter(TelegramAuthAccount.user_id != "").all()
        if str(user_id or "").strip()
    }
    users = db.query(User).order_by(User.display_name.asc()).all()

    lines = [
        f"Доступы студии — {utcnow_naive().strftime('%d.%m.%Y %H:%M')}",
        f"Учёток: {len(users)}",
        "",
        "Паролей в этом файле нет: они хранятся односторонними хешами и не читаются",
        "обратно никем, включая систему. Пароль появляется здесь только в момент выдачи.",
        "Забытый пароль не восстанавливается — выдаётся новый на экране «Пользователи».",
        "",
        "имя\tлогин\tпароль\ttelegram\tроли\tвход",
    ]
    for user in users:
        telegram = linked.get(str(user.id), "")
        ways = []
        if str(user.password_hash or "") != "telegram-login":
            ways.append("пароль")
        if telegram:
            ways.append("telegram")
        password = fresh.get(str(user.id)) or ("— выдать на экране" if not ways or ways == ["telegram"] else "— задан ранее")
        state = "" if str(user.is_active or "").lower() == "true" else "  [выключена]"
        lines.append(
            f"{user.display_name or user.login}\t{user.login}\t{password}\t{telegram or '—'}\t"
            f"{','.join(_roles_of(db, user.id)) or '—'}\t{'+'.join(ways) or '—'}{state}"
        )
    if fresh:
        lines += ["", f"Новых паролей выдано: {len(fresh)}. Другого случая их увидеть не будет."]
    return "\n".join(lines) + "\n"


def attach_account_to_whitelist(db, row: TelegramAuthAccount) -> dict | None:
    """Завести учётку для новой телеграм-записи — или привязать уже существующую.

    Человека вносят один раз, а не дважды: назвал имя и телеграм — получил и вход по
    логину. Если такой человек в студии уже есть, привязываемся к нему: плодить вторую
    учётку тем же способом, каким они и плодились, было бы странно.

    Возвращает логин и пароль — если завели новую. `None` — если привязались к своей.
    """
    name = str(row.display_name or "").strip()
    if not name:
        return None

    same = [
        user
        for user in db.query(User).filter(User.is_active == "true").all()
        if names_match(str(user.display_name or ""), name)
    ]
    if len(same) == 1:
        row.user_id = same[0].id
        return None
    if len(same) > 1:
        # Двое подходят — значит не подходит никто: угадывать личность нельзя.
        return None

    made = create_user(db, display_name=name, roles=[str(row.role or "dictor").strip() or "dictor"])
    row.user_id = made["user"]["id"]
    return {"login": made["user"]["login"], "display_name": name, "password": made["password"]}


def attach_telegram(db, *, user_id: str, telegram_user_id: str) -> dict:
    """Привязать телеграм к этой учётке. Пустой айди — отвязать. Вызывающий фиксирует.

    Ответ берут у владельца, а не у сверки по именам: он смотрит на строку конкретного
    человека и уже знает, чей это телеграм. Сверка нужна там, где спросить некого.
    """
    user = db.get(User, str(user_id or "").strip())
    if user is None:
        raise UserAdminError("user_not_found")

    wanted = str(telegram_user_id or "").strip()
    if wanted and not wanted.isdigit():
        raise UserAdminError("bad_telegram_id")

    if not wanted:
        for row in db.query(TelegramAuthAccount).filter(TelegramAuthAccount.user_id == user.id).all():
            # Запись остаётся: вход через неё разрешён, просто чей — снова вопрос.
            row.user_id = ""
        return {"telegram_user_id": ""}

    row = db.query(TelegramAuthAccount).filter(TelegramAuthAccount.telegram_user_id == wanted).one_or_none()
    if row is not None and str(row.user_id or "").strip() and row.user_id != user.id:
        raise UserAdminError("telegram_taken")

    # У человека один телеграм: второй — это смена, а не добавка.
    for old in db.query(TelegramAuthAccount).filter(TelegramAuthAccount.user_id == user.id).all():
        if old.telegram_user_id != wanted:
            old.user_id = ""

    if row is None:
        row = TelegramAuthAccount(
            telegram_user_id=wanted,
            role=(_roles_of(db, user.id) or ["dictor"])[0],
            access_scope="full",
            display_name=str(user.display_name or ""),
            is_active="true",
        )
        db.add(row)
    row.user_id = user.id
    row.display_name = str(user.display_name or row.display_name or "")
    db.flush()
    return {"telegram_user_id": wanted, "display_name": row.display_name, "role": row.role}


def whitelist_rows(db) -> list[dict]:
    """Записи whitelist — и к чьей учётке каждая привязана.

    Без этого запись выглядит ничьей: имя, айди и роль ничего не говорят о том, дошла ли
    привязка. А именно она и решает, узнает ли система человека при входе.
    """
    users = {str(user.id): user for user in db.query(User).all()}
    rows = (
        db.query(TelegramAuthAccount)
        .order_by(TelegramAuthAccount.display_name.asc(), TelegramAuthAccount.telegram_user_id.asc())
        .all()
    )
    out = []
    for item in rows:
        owner = users.get(str(item.user_id or ""))
        out.append({
            "id": str(item.id),
            "telegram_user_id": str(item.telegram_user_id or ""),
            "display_name": str(item.display_name or item.telegram_user_id or ""),
            "role": str(item.role or ""),
            "access_scope": str(item.access_scope or ""),
            "is_active": str(item.is_active or "").lower() == "true",
            "linked_login": str(owner.login or "") if owner else "",
            "linked_name": str(owner.display_name or "") if owner else "",
        })
    return out

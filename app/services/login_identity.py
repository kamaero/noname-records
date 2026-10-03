"""Which account a login lands in, and whether it may land at all.

Two rules live here, and they are the same rule read from two ends.

The whitelist decides **whether** a Telegram identity may come in. The account decides
**who that person is**. Telegram login used to blur this: it re-applied the whitelist's
role on every visit, so an account moved from `author` to `dictor` reverted at the
next login — and all 25 whitelist rows say `author`, which is the right to edit a
book's markup. The role now seeds an account being created and is never imposed on one
that already exists.

The link is what makes a person one person. Resolution used to go through the synthetic
login `tg_<id>`, so anyone who also had a password owned two unconnected rows, and
deleting either did not merge them — the Telegram one returned on the next visit.
`telegram_auth_accounts.user_id` connects them; a login that still finds the old shape
repairs the link on its way through, so nobody has to be migrated by hand.
"""
from __future__ import annotations

import uuid

from app.models import TelegramAuthAccount, User, UserRole
from app.services.telegram import names_match

TELEGRAM_LOGIN_PREFIX = "tg_"
TELEGRAM_PASSWORD_HASH = "telegram-login"


def account_can_sign_in(user: User | None) -> bool:
    """An account switched off on the accounts screen cannot be signed into.

    The password route used to check the password and nothing else, so «Выключить»
    set a flag that nothing read.
    """
    return user is not None and str(user.is_active or "").lower() == "true"


def _has_roles(db, user_id: str) -> bool:
    return db.query(UserRole).filter(UserRole.user_id == user_id).first() is not None


def resolve_telegram_user(
    db,
    *,
    telegram_user_id: str,
    account: TelegramAuthAccount,
    display_name: str = "",
) -> User:
    """The account behind a whitelisted Telegram identity, creating it the first time.

    Четыре пути, по порядку: связь; исторический логин `tg_<id>`; учётка того же
    человека, найденная по имени; и только потом новая.

    Третий появился не сразу, и без него копились дубли: у диктора есть учётка с
    паролем, whitelist знает его телеграм, связи между ними нет — и первый же вход
    заводит вторую учётку. Тринадцать человек стояли в одном входе от этого.

    Совпадение по имени — тем же `names_match`, что и в касте. Если подходит несколько,
    не привязываемся ни к кому: угадывать личность нельзя, пусть решает человек на
    экране учёток. The caller commits.
    """
    telegram_user_id = str(telegram_user_id or "").strip()
    linked_id = str(getattr(account, "user_id", "") or "").strip()
    user = db.get(User, linked_id) if linked_id else None

    if user is None:
        legacy_login = f"{TELEGRAM_LOGIN_PREFIX}{telegram_user_id}"
        user = db.query(User).filter(User.login == legacy_login).first()

    if user is None:
        user = _account_of_the_same_person(db, display_name or account.display_name)

    created = user is None
    if created:
        name = (display_name or account.display_name or f"{TELEGRAM_LOGIN_PREFIX}{telegram_user_id}").strip()
        user = User(
            id=str(uuid.uuid4()),
            login=f"{TELEGRAM_LOGIN_PREFIX}{telegram_user_id}",
            password_hash=TELEGRAM_PASSWORD_HASH,
            display_name=name[:120],
            is_active="true",
        )
        db.add(user)
        db.flush()

    # The whitelist's role seeds a new account, and repairs one that has no roles at
    # all (a login with none is refused outright). It never overrides a decision the
    # accounts screen has made.
    if (created or not _has_roles(db, user.id)) and str(account.role or "").strip():
        db.add(UserRole(id=str(uuid.uuid4()), user_id=user.id, role=account.role))

    if linked_id != user.id:
        account.user_id = user.id
    db.flush()
    return user


def _account_of_the_same_person(db, name: str) -> User | None:
    """Живая учётка того же человека, найденная по имени. `None` — если её нет или их много.

    Выключенная учётка не в счёт: её выключили нарочно, и вход через телеграм не должен
    быть обходом этого решения.
    """
    wanted = str(name or "").strip()
    if not wanted:
        return None
    found = [
        user
        for user in db.query(User).filter(User.is_active == "true").all()
        if names_match(str(user.display_name or ""), wanted)
    ]
    return found[0] if len(found) == 1 else None

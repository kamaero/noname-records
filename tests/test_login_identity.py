"""Which account a login lands in, and whether it may land at all.

Two things were wrong once the accounts screen existed. Switching an account off set
a flag that the password route never read, so a disabled person kept signing in. And
Telegram login re-added the whitelist's role on every visit, so changing someone from
`author` to `dictor` lasted until their next login — all 25 whitelist rows say
`author`, and `author` is the right to edit a book's markup.

Behind both sits the same confusion: the whitelist decides *whether* someone may come
in; the account decides *who they are*. This module keeps that line. The whitelist's
role seeds an account that does not exist yet and is never imposed on one that does.

The link is what makes a person one person. Telegram login used to resolve a user by
the synthetic login `tg_<id>`, so anyone who also had a password owned two rows that
nothing connected. `telegram_auth_accounts.user_id` connects them, and a login that
finds the old shape repairs it on the way through.
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import TelegramAuthAccount, User, UserRole
from app.services.login_identity import account_can_sign_in, resolve_telegram_user
from app.services.user_admin import create_user


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with sessionmaker(bind=engine, autocommit=False, autoflush=False)() as session:
        yield session


def _whitelist(db, telegram_user_id: str, *, role: str = "author", display_name: str = "Елена Breeze") -> TelegramAuthAccount:
    row = TelegramAuthAccount(
        telegram_user_id=telegram_user_id,
        role=role,
        access_scope="full",
        display_name=display_name,
        is_active="true",
    )
    db.add(row)
    db.flush()
    return row


def _roles(db, user_id: str) -> list[str]:
    return sorted(row.role for row in db.query(UserRole).filter(UserRole.user_id == user_id).all())


class TestCanSignIn:
    def test_a_switched_off_account_may_not_sign_in(self, db):
        """«Выключить» on the accounts screen has to mean something."""
        made = create_user(db, display_name="Архип Дорохов", roles=["dictor"])
        db.commit()
        user = db.get(User, made["user"]["id"])
        user.is_active = "false"

        assert not account_can_sign_in(user)

    def test_an_active_account_may(self, db):
        made = create_user(db, display_name="Архип Дорохов", roles=["dictor"])
        db.commit()

        assert account_can_sign_in(db.get(User, made["user"]["id"]))

    def test_a_missing_account_may_not(self, db):
        assert not account_can_sign_in(None)


class TestTelegramResolution:
    def test_the_link_decides_where_a_telegram_login_lands(self, db):
        made = create_user(db, display_name="Елена Breeze", roles=["dictor"])
        db.commit()
        row = _whitelist(db, "900000101")
        row.user_id = made["user"]["id"]
        db.commit()

        user = resolve_telegram_user(db, telegram_user_id="900000101", account=row, display_name="Elena")

        assert user.id == made["user"]["id"]
        assert user.login == "elena.breeze"

    def test_an_unlinked_row_still_finds_the_old_synthetic_login_and_repairs_it(self, db):
        """Everyone who logged in before this change owns a `tg_<id>` row; keep them."""
        legacy = create_user(db, display_name="Елена Breeze", login="tg_900000101",
                             password_hash="telegram-login", roles=["author"])["user"]
        row = _whitelist(db, "900000101")
        db.commit()

        user = resolve_telegram_user(db, telegram_user_id="900000101", account=row, display_name="Elena")
        db.commit()

        assert user.id == legacy["id"]
        assert row.user_id == legacy["id"], "проход по старому пути должен проставить связь"

    def test_a_first_time_visitor_gets_an_account_seeded_with_the_whitelist_role(self, db):
        row = _whitelist(db, "999", role="dictor", display_name="Новый Диктор")
        db.commit()

        user = resolve_telegram_user(db, telegram_user_id="999", account=row, display_name="Новый Диктор")
        db.commit()

        assert user.login == "tg_999"
        assert _roles(db, user.id) == ["dictor"]
        assert row.user_id == user.id

    def test_the_whitelist_role_is_not_imposed_on_an_account_that_already_exists(self, db):
        """Otherwise «сделать их дикторами» is undone at the next Telegram login."""
        made = create_user(db, display_name="Елена Breeze", roles=["dictor"])
        db.commit()
        row = _whitelist(db, "900000101", role="author")
        row.user_id = made["user"]["id"]
        db.commit()

        resolve_telegram_user(db, telegram_user_id="900000101", account=row, display_name="Elena")
        db.commit()

        assert _roles(db, made["user"]["id"]) == ["dictor"]

    def test_an_account_with_no_roles_at_all_is_given_the_whitelist_one(self, db):
        """A login with no roles is refused outright, so an empty account is repaired."""
        made = create_user(db, display_name="Елена Breeze", roles=[])
        db.commit()
        row = _whitelist(db, "900000101", role="author")
        row.user_id = made["user"]["id"]
        db.commit()

        resolve_telegram_user(db, telegram_user_id="900000101", account=row, display_name="Elena")
        db.commit()

        assert _roles(db, made["user"]["id"]) == ["author"]


class TestNotMakingASecondAccountForTheSamePerson:
    """Вход через телеграм заводил новую учётку, даже когда человек в системе уже есть.

    Так и накопились дубли: у диктора учётка с паролем, whitelist знает его телеграм, а
    связи между ними нет — и первый же вход создаёт вторую учётку. Тринадцать человек
    стояли в одном входе от этого.

    Сверяем по имени тем же `names_match`, что и каст: «Роман Сомов» в учётке и
    «Сомов Роман» в whitelist — один человек. Если совпал ровно один — привязываемся к
    нему. Если несколько — не гадаем: пусть решает человек на экране учёток.
    """

    def test_an_existing_account_is_linked_instead_of_duplicated(self, db):
        made = create_user(db, display_name="Роман Сомов", roles=["dictor"])["user"]
        row = _whitelist(db, "900000104", role="dictor", display_name="Сомов Роман")

        user = resolve_telegram_user(db, telegram_user_id="900000104", account=row, display_name="Сомов Роман")

        assert user.id == made["id"], "тот же человек, а не новая учётка"
        assert row.user_id == made["id"]
        assert db.query(User).count() == 1

    def test_a_stranger_still_gets_an_account(self, db):
        create_user(db, display_name="Роман Сомов", roles=["dictor"])
        row = _whitelist(db, "999", role="dictor", display_name="Совершенно Другой")

        user = resolve_telegram_user(db, telegram_user_id="999", account=row, display_name="Совершенно Другой")

        assert user.login == "tg_999"
        assert db.query(User).count() == 2

    def test_two_people_with_matching_names_are_not_guessed_between(self, db):
        """Двое подходят — значит не подходит никто: угадывать личность нельзя."""
        create_user(db, display_name="Иван Иванов", roles=["dictor"])
        create_user(db, display_name="Иван Иванов", roles=["dictor"])
        row = _whitelist(db, "777", role="dictor", display_name="Иванов Иван")

        user = resolve_telegram_user(db, telegram_user_id="777", account=row, display_name="Иванов Иван")

        assert user.login == "tg_777"
        assert db.query(User).count() == 3

    def test_a_switched_off_account_is_not_the_one_to_link_to(self, db):
        made = create_user(db, display_name="Роман Сомов", roles=["dictor"])["user"]
        db.get(User, made["id"]).is_active = "false"
        db.flush()
        row = _whitelist(db, "900000104", role="dictor", display_name="Сомов Роман")

        user = resolve_telegram_user(db, telegram_user_id="900000104", account=row, display_name="Сомов Роман")

        assert user.id != made["id"], "выключенную учётку выключили нарочно"

    def test_the_historical_login_still_wins_over_a_name_match(self, db):
        """Если `tg_<id>` уже есть, это он и есть — имена тут ни при чём."""
        create_user(db, display_name="Роман Сомов", roles=["dictor"])
        legacy = create_user(db, display_name="Сомов Роман", roles=["dictor"], login="tg_900000104")["user"]
        row = _whitelist(db, "900000104", role="dictor", display_name="Сомов Роман")

        user = resolve_telegram_user(db, telegram_user_id="900000104", account=row, display_name="Сомов Роман")

        assert user.id == legacy["id"]

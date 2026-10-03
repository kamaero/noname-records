"""Реестр доступов и выдача паролей тем, у кого их нет.

Выгрузить «все логины и пароли» нельзя, и это не ограничение, а устройство: пароли
хранятся односторонними хешами, поэтому утечка базы не отдаёт ничьи пароли. Из системы
можно достать логин, но не пароль — его никто, включая нас, больше не видел с момента
выдачи.

Поэтому выгрузка — это реестр: кто, под каким логином и какими путями входит. А пароль
появляется в файле только тогда, когда мы его прямо сейчас и выдали.
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import TelegramAuthAccount, User
from app.services.login_identity import TELEGRAM_PASSWORD_HASH
from app.services.user_admin import (
    accounts_without_password,
    create_user,
    export_roster,
    issue_passwords,
    verify_password,
)


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with sessionmaker(bind=engine, autocommit=False, autoflush=False)() as session:
        yield session


def _telegram_born(db, *, name, telegram_id, login):
    made = create_user(db, display_name=name, roles=["dictor"], login=login)["user"]
    user = db.get(User, made["id"])
    user.password_hash = TELEGRAM_PASSWORD_HASH
    db.add(TelegramAuthAccount(telegram_user_id=telegram_id, role="dictor",
                               display_name=name, is_active="true", user_id=user.id))
    db.flush()
    return user


class TestWhoHasNoPassword:
    def test_an_account_born_of_telegram_has_none(self, db):
        user = _telegram_born(db, name="Андрей Сазонов", telegram_id="900000105", login="tg_900000105")

        assert [u.id for u in accounts_without_password(db)] == [user.id]

    def test_an_account_made_on_the_screen_has_one(self, db):
        create_user(db, display_name="Роман Сомов", roles=["dictor"])

        assert accounts_without_password(db) == []

    def test_a_switched_off_account_is_not_offered_a_password(self, db):
        user = _telegram_born(db, name="Кто-то", telegram_id="1", login="tg_1")
        user.is_active = "false"
        db.flush()

        assert accounts_without_password(db) == []


class TestIssuingThem:
    def test_the_password_works_afterwards(self, db):
        user = _telegram_born(db, name="Андрей Сазонов", telegram_id="900000105", login="tg_900000105")

        issued = issue_passwords(db, [user.id])

        assert len(issued) == 1
        assert verify_password(issued[0]["password"], db.get(User, user.id).password_hash)

    def test_it_says_who_it_belongs_to(self, db):
        user = _telegram_born(db, name="Андрей Сазонов", telegram_id="900000105", login="tg_900000105")

        row = issue_passwords(db, [user.id])[0]

        assert row["login"] == "tg_900000105"
        assert row["display_name"] == "Андрей Сазонов"

    def test_an_account_that_already_has_one_is_left_alone(self, db):
        """Восемнадцать человек входят паролем; выдать им новые — запереть их снаружи."""
        made = create_user(db, display_name="Роман Сомов", roles=["dictor"])
        was = db.get(User, made["user"]["id"]).password_hash

        assert issue_passwords(db, [made["user"]["id"]]) == []
        assert db.get(User, made["user"]["id"]).password_hash == was


class TestTheRoster:
    def test_it_lists_the_login_and_both_ways_in(self, db):
        made = create_user(db, display_name="Анна Ольшанская", roles=["dictor"], login="anna.olshanskaya")
        db.add(TelegramAuthAccount(telegram_user_id="900000109", role="dictor",
                                   display_name="Анна Ольшанская", is_active="true",
                                   user_id=made["user"]["id"]))
        db.flush()

        text = export_roster(db)

        assert "anna.olshanskaya" in text
        assert "Анна Ольшанская" in text
        assert "900000109" in text

    def test_it_says_plainly_that_passwords_are_not_in_it(self, db):
        create_user(db, display_name="Роман Сомов", roles=["dictor"])

        text = export_roster(db)

        assert "парол" in text.lower()

    def test_it_never_carries_a_hash(self, db):
        made = create_user(db, display_name="Роман Сомов", roles=["dictor"])
        db.flush()

        text = export_roster(db)

        assert db.get(User, made["user"]["id"]).password_hash not in text

    def test_freshly_issued_passwords_can_be_put_into_it(self, db):
        user = _telegram_born(db, name="Андрей Сазонов", telegram_id="900000105", login="tg_900000105")
        issued = issue_passwords(db, [user.id])

        text = export_roster(db, issued=issued)

        assert issued[0]["password"] in text


class TestAddingSomebodyByTelegramAlone:
    """Человека вносят один раз: назвал имя и телеграм — получил и вход по логину."""

    def _row(self, name, telegram_id="999", role="dictor"):
        return TelegramAuthAccount(telegram_user_id=telegram_id, role=role,
                                   display_name=name, is_active="true")

    def test_a_new_person_gets_a_login_and_a_password(self, db):
        from app.services.user_admin import attach_account_to_whitelist

        row = self._row("Дмитрий Носов")
        db.add(row)
        db.flush()

        made = attach_account_to_whitelist(db, row)

        assert made["login"] and made["password"]
        assert row.user_id
        assert verify_password(made["password"], db.get(User, row.user_id).password_hash)

    def test_the_role_from_the_whitelist_is_the_one_he_gets(self, db):
        from app.services.user_admin import attach_account_to_whitelist, list_users

        row = self._row("Дмитрий Носов", role="author")
        db.add(row)
        db.flush()
        attach_account_to_whitelist(db, row)
        db.flush()

        assert next(r for r in list_users(db) if r["id"] == row.user_id)["roles"] == ["author"]

    def test_somebody_already_here_is_linked_not_duplicated(self, db):
        from app.services.user_admin import attach_account_to_whitelist

        made = create_user(db, display_name="Роман Сомов", roles=["dictor"])["user"]
        row = self._row("Сомов Роман")
        db.add(row)
        db.flush()

        assert attach_account_to_whitelist(db, row) is None
        assert row.user_id == made["id"]
        assert db.query(User).count() == 1

    def test_two_people_of_the_same_name_are_not_guessed_between(self, db):
        from app.services.user_admin import attach_account_to_whitelist

        create_user(db, display_name="Иван Иванов", roles=["dictor"])
        create_user(db, display_name="Иван Иванов", roles=["dictor"])
        row = self._row("Иванов Иван")
        db.add(row)
        db.flush()

        assert attach_account_to_whitelist(db, row) is None
        assert not row.user_id, "личность не угадывают — пусть решает человек"

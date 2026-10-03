"""Телеграм привязывается в строке человека, а не угадыванием по имени.

Форма whitelist вносит запись и ищет, кому она принадлежит, по имени. Это работает,
пока имена сходятся, — а расходятся они именно там, где это важнее всего. И главное:
когда владелец смотрит на строку конкретного человека, он уже знает ответ, и спрашивать
его у сверки нелепо.

Здесь ответ берут у него: вот эта учётка, вот этот телеграм.
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import TelegramAuthAccount, User
from app.services.user_admin import UserAdminError, attach_telegram, create_user


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with sessionmaker(bind=engine, autocommit=False, autoflush=False)() as session:
        yield session


@pytest.fixture()
def konstantin(db):
    return create_user(db, display_name="Литвиненко Константин", roles=["dictor"],
                       login="konstantin.litvinenko")["user"]


class TestGivingSomebodyATelegram:
    def test_a_new_id_is_written_down_and_linked(self, db, konstantin):
        attach_telegram(db, user_id=konstantin["id"], telegram_user_id="123456")

        row = db.query(TelegramAuthAccount).filter(TelegramAuthAccount.telegram_user_id == "123456").one()
        assert row.user_id == konstantin["id"]
        assert row.display_name == "Литвиненко Константин"

    def test_the_role_comes_from_the_account_not_from_a_guess(self, db):
        made = create_user(db, display_name="Александр Белозёров", roles=["author"])["user"]

        attach_telegram(db, user_id=made["id"], telegram_user_id="900000106")

        assert db.query(TelegramAuthAccount).one().role == "author"

    def test_an_existing_whitelist_row_is_pointed_at_him_not_duplicated(self, db, konstantin):
        """Запись уже внесли, а к кому — не знали. Теперь знают."""
        db.add(TelegramAuthAccount(telegram_user_id="123456", role="dictor",
                                   display_name="кто-то", is_active="true"))
        db.flush()

        attach_telegram(db, user_id=konstantin["id"], telegram_user_id="123456")

        rows = db.query(TelegramAuthAccount).all()
        assert len(rows) == 1
        assert rows[0].user_id == konstantin["id"]

    def test_giving_him_a_second_id_replaces_the_first(self, db, konstantin):
        """У человека один телеграм; второй — это смена, а не добавка."""
        attach_telegram(db, user_id=konstantin["id"], telegram_user_id="111")
        attach_telegram(db, user_id=konstantin["id"], telegram_user_id="222")

        linked = db.query(TelegramAuthAccount).filter(TelegramAuthAccount.user_id == konstantin["id"]).all()
        assert [row.telegram_user_id for row in linked] == ["222"]


class TestWhatItRefuses:
    def test_an_id_that_belongs_to_somebody_else(self, db, konstantin):
        other = create_user(db, display_name="Кто-то Другой", roles=["dictor"])["user"]
        attach_telegram(db, user_id=other["id"], telegram_user_id="123456")

        with pytest.raises(UserAdminError, match="telegram_taken"):
            attach_telegram(db, user_id=konstantin["id"], telegram_user_id="123456")

    def test_an_account_that_does_not_exist(self, db):
        with pytest.raises(UserAdminError, match="user_not_found"):
            attach_telegram(db, user_id="нет-такого", telegram_user_id="123456")

    def test_an_id_that_is_not_a_number(self, db, konstantin):
        with pytest.raises(UserAdminError, match="bad_telegram_id"):
            attach_telegram(db, user_id=konstantin["id"], telegram_user_id="не айди")


class TestTakingItAway:
    def test_an_empty_id_unlinks_without_deleting_the_row(self, db, konstantin):
        """Запись остаётся в whitelist: вход через неё разрешён, просто чей — заново вопрос."""
        attach_telegram(db, user_id=konstantin["id"], telegram_user_id="123456")

        attach_telegram(db, user_id=konstantin["id"], telegram_user_id="")

        row = db.query(TelegramAuthAccount).one()
        assert row.user_id == ""
        assert row.telegram_user_id == "123456"


class TestTheWhitelistSaysWhoseItIs:
    """Запись без указания владельца выглядит ничьей — и заставляет сомневаться, дошла ли.

    Внесённый айди попадал в whitelist правильно, но список показывал только имя, айди и
    роль. Понять по нему, привязана запись к учётке или висит сама по себе, было нельзя.
    """

    def test_a_linked_entry_names_the_account(self, db, konstantin):
        from app.services.user_admin import whitelist_rows

        attach_telegram(db, user_id=konstantin["id"], telegram_user_id="123456")

        row = whitelist_rows(db)[0]
        assert row["linked_login"] == "konstantin.litvinenko"
        assert row["linked_name"] == "Литвиненко Константин"

    def test_an_unlinked_entry_says_so_by_being_empty(self, db):
        from app.services.user_admin import whitelist_rows

        db.add(TelegramAuthAccount(telegram_user_id="999", role="dictor",
                                   display_name="Никому", is_active="true"))
        db.flush()

        row = whitelist_rows(db)[0]
        assert row["linked_login"] == "" and row["linked_name"] == ""

    def test_entries_come_in_a_readable_order(self, db, konstantin):
        from app.services.user_admin import whitelist_rows

        db.add(TelegramAuthAccount(telegram_user_id="1", role="dictor", display_name="Яков", is_active="true"))
        db.add(TelegramAuthAccount(telegram_user_id="2", role="dictor", display_name="Андрей", is_active="true"))
        db.flush()

        assert [r["display_name"] for r in whitelist_rows(db)] == ["Андрей", "Яков"]

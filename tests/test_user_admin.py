"""Managing the studio's accounts: the operations the owner used to ask me to run as SQL.

Forty-nine dictors were let into the system by hand — logins transliterated from their
names, passwords generated in a scratch script, roles set with an `insert`. That is
fine once and untenable as a habit: the owner should be able to rename someone who
typed their surname wrong, turn an account off, hand out a new password, without a
session with me in between.

Two guards matter more than the rest, because both mistakes lock the owner out of his
own studio: the last administrator cannot be demoted, and nobody can delete himself.
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import (
    AuditLog,
    DictorAssignment,
    DictorDemo,
    DictorLink,
    DictorProfile,
    OperatorIntervention,
    TelegramAuthAccount,
    User,
    UserRole,
)
from app.services.user_admin import (
    UserAdminError,
    create_user,
    delete_user,
    merge_accounts,
    list_users,
    reset_password,
    suggest_login,
    update_user,
    verify_password,
)


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with sessionmaker(bind=engine, autocommit=False, autoflush=False)() as session:
        yield session


def _owner(db) -> User:
    user = create_user(db, display_name="Max Ray", roles=["admin"])["user"]
    db.commit()
    return db.get(User, user["id"])


class TestLogin:
    def test_a_russian_name_becomes_the_login_the_studio_already_uses(self, db):
        """The 49 accounts handed out in September look like this; keep the shape."""
        assert suggest_login(db, "Александр Широков") == "aleksandr.shirokov"

    def test_latin_in_the_name_is_left_alone(self, db):
        assert suggest_login(db, "Елена Breeze") == "elena.breeze"

    def test_a_second_person_of_the_same_name_gets_a_login_of_his_own(self, db):
        create_user(db, display_name="Александр Широков", roles=["dictor"])
        db.commit()

        assert suggest_login(db, "Александр Широков") == "aleksandr.shirokov2"

    def test_a_one_word_name_still_makes_a_login(self, db):
        assert suggest_login(db, "Ворчалыч") == "vorchalych"


class TestCreate:
    def test_the_password_it_hands_out_is_the_password_that_works(self, db):
        made = create_user(db, display_name="Мирон Зарецкий", roles=["dictor"])
        db.commit()

        stored = db.get(User, made["user"]["id"])
        assert verify_password(made["password"], stored.password_hash)
        assert not verify_password("не тот пароль", stored.password_hash)

    def test_the_account_starts_active_and_with_the_role_it_was_given(self, db):
        made = create_user(db, display_name="Мирон Зарецкий", roles=["dictor"])
        db.commit()

        stored = db.get(User, made["user"]["id"])
        assert stored.is_active == "true"
        assert [row.role for row in db.query(UserRole).filter(UserRole.user_id == stored.id)] == ["dictor"]

    def test_an_account_with_no_name_is_refused(self, db):
        with pytest.raises(UserAdminError) as exc:
            create_user(db, display_name="   ", roles=["dictor"])
        assert exc.value.code == "display_name_required"


class TestUpdate:
    def test_a_misspelled_surname_can_be_fixed_without_changing_the_login(self, db):
        made = create_user(db, display_name="Михаил Галинов", roles=["dictor"])
        db.commit()

        update_user(db, made["user"]["id"], display_name="Михаил Галанов")
        db.commit()

        stored = db.get(User, made["user"]["id"])
        assert stored.display_name == "Михаил Галанов"
        assert stored.login == "mihail.galinov"

    def test_turning_an_account_off_leaves_it_in_place(self, db):
        made = create_user(db, display_name="Архип Дорохов", roles=["dictor"])
        db.commit()

        update_user(db, made["user"]["id"], is_active=False)
        db.commit()

        assert db.get(User, made["user"]["id"]).is_active == "false"

    def test_roles_are_replaced_wholesale_not_added_to(self, db):
        made = create_user(db, display_name="Архип Дорохов", roles=["author"])
        db.commit()

        update_user(db, made["user"]["id"], roles=["dictor"])
        db.commit()

        assert [row.role for row in db.query(UserRole).filter(UserRole.user_id == made["user"]["id"])] == ["dictor"]

    def test_the_last_administrator_cannot_be_demoted(self, db):
        owner = _owner(db)

        with pytest.raises(UserAdminError) as exc:
            update_user(db, owner.id, roles=["author"])

        assert exc.value.code == "last_admin"
        assert "admin" in [row.role for row in db.query(UserRole).filter(UserRole.user_id == owner.id)]

    def test_the_last_administrator_cannot_be_switched_off_either(self, db):
        owner = _owner(db)

        with pytest.raises(UserAdminError) as exc:
            update_user(db, owner.id, is_active=False)

        assert exc.value.code == "last_admin"

    def test_one_of_two_administrators_may_be_demoted(self, db):
        _owner(db)
        second = create_user(db, display_name="Второй Админ", roles=["admin"])["user"]
        db.commit()

        update_user(db, second["id"], roles=["author"])
        db.commit()

        assert [row.role for row in db.query(UserRole).filter(UserRole.user_id == second["id"])] == ["author"]


class TestPassword:
    def test_a_new_password_works_and_the_old_one_stops(self, db):
        made = create_user(db, display_name="Зарина Мельникова", roles=["dictor"])
        db.commit()
        old = made["password"]

        fresh = reset_password(db, made["user"]["id"])
        db.commit()

        stored = db.get(User, made["user"]["id"])
        assert verify_password(fresh, stored.password_hash)
        assert not verify_password(old, stored.password_hash)


class TestDelete:
    def test_deleting_an_account_takes_its_roles_with_it(self, db):
        owner = _owner(db)
        victim = create_user(db, display_name="Лишняя Учётка", roles=["author"])["user"]
        db.commit()

        delete_user(db, victim["id"], actor_user_id=owner.id)
        db.commit()

        assert db.get(User, victim["id"]) is None
        assert db.query(UserRole).filter(UserRole.user_id == victim["id"]).count() == 0

    def test_nobody_deletes_himself(self, db):
        owner = _owner(db)

        with pytest.raises(UserAdminError) as exc:
            delete_user(db, owner.id, actor_user_id=owner.id)

        assert exc.value.code == "self_delete"
        assert db.get(User, owner.id) is not None

    def test_the_last_administrator_survives_even_someone_else_deleting_him(self, db):
        owner = _owner(db)
        other = create_user(db, display_name="Кто-то Ещё", roles=["author"])["user"]
        db.commit()

        with pytest.raises(UserAdminError) as exc:
            delete_user(db, owner.id, actor_user_id=other["id"])

        assert exc.value.code == "last_admin"

    def test_an_unknown_account_is_not_found(self, db):
        with pytest.raises(UserAdminError) as exc:
            delete_user(db, "no-such-id", actor_user_id="whoever")
        assert exc.value.code == "user_not_found"


class TestList:
    def test_the_list_says_how_each_person_can_get_in(self, db):
        _owner(db)
        create_user(db, display_name="Max Ray", login="tg_900000100", password_hash="telegram-login", roles=["author"])
        db.commit()

        rows = {row["login"]: row for row in list_users(db)}

        assert rows["max.ray"]["auth"] == "password"
        assert rows["tg_900000100"]["auth"] == "telegram"

    def test_a_person_with_both_ways_in_is_pointed_at_his_other_account(self, db):
        _owner(db)
        create_user(db, display_name="Max Ray", login="tg_900000100", password_hash="telegram-login", roles=["author"])
        db.commit()

        rows = {row["login"]: row for row in list_users(db)}

        assert rows["tg_900000100"]["twin_login"] == "max.ray"
        assert rows["max.ray"]["twin_login"] == "tg_900000100"

    def test_the_twin_is_found_the_way_the_cast_finds_an_actor(self, db):
        """Telegram shows the name its owner typed there; the login carries the one I made.

        «Зарина Мельникова» and «Зарина Мельникова (Яковлева)» are one person, and so are
        «Пётр Ступников» and «Диктор Пётр Ступников». Exact string equality
        misses both; `names_match` — two common tokens, order-free — does not.
        """
        create_user(db, display_name="Зарина Мельникова", roles=["dictor"])
        create_user(db, display_name="Зарина Мельникова (Яковлева)", login="tg_900000108",
                    password_hash="telegram-login", roles=["author"])
        db.commit()

        rows = {row["login"]: row for row in list_users(db)}

        assert rows["tg_900000108"]["twin_login"] == "zarina.melnikova"
        assert rows["zarina.melnikova"]["twin_login"] == "tg_900000108"

    def test_two_different_people_are_not_twins(self, db):
        create_user(db, display_name="Антон Бондаренко", roles=["dictor"])
        create_user(db, display_name="Николай Бондаренко", roles=["dictor"])
        db.commit()

        rows = {row["login"]: row for row in list_users(db)}

        assert rows["anton.bondarenko"]["twin_login"] == ""
        assert rows["nikolay.bondarenko"]["twin_login"] == ""


class TestMerge:
    """One person, two accounts: joining them is the point of the Telegram link.

    A merge is not a delete. The row that goes away has a history — who approved a
    chapter, who spent the tokens — and 19 rows in production already point at
    accounts that no longer exist. Losing the answer to «кто это сделал» is the cost
    of merging carelessly, so everything the dropped account did moves to the keeper.
    """

    def _telegram(self, db, telegram_user_id: str, user_id: str) -> TelegramAuthAccount:
        row = TelegramAuthAccount(telegram_user_id=telegram_user_id, role="author", access_scope="full",
                                  display_name="Елена Breeze", is_active="true", user_id=user_id)
        db.add(row)
        db.flush()
        return row

    def _pair(self, db):
        keep = create_user(db, display_name="Елена Breeze", roles=["dictor"])["user"]
        drop = create_user(db, display_name="Елена Breeze", login="tg_900000101",
                           password_hash="telegram-login", roles=["author"])["user"]
        db.commit()
        return keep, drop

    def test_the_telegram_identity_follows_the_account_that_stays(self, db):
        keep, drop = self._pair(db)
        row = self._telegram(db, "900000101", drop["id"])
        db.commit()

        merge_accounts(db, keep_id=keep["id"], drop_id=drop["id"], actor_user_id="someone")
        db.commit()

        assert row.user_id == keep["id"]
        assert db.get(User, drop["id"]) is None

    def test_what_the_dropped_account_did_is_still_attributed_to_the_person(self, db):
        keep, drop = self._pair(db)
        db.add(AuditLog(id="a1", user_id=drop["id"], entity_type="script_book", entity_id="b1", action="approve"))
        db.add(OperatorIntervention(id="o1", actor_user_id=drop["id"], book_id="b1", action_type="relabel"))
        db.commit()

        merge_accounts(db, keep_id=keep["id"], drop_id=drop["id"], actor_user_id="someone")
        db.commit()

        assert db.get(AuditLog, "a1").user_id == keep["id"]
        assert db.get(OperatorIntervention, "o1").actor_user_id == keep["id"]

    def test_the_roles_of_both_end_up_on_the_one_that_stays(self, db):
        keep, drop = self._pair(db)

        merge_accounts(db, keep_id=keep["id"], drop_id=drop["id"], actor_user_id="someone")
        db.commit()

        assert sorted(row.role for row in db.query(UserRole).filter(UserRole.user_id == keep["id"])) == [
            "author", "dictor",
        ]
        assert db.query(UserRole).filter(UserRole.user_id == drop["id"]).count() == 0

    def test_an_account_cannot_be_merged_into_itself(self, db):
        keep, _drop = self._pair(db)

        with pytest.raises(UserAdminError) as exc:
            merge_accounts(db, keep_id=keep["id"], drop_id=keep["id"], actor_user_id="someone")

        assert exc.value.code == "same_account"

    def test_you_cannot_drop_the_account_you_are_signed_in_as(self, db):
        keep, drop = self._pair(db)

        with pytest.raises(UserAdminError) as exc:
            merge_accounts(db, keep_id=keep["id"], drop_id=drop["id"], actor_user_id=drop["id"])

        assert exc.value.code == "self_delete"
        assert db.get(User, drop["id"]) is not None

    def test_an_unknown_account_is_not_found(self, db):
        keep, _drop = self._pair(db)

        with pytest.raises(UserAdminError) as exc:
            merge_accounts(db, keep_id=keep["id"], drop_id="no-such-id", actor_user_id="someone")

        assert exc.value.code == "user_not_found"


class TestHowThePersonGetsIn:
    """Учётка перестала быть «или логин, или телеграм»: она может быть и тем, и другим.

    Пока `auth` говорил одно слово, привязанный телеграм был не виден: учётка с паролем
    выглядела как «только пароль», хотя человек уже входил через телеграм и попадал
    именно в неё. Экран должен показывать оба пути — вопрос «узнает ли система меня»
    решается тем, что оба ведут в одну строку.
    """

    def test_an_account_with_a_password_only(self, db):
        made = create_user(db, display_name="Роман Сомов", roles=["dictor"])["user"]
        db.commit()

        row = next(r for r in list_users(db) if r["id"] == made["id"])

        assert row["ways_in"] == ["password"]
        assert row["telegram_user_id"] == ""

    def test_an_account_reachable_both_ways(self, db):
        made = create_user(db, display_name="Роман Сомов", roles=["dictor"])["user"]
        db.add(TelegramAuthAccount(telegram_user_id="900000104", role="dictor",
                                   display_name="Сомов Роман", is_active="true", user_id=made["id"]))
        db.commit()

        row = next(r for r in list_users(db) if r["id"] == made["id"])

        assert row["ways_in"] == ["password", "telegram"]
        assert row["telegram_user_id"] == "900000104"

    def test_an_account_born_of_telegram(self, db):
        made = create_user(db, display_name="Кто-то", roles=["dictor"], login="tg_999")["user"]
        db.add(TelegramAuthAccount(telegram_user_id="999", role="dictor",
                                   display_name="Кто-то", is_active="true", user_id=made["id"]))
        db.commit()

        row = next(r for r in list_users(db) if r["id"] == made["id"])

        assert row["ways_in"] == ["telegram"]


def _demo(db, user_id, demo_id, md5):
    db.add(DictorDemo(id=demo_id, user_id=user_id, title="ведьма", stored_key=f"demos/{user_id}/{demo_id}.mp3",
                      duration_seconds=30, size_bytes=1, md5=md5, source="import"))


class TestDictorCard:
    """Учётка и карточка диктора — одно: слияние несёт карточку, удаление её убирает."""

    def test_a_merge_moves_the_card_demos_and_links_to_the_account_that_stays(self, db):
        keep = create_user(db, display_name="Кравченко Алёна", roles=["dictor"])["user"]
        drop = create_user(db, display_name="Кравченко Алёна", login="tg_42", password_hash="telegram-login",
                           roles=["dictor"])["user"]
        db.add(DictorProfile(user_id=keep["id"], note="", telegram_username=""))
        db.add(DictorProfile(user_id=drop["id"], note="ведьмы и старухи", telegram_username="lena_voice",
                             main_demo_id="d-drop"))
        _demo(db, keep["id"], "d-keep", "same")
        _demo(db, drop["id"], "d-twin", "same")
        _demo(db, drop["id"], "d-drop", "other")
        db.add(DictorLink(user_id=drop["id"], url="https://t.me/example_voice"))
        db.add(DictorAssignment(user_id=drop["id"], book_id="b1", character_id="c1", role_name="Ведьма",
                                state="approved"))
        db.commit()

        merge_accounts(db, keep_id=keep["id"], drop_id=drop["id"], actor_user_id="someone")
        db.commit()

        profile = db.get(DictorProfile, keep["id"])
        assert (profile.note, profile.telegram_username, profile.main_demo_id) == (
            "ведьмы и старухи", "lena_voice", "d-drop")
        assert db.get(DictorProfile, drop["id"]) is None
        assert sorted(d.id for d in db.query(DictorDemo).filter_by(user_id=keep["id"])) == ["d-drop", "d-keep"]
        assert db.query(DictorDemo).filter_by(user_id=drop["id"]).count() == 0
        assert db.query(DictorLink).filter_by(user_id=keep["id"]).count() == 1
        assert db.query(DictorAssignment).filter_by(user_id=drop["id"]).count() == 0

    def test_a_merge_gives_the_card_to_a_keeper_without_one(self, db):
        keep = create_user(db, display_name="Кравченко Алёна", roles=["dictor"])["user"]
        drop = create_user(db, display_name="Кравченко Алёна", login="tg_42", password_hash="telegram-login",
                           roles=["dictor"])["user"]
        db.add(DictorProfile(user_id=drop["id"], telegram_username="lena_voice"))
        db.commit()

        merge_accounts(db, keep_id=keep["id"], drop_id=drop["id"], actor_user_id="someone")
        db.commit()

        assert db.get(DictorProfile, keep["id"]).telegram_username == "lena_voice"

    def test_deleting_an_account_removes_the_dictor_from_the_section(self, db):
        owner = _owner(db)
        victim = create_user(db, display_name="Лишний Диктор", roles=["dictor"])["user"]
        db.add(DictorProfile(user_id=victim["id"]))
        _demo(db, victim["id"], "d1", "m")
        db.add(DictorLink(user_id=victim["id"], url="https://t.me/x"))
        db.add(DictorAssignment(user_id=victim["id"], book_id="b1", character_id="c1", role_name="Ведьма",
                                state="approved"))
        db.commit()

        delete_user(db, victim["id"], actor_user_id=owner.id)
        db.commit()

        for model in (DictorProfile, DictorDemo, DictorLink, DictorAssignment):
            assert db.query(model).filter_by(user_id=victim["id"]).count() == 0, model.__name__

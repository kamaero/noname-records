"""Сервис раздела «Дикторы»: список, карточка, правки владельца (план 2а)."""
import os
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import (
    AuthorCharacter, BookBudget, Character, DictorAssignment, DictorDemo, DictorLink, DictorProfile, Recast,
    ScriptBook, TelegramAuthAccount, User, UserRole,
)
from app.services import audio_storage, dictors
from app.services.dictors import DictorError


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, autoflush=False)() as session:
        yield session


def _user(db, uid, name, *, roles=("dictor",), tid=""):
    db.add(User(id=uid, login=uid, password_hash="x", display_name=name, is_active="true"))
    for role in roles:
        db.add(UserRole(user_id=uid, role=role))
    if tid:
        db.add(TelegramAuthAccount(telegram_user_id=tid, role=roles[0], access_scope="full", display_name=name,
                                   is_active="true", user_id=uid))
    db.commit()


def _book(db, bid, title):
    db.add(ScriptBook(id=bid, title=title, display_title=title, source_filename="x.docx", source_format="docx",
                      total_chars=0, author_sheets_x1000=0, chapter_count=1, has_chapters="true", status="processing"))
    db.commit()


def _demo(db, uid, did, title="демо", seconds=60.0, data=b"mp3"):
    key = f"demos/{uid}/{did}.mp3"
    audio_storage.write_file(key, data)
    db.add(DictorDemo(id=did, user_id=uid, title=title, stored_key=key, duration_seconds=seconds,
                      size_bytes=len(data), md5=did, source="import"))
    db.commit()
    return key


def test_the_list_holds_only_dictors_with_their_counts(db):
    _user(db, "u1", "Ветрова Ольга", tid="42")
    _user(db, "u2", "Админ Только", roles=("admin",))
    _user(db, "u3", "Ёлкин Пётр")
    _book(db, "b1", "Крылья")
    _book(db, "b2", "СВ2")
    _demo(db, "u1", "d1", "главное")
    _demo(db, "u1", "d2", "второе")
    db.add(DictorProfile(user_id="u1", telegram_username="olga", note="Тёплый тембр", main_demo_id="d1"))
    db.add(DictorAssignment(user_id="u1", book_id="b1", character_id="c1", role_name="Куйбу"))
    db.add(DictorAssignment(user_id="u1", book_id="b1", character_id="c2", role_name="Дгарнин"))
    db.add(DictorAssignment(user_id="u1", book_id="b2", character_id="c3", role_name="Куйбу"))
    db.commit()

    items = dictors.list_dictors(db)

    assert [i["name"] for i in items] == ["Ветрова Ольга", "Ёлкин Пётр"]
    olga = items[0]
    assert (olga["telegram_user_id"], olga["username"], olga["demos"], olga["roles"], olga["books"]) == \
        ("42", "olga", 2, 3, 2)
    assert olga["main_demo"]["id"] == "d1" and olga["note"] == "Тёплый тембр"
    assert items[1]["main_demo"] is None and items[1]["telegram_user_id"] == ""


def test_without_a_chosen_main_demo_the_first_one_plays(db):
    _user(db, "u1", "Ветрова Ольга")
    _demo(db, "u1", "d1")
    assert dictors.list_dictors(db)[0]["main_demo"]["id"] == "d1"


def test_the_card_carries_roles_links_and_recasts(db):
    _user(db, "u1", "Ветрова Ольга", tid="42")
    _book(db, "b1", "Крылья")
    _demo(db, "u1", "d1")
    db.add(DictorLink(user_id="u1", url="https://vk.com/example_voice", title="ВК"))
    db.add(DictorAssignment(user_id="u1", book_id="b1", character_id="c1", role_name="Куйбу", state="proposed",
                            recorded=True))
    db.add(Recast(role_name="Ульдат", from_actor="Ольга Ветрова", to_actor="Рябов Иван", reason="overlap"))
    db.add(Recast(role_name="Чужая", from_actor="Иванов Пётр", to_actor="Сидоров Олег", reason="left"))
    db.commit()

    card = dictors.dictor_card(db, "u1")

    assert card["roles"] == [{"book_id": "b1", "book": "Крылья", "role": "Куйбу", "state": "proposed",
                              "recorded": True, "deadline": None}]
    assert [link["url"] for link in card["links"]] == ["https://vk.com/example_voice"]
    assert [r["role_name"] for r in card["recasts"]] == ["Ульдат"]
    assert [d["id"] for d in card["demos"]] == ["d1"]
    with pytest.raises(DictorError) as missing:
        dictors.dictor_card(db, "nobody")
    assert missing.value.code == "not_found"


def test_note_and_main_demo(db):
    _user(db, "u1", "Ветрова Ольга")
    _user(db, "u2", "Другой Диктор")
    _demo(db, "u1", "d1")
    _demo(db, "u2", "d9")
    dictors.set_note(db, "u1", "  звонкая  ")
    dictors.set_main_demo(db, "u1", "d1")
    db.commit()
    profile = db.get(DictorProfile, "u1")
    assert (profile.note, profile.main_demo_id) == ("звонкая", "d1")
    with pytest.raises(DictorError) as foreign:
        dictors.set_main_demo(db, "u1", "d9")
    assert foreign.value.code == "demo_not_found"


def test_deleting_the_main_demo_removes_the_file_and_the_star(db):
    _user(db, "u1", "Ветрова Ольга")
    key = _demo(db, "u1", "d1")
    dictors.set_main_demo(db, "u1", "d1")
    assert dictors.delete_demo(db, "d1") == "u1"
    db.commit()
    assert db.get(DictorDemo, "d1") is None and db.get(DictorProfile, "u1").main_demo_id == ""
    assert not os.path.exists(audio_storage.resolve_path(key))


def test_an_uploaded_demo_is_converted_and_a_duplicate_refused(db, tmp_path, monkeypatch):
    _user(db, "u1", "Ветрова Ольга")
    source = tmp_path / "Демо Ветрова — сказка.wav"
    source.write_bytes(b"raw")

    def fake_mp3(src, target):
        with open(target, "wb") as handle:
            handle.write(b"converted")
        return {"duration_seconds": 42.0, "size_bytes": 9, "md5": "same", "trimmed": False}
    monkeypatch.setattr("app.services.demo_audio.to_demo_mp3", fake_mp3)

    demo = dictors.add_demo(db, "u1", str(source), "сказка")
    db.commit()
    stored = db.get(DictorDemo, demo["id"])
    assert (stored.title, stored.source, stored.duration_seconds) == ("сказка", "upload", 42.0)
    assert open(audio_storage.resolve_path(stored.stored_key), "rb").read() == b"converted"
    with pytest.raises(DictorError) as dup:
        dictors.add_demo(db, "u1", str(source), "ещё раз")
    assert dup.value.code == "duplicate"


def test_create_makes_a_dictor_and_refuses_a_taken_name(db):
    made = dictors.create_dictor(db, "Ветрова Ольга", telegram_user_id="42", username="@olga")
    db.commit()
    user = db.get(User, made["user_id"])
    assert user.display_name == "Ветрова Ольга" and made["password"] == ""
    assert db.query(TelegramAuthAccount).filter_by(telegram_user_id="42").one().user_id == user.id
    assert db.get(DictorProfile, user.id).telegram_username == "olga"
    no_tg = dictors.create_dictor(db, "Петров Пётр")
    assert len(no_tg["password"]) == 12
    with pytest.raises(DictorError) as taken:
        dictors.create_dictor(db, "Ольга Ветрова")
    assert taken.value.code == "name_taken"


def test_rename_follows_the_name_through_cast_profile_and_narrator(db, monkeypatch):
    _user(db, "u1", "Ветрова Ольга")
    _book(db, "b1", "Крылья")
    _book(db, "b2", "СВ2")
    db.add(Character(id="c1", book_id="b1", name="Куйбу", aliases="", character_color="", actor_name="Ветрова Ольга"))
    db.add(Character(id="c2", book_id="b2", name="Дгарнин", aliases="", character_color="", actor_name="Ольга Ветрова?"))
    db.add(Character(id="c3", book_id="b2", name="Чужая", aliases="", character_color="", actor_name="Ветров Олег"))
    db.add(AuthorCharacter(id="ac1", author_id="a", canonical_name="Куйбу", aliases="[]", actor_name="Ветрова Ольга"))
    db.add(BookBudget(book_id="b2", narrator_actor_name="Ветрова Ольга"))
    db.commit()
    rebuilt = []
    monkeypatch.setattr("app.services.casting.rebuild_assignments", lambda db, book_id: rebuilt.append(book_id) or 0)

    report = dictors.rename_dictor(db, "u1", "Ветрова-Лунная Ольга")
    db.commit()

    assert db.get(User, "u1").display_name == "Ветрова-Лунная Ольга"
    assert db.get(Character, "c1").actor_name == "Ветрова-Лунная Ольга"
    assert db.get(Character, "c2").actor_name == "Ветрова-Лунная Ольга?"
    assert db.get(Character, "c3").actor_name == "Ветров Олег"
    assert db.get(AuthorCharacter, "ac1").actor_name == "Ветрова-Лунная Ольга"
    assert db.query(BookBudget).filter_by(book_id="b2").one().narrator_actor_name == "Ветрова-Лунная Ольга"
    assert (report["characters"], report["profiles"], report["narrators"]) == (2, 1, 1)
    assert sorted(rebuilt) == ["b1", "b2"] and sorted(report["books"]) == ["b1", "b2"]


def test_rename_into_another_dictors_name_changes_nothing(db):
    _user(db, "u1", "Ветрова Ольга")
    _user(db, "u2", "Иванов Пётр")
    with pytest.raises(DictorError) as taken:
        dictors.rename_dictor(db, "u1", "Пётр Иванов")
    assert taken.value.code == "name_taken"
    assert db.get(User, "u1").display_name == "Ветрова Ольга"
    with pytest.raises(DictorError) as bad:
        dictors.rename_dictor(db, "u1", "   ")
    assert bad.value.code == "bad_name"


def test_delete_refuses_while_roles_remain_and_otherwise_removes_everything(db):
    _user(db, "admin", "Админ", roles=("admin",))
    _user(db, "u1", "Ветрова Ольга", tid="42")
    _book(db, "b1", "Крылья")
    db.add(DictorAssignment(user_id="u1", book_id="b1", character_id="c1", role_name="Куйбу"))
    db.commit()
    with pytest.raises(DictorError) as busy:
        dictors.delete_dictor(db, "u1", actor_user_id="admin")
    assert busy.value.code == "has_roles" and busy.value.detail == [{"book": "Крылья", "role": "Куйбу"}]

    db.query(DictorAssignment).delete()
    db.commit()
    dictors.delete_dictor(db, "u1", actor_user_id="admin")
    db.commit()
    assert db.get(User, "u1") is None
    assert db.query(TelegramAuthAccount).filter_by(telegram_user_id="42").count() == 0


def _take(db, aid, actor, role="Куйбу", location="nas"):
    from app.models import AudioFile
    db.add(AudioFile(id=aid, book_code="K", original_filename="t.wav", stored_key=f"k/{aid}.wav", mime_type="audio/wav",
                     size_bytes=1, chapter="1", role=role, actor_name=actor, kind="take", location=location,
                     canonical_filename=f"K_Ch01_Kuybu_{aid}.wav"))
    db.commit()


def test_rename_keeps_recorded_takes_as_takes(db):
    # Ревью 01.10: записи переименовывались раньше каста — дубли становились «пробами».
    from app.models import AudioFile
    _user(db, "u1", "Галинов Иван")
    _book(db, "b1", "K")
    db.add(Character(id="c1", book_id="b1", name="Куйбу", aliases="", character_color="", actor_name="Галинов Иван"))
    db.commit()
    _take(db, "a1", "Галинов Иван")
    dictors.rename_dictor(db, "u1", "Галанов Иван")
    db.commit()
    take = db.get(AudioFile, "a1")
    assert take.kind == "take" and take.actor_name == "Галанов Иван" and "proba" not in take.canonical_filename


def test_rename_leaves_an_ambiguous_cast_entry_alone(db):
    # Ревью 01.10: «Иван» в касте — не обязательно переименованный Иван.
    _user(db, "u1", "Галинов Иван")
    _user(db, "u2", "Петров Иван")
    _book(db, "b1", "K")
    db.add(Character(id="c1", book_id="b1", name="Куйбу", aliases="", character_color="", actor_name="Галинов Иван"))
    db.add(Character(id="c2", book_id="b1", name="Егбод", aliases="", character_color="", actor_name="Иван"))
    db.add(AuthorCharacter(id="ac2", author_id="a", canonical_name="Егбод", aliases="[]", actor_name="Иван"))
    db.commit()
    dictors.rename_dictor(db, "u1", "Галанов Иван")
    db.commit()
    assert db.get(Character, "c1").actor_name == "Галанов Иван"
    assert db.get(Character, "c2").actor_name == "Иван" and db.get(AuthorCharacter, "ac2").actor_name == "Иван"


def test_a_short_name_inside_a_longer_one_is_taken(db):
    # Ревью 01.10: «Кира» при живой «Кира Иванова» сделала бы обеих неразличимыми в касте.
    _user(db, "u1", "Кира Иванова")
    with pytest.raises(DictorError) as taken:
        dictors.create_dictor(db, "Кира")
    assert taken.value.code == "name_taken"


def test_a_dictor_only_on_an_author_profile_still_has_roles(db):
    _user(db, "admin", "Админ", roles=("admin",))
    _user(db, "u1", "Ветрова Ольга")
    db.add(AuthorCharacter(id="ac1", author_id="a", canonical_name="Куйбу", aliases="[]", actor_name="Ветрова Ольга"))
    db.commit()
    with pytest.raises(DictorError) as busy:
        dictors.delete_dictor(db, "u1", actor_user_id="admin")
    assert busy.value.code == "has_roles" and busy.value.detail == [{"book": "профиль автора", "role": "Куйбу"}]


def test_an_id_from_the_env_whitelist_cannot_be_deleted_here(db, monkeypatch):
    # Ревью 01.10: стартовый скрипт вернул бы строку из .env — и человека, с ролью автора.
    from app.config import settings
    monkeypatch.setattr(settings, "telegram_auth_whitelist", "42|Ветрова Ольга")
    _user(db, "admin", "Админ", roles=("admin",))
    _user(db, "u1", "Ветрова Ольга", tid="42")
    with pytest.raises(DictorError) as env:
        dictors.delete_dictor(db, "u1", actor_user_id="admin")
    assert env.value.code == "env_whitelist" and db.get(User, "u1") is not None

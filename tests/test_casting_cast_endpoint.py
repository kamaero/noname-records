# tests/test_casting_cast_endpoint.py
import json
from asyncio import run

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.budget_api import SaveBudgetCharacterPayload, build_budget_api_handlers
from app.db import Base
from app.models import BookBudget, Character, CharacterBudgetSnapshot, RoleVote, ScriptBook, ScriptChapter
from app.services.role_votes import cast_role_vote
from tests.casting_cycle import build_cycle


@pytest.fixture()
def cast_env():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    with SessionLocal() as db:
        build_cycle(db)
    who = {"uid": "u-author", "roles": {"author"}, "name": "Белозёров"}
    handlers = build_budget_api_handlers({
        "is_authenticated": lambda _r: True,
        "has_workspace_full_access": lambda _r: True,
        "has_any_role": lambda _r, roles: bool({"author"} & set(roles)),
        "is_agent": lambda _r: False,
        "session_payload": lambda _r: {"uid": who["uid"]},
        "session_roles": lambda _r: who["roles"],
        "session_display_name": lambda _r: who["name"],
        "SessionLocal": SessionLocal,
        "get_book": lambda db, book_id: db.get(ScriptBook, book_id),
        "build_character_style_maps": lambda _c: None,
        "format_dt": lambda v: str(v) if v else None,
        "BookBudget": BookBudget,
        "Character": Character,
        "CharacterBudgetSnapshot": CharacterBudgetSnapshot,
        "ScriptChapter": ScriptChapter,
    })

    class Env:
        sessions = SessionLocal

        @staticmethod
        def as_owner():
            who.update(uid="u-admin", roles={"admin"}, name="Владелец")

        @staticmethod
        def save_book(book_id, **fields):
            from app.api.budget_api import SaveBookBudgetPayload

            result = run(handlers["api_save_book_budget"](object(), book_id, SaveBookBudgetPayload(**fields)))
            return result if isinstance(result, dict) else json.loads(result.body)

        @staticmethod
        def save(char_id, **fields):
            result = run(handlers["api_save_budget_character"](object(), char_id, SaveBudgetCharacterPayload(**fields)))
            return result if isinstance(result, dict) else json.loads(result.body)

    return Env


def test_approving_in_one_book_casts_the_cycle_and_writes_one_letter(monkeypatch, cast_env):
    sent = []
    monkeypatch.setattr("app.api.budget_api.notify_role_approved",
                        lambda db, **kw: sent.append(kw) or {"notified": True, "kind": "approval"})
    body = cast_env.save("c1", actor_name="Сомов Роман")
    assert [row["character_id"] for row in body["cycle"]["changed"]] == ["c2", "c3"]
    assert len(sent) == 1
    assert sent[0]["also_in"] == ["Сказки волшебников 2", "Крылья Полумрака"]
    with cast_env.sessions() as db:
        assert db.get(Character, "c3").actor_name == "Сомов Роман"


def test_a_proposal_writes_no_cycle_and_no_also_in(monkeypatch, cast_env):
    sent = []
    monkeypatch.setattr("app.api.budget_api.notify_role_approved",
                        lambda db, **kw: sent.append(kw) or {"notified": True, "kind": "audition"})
    body = cast_env.save("c1", actor_name="Сомов Роман?")
    assert "cycle" not in body
    assert sent[0]["also_in"] == []


def test_assignments_are_rebuilt_for_every_book_the_approval_touched(monkeypatch, cast_env):
    from app.models import DictorAssignment, User, UserRole

    with cast_env.sessions() as db:
        db.add(User(id="u-p", login="tg_1", password_hash="telegram-login", display_name="Сомов Роман", is_active="true"))
        db.add(UserRole(id="r-p", user_id="u-p", role="dictor"))
        db.commit()
    monkeypatch.setattr("app.api.budget_api.notify_role_approved", lambda db, **kw: {"notified": True})
    cast_env.save("c1", actor_name="Сомов Роман")
    with cast_env.sessions() as db:
        rows = db.query(DictorAssignment).filter_by(user_id="u-p").all()
        assert sorted((r.book_id, r.state) for r in rows) == [("b1", "approved"), ("b2", "approved"), ("b3", "approved")]


def test_the_narrator_lands_in_the_assignments_of_its_book(monkeypatch, cast_env):
    from app.models import DictorAssignment, User, UserRole

    with cast_env.sessions() as db:
        db.add(User(id="u-l", login="tg_2", password_hash="telegram-login", display_name="Остапович Евгений", is_active="true"))
        db.add(UserRole(id="r-l", user_id="u-l", role="dictor"))
        db.add(BookBudget(book_id="b3"))
        db.commit()
    monkeypatch.setattr("app.api.budget_api.notify_role_approved", lambda db, **kw: {"notified": True})
    cast_env.save_book("b3", narrator_actor_name="Остапович Евгений")
    with cast_env.sessions() as db:
        rows = [(r.book_id, r.character_id, r.role_name) for r in db.query(DictorAssignment).filter_by(user_id="u-l")]
    assert rows == [("b3", "", "Рассказчик")]


def test_a_losing_vote_is_not_carried_across_the_cycle(monkeypatch, cast_env):
    sent = []
    monkeypatch.setattr("app.api.budget_api.notify_role_approved", lambda db, **kw: sent.append(kw) or {})
    with cast_env.sessions() as db:
        cast_role_vote(db, character=db.get(Character, "c1"), voter_uid="u-author", voter_name="Белозёров",
                       weight=2, actor_name="Сомов Роман")
        db.commit()
    cast_env.as_owner()
    body = cast_env.save("c1", actor_name="Иванов Иван")
    assert "cycle" not in body, "владелец проиграл автору в этой книге — нести по циклу нечего"
    with cast_env.sessions() as db:
        assert db.get(Character, "c1").actor_name == "Сомов Роман"
        assert db.get(Character, "c2").actor_name == ""
        assert db.query(RoleVote).filter_by(character_id="c2").count() == 0


def test_an_unchanged_book_still_announces_the_books_the_cycle_changed(monkeypatch, cast_env):
    sent = []
    monkeypatch.setattr("app.api.budget_api.notify_role_approved", lambda db, **kw: sent.append(kw) or {})
    with cast_env.sessions() as db:
        cast_role_vote(db, character=db.get(Character, "c1"), voter_uid="u-author", voter_name="Белозёров",
                       weight=2, actor_name="Сомов Роман")
        db.commit()
    body = cast_env.save("c1", actor_name="Сомов Роман")
    assert [row["character_id"] for row in body["cycle"]["changed"]] == ["c2", "c3"]
    assert len(sent) == 1
    assert (sent[0]["book_id"], sent[0]["also_in"]) == ("b2", ["Крылья Полумрака"])

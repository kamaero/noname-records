"""Кто утверждает актёра на роль, когда голосов два.

Владелец студии и автор смотрят на роль с разных сторон: один думает о загрузке и
сроках, другой — о том, как персонаж должен звучать. Голос автора весит два, голос
владельца — один, и это не вежливость, а разрешение спора: книгу написал он.

Голос — не действие, а мнение, которое остаётся. Поэтому голоса складываются, а
результат пересчитывается заново после каждого: передумал автор — решение уходит туда,
куда указывает уцелевший расклад, а не туда, где последний раз нажали. Пустой голос —
не отказ голосовать, а голос за то, чтобы роль осталась ничьей.
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Character, ScriptBook
from app.services.role_votes import cast_role_vote, role_votes, vote_weight
from app.time_utils import utcnow_naive

OWNER = ("u-kam", "Max Ray", 1)
AUTHOR = ("u-rud", "Александр Белозёров", 2)
AGENT = ("u-agata", "Агата Ковалёва", 0)


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        yield session


@pytest.fixture()
def role(db):
    book = ScriptBook(title="Крылья полумрака", source_filename="k.txt", source_format="txt", created_at=utcnow_naive())
    db.add(book)
    db.flush()
    character = Character(book_id=book.id, name="Сатухух")
    db.add(character)
    db.flush()
    return character


def _vote(db, character, who, actor):
    uid, name, weight = who
    return cast_role_vote(db, character=character, voter_uid=uid, voter_name=name, weight=weight, actor_name=actor)


class TestTheWeights:
    def test_the_author_outweighs_the_owner(self):
        assert vote_weight({"author"}) == 2
        assert vote_weight({"admin"}) == 1

    def test_an_author_who_is_also_admin_keeps_the_heavier_voice(self):
        assert vote_weight({"admin", "author"}) == 2

    def test_a_dictor_has_no_voice_here(self):
        assert vote_weight({"dictor"}) == 0
        assert vote_weight(set()) == 0


class TestOneVoter:
    def test_the_owner_alone_decides(self, db, role):
        result = _vote(db, role, OWNER, "Роман Сомов")

        assert result["actor_name"] == "Роман Сомов"
        assert result["changed"] is True
        assert role.actor_name == "Роман Сомов"

    def test_voting_the_same_way_twice_changes_nothing(self, db, role):
        _vote(db, role, OWNER, "Роман Сомов")
        result = _vote(db, role, OWNER, "Роман Сомов")

        assert result["changed"] is False
        assert len(role_votes(db, role.id)) == 1, "второй голос того же человека заменяет первый, а не добавляется"

    def test_he_can_change_his_own_mind(self, db, role):
        _vote(db, role, OWNER, "Роман Сомов")
        result = _vote(db, role, OWNER, "Сергей Зотов")

        assert result["actor_name"] == "Сергей Зотов"


class TestWhenTheTwoDisagree:
    def test_the_author_overrides_the_owner(self, db, role):
        _vote(db, role, OWNER, "Роман Сомов")
        result = _vote(db, role, AUTHOR, "Сергей Зотов")

        assert result["actor_name"] == "Сергей Зотов"

    def test_and_the_owner_cannot_override_back(self, db, role):
        _vote(db, role, AUTHOR, "Сергей Зотов")
        result = _vote(db, role, OWNER, "Роман Сомов")

        assert result["actor_name"] == "Сергей Зотов"
        assert result["changed"] is False
        assert result["overridden_by"] == "Александр Белозёров"

    def test_the_owners_voice_is_not_lost_only_outweighed(self, db, role):
        """Он останется в списке и решит дело, если автор передумает."""
        _vote(db, role, AUTHOR, "Сергей Зотов")
        _vote(db, role, OWNER, "Роман Сомов")

        assert [v["actor_name"] for v in role_votes(db, role.id)] == ["Сергей Зотов", "Роман Сомов"]

    def test_and_decides_it_the_moment_the_author_agrees(self, db, role):
        _vote(db, role, AUTHOR, "Сергей Зотов")
        _vote(db, role, OWNER, "Роман Сомов")
        result = _vote(db, role, AUTHOR, "Роман Сомов")

        assert result["actor_name"] == "Роман Сомов"


class TestWhenTheTwoAgree:
    def test_the_weight_adds_up(self, db, role):
        _vote(db, role, OWNER, "Роман Сомов")
        _vote(db, role, AUTHOR, "Роман Сомов")
        votes = role_votes(db, role.id)

        assert sum(v["weight"] for v in votes) == 3
        assert role.actor_name == "Роман Сомов"


class TestTakingAnActorOff:
    def test_an_empty_vote_is_a_vote_for_nobody(self, db, role):
        _vote(db, role, AUTHOR, "Сергей Зотов")
        result = _vote(db, role, AUTHOR, "")

        assert result["actor_name"] == ""
        assert role.actor_name == ""

    def test_and_the_author_taking_off_beats_the_owner_putting_on(self, db, role):
        """«Снять актёра» — это голос за то, чтобы роль осталась ничьей, а не отказ голосовать."""
        _vote(db, role, OWNER, "Роман Сомов")
        result = _vote(db, role, AUTHOR, "")

        assert result["actor_name"] == ""

    def test_the_owner_cannot_take_off_whom_the_author_put_on(self, db, role):
        _vote(db, role, AUTHOR, "Сергей Зотов")
        result = _vote(db, role, OWNER, "")

        assert result["actor_name"] == "Сергей Зотов"


class TestWhatTheScreenShows:
    def test_every_voice_is_named_with_its_weight(self, db, role):
        _vote(db, role, OWNER, "Роман Сомов")
        _vote(db, role, AUTHOR, "Сергей Зотов")

        votes = {v["voter_name"]: (v["actor_name"], v["weight"]) for v in role_votes(db, role.id)}

        assert votes == {"Max Ray": ("Роман Сомов", 1), "Александр Белозёров": ("Сергей Зотов", 2)}


class TestACastThatPredatesTheVotes:
    """Таблица голосов появилась позже касты: на бою 65 назначений из 74 без единого голоса.

    Миграция `0017_role_votes` завела таблицу и ничего в неё не перенесла, а
    `app/services/author_profile.py` пишет имя актёра прямо в карточку. Поэтому у
    подавляющего большинства назначений голосов нет вовсе — и предложение агента
    весом 0 оказывается единственным голосом, то есть побеждает и стирает решение,
    принятое до голосования.
    """

    @pytest.fixture()
    def standing(self, db, role):
        """Роль, актёр на которую назначен до появления голосов."""
        role.actor_name = "Остапович Евгений"
        db.flush()
        return role

    def test_a_weightless_suggestion_does_not_displace_it(self, db, standing):
        result = _vote(db, standing, AGENT, "Натали Ким")

        assert result["actor_name"] == "Остапович Евгений"
        assert standing.actor_name == "Остапович Евгений"
        assert result["overridden_by"], "агент должен прочесть, что именно перевесило его предложение"

    def test_an_empty_weightless_vote_does_not_erase_it(self, db, standing):
        """Снять чужое назначение пустой строкой агент не может — снимать нечего, он не назначал."""
        result = _vote(db, standing, AGENT, "")

        assert result["actor_name"] == "Остапович Евгений"
        assert standing.actor_name == "Остапович Евгений"

    def test_the_author_replaces_it(self, db, standing):
        result = _vote(db, standing, AUTHOR, "Роман Сомов")

        assert result["actor_name"] == "Роман Сомов"

    def test_the_owner_replaces_it_too(self, db, standing):
        """Вес затравки — один, а не два: при равенстве решает свежесть, и владелец
        не теряет права переназначить то, что назначено до голосования."""
        result = _vote(db, standing, OWNER, "Роман Сомов")

        assert result["actor_name"] == "Роман Сомов"

    def test_the_seed_is_planted_once(self, db, standing):
        _vote(db, standing, AGENT, "Натали Ким")
        _vote(db, standing, AGENT, "Жукова Виктория")

        assert len(role_votes(db, standing.id)) == 2, "затравка одна: голоса есть — сеять больше нечего"

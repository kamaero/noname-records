"""Что агенту можно в карточке персонажа: одно поле из тринадцати, и то с вопросом.

Агент — кастинг-директор: он предлагает актёра, а решает автор. Предложение живёт как
голос весом 0 (`app/services/role_votes.py`), поэтому отдельного «черновика назначения»
заводить не надо: предварительным его делает арифметика весов плюс «?» в имени.

Раса, темперамент, деньги и палитра — не его дело, и запрос с ними должен получить
внятный отказ, а не тихо потерять половину полей.
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from app.auth import session_serializer
from app.db import Base, SessionLocal, engine as app_engine
from app.main import app
from app.models import Character, RoleVote, ScriptBook

BOOK = "book-1"
CHAR = "char-1"


@pytest.fixture(autouse=True)
def _database():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    SessionLocal.configure(bind=engine)
    with SessionLocal() as db:
        db.add(ScriptBook(id=BOOK, title="Крылья Полумрака", source_filename="k.txt", source_format="txt"))
        db.add(Character(id=CHAR, book_id=BOOK, name="Химера"))
        db.commit()
    try:
        yield
    finally:
        SessionLocal.configure(bind=app_engine)


@pytest.fixture(autouse=True)
def _no_telegram(monkeypatch):
    """Назначение шлёт письмо; здесь проверяется не оно, а право писать в карточку."""
    monkeypatch.setattr("app.api.budget_api.notify_role_approved", lambda *a, **k: None)


def _client(*roles, uid="u1", name="Агата Ковалёва"):
    client = TestClient(app)
    client.cookies.set(
        "session",
        session_serializer.dumps({"uid": uid, "sub": uid, "roles": list(roles), "display_name": name}),
    )
    return client


def _actor_name():
    with SessionLocal() as db:
        return db.get(Character, CHAR).actor_name


class TestWhatTheAgentMayWrite:
    def test_he_assigns_an_actor(self):
        response = _client("agent").post(f"/api/budget/character/{CHAR}", json={"actor_name": "Натали Ким"})

        assert response.status_code == 200
        assert response.json()["ok"] is True

    def test_the_question_mark_is_added_for_him(self):
        _client("agent").post(f"/api/budget/character/{CHAR}", json={"actor_name": "Натали Ким"})

        assert _actor_name() == "Натали Ким?"

    def test_a_name_he_marked_himself_is_left_alone(self):
        _client("agent").post(f"/api/budget/character/{CHAR}", json={"actor_name": "Натали Ким?"})

        assert _actor_name() == "Натали Ким?"

    def test_he_may_take_his_own_suggestion_back(self):
        _client("agent").post(f"/api/budget/character/{CHAR}", json={"actor_name": "Натали Ким"})
        _client("agent").post(f"/api/budget/character/{CHAR}", json={"actor_name": ""})

        assert _actor_name() == ""


class TestWhatTheAgentMayNot:
    @pytest.mark.parametrize(
        "field, value",
        [("race", "демон"), ("temperament", "вспыльчивая"), ("manual_fixed_rub", 5000), ("name", "Не Химера")],
    )
    def test_every_other_field_is_refused(self, field, value):
        response = _client("agent").post(f"/api/budget/character/{CHAR}", json={field: value})

        assert response.status_code == 403

    def test_a_refused_request_changes_nothing(self):
        _client("agent").post(f"/api/budget/character/{CHAR}", json={"actor_name": "Натали Ким", "race": "демон"})

        assert _actor_name() == ""

    def test_the_refusal_says_what_he_may_do_instead(self):
        response = _client("agent").post(f"/api/budget/character/{CHAR}", json={"race": "демон"})

        assert "актёр" in response.json()["message"].lower()


class TestTheWeightOfHisVote:
    def test_the_author_outweighs_him(self):
        _client("agent", uid="agata").post(f"/api/budget/character/{CHAR}", json={"actor_name": "Натали Ким"})
        _client("author", uid="belozerov", name="Белозёров").post(
            f"/api/budget/character/{CHAR}", json={"actor_name": "Роман Сомов"}
        )

        assert _actor_name() == "Роман Сомов"

    def test_he_is_told_whose_decision_stands(self):
        _client("author", uid="belozerov", name="Белозёров").post(
            f"/api/budget/character/{CHAR}", json={"actor_name": "Роман Сомов"}
        )
        response = _client("agent", uid="agata").post(
            f"/api/budget/character/{CHAR}", json={"actor_name": "Натали Ким"}
        )

        assert response.json()["vote"]["overridden_by"] == "Белозёров"

    def test_his_vote_weighs_nothing(self):
        _client("agent", uid="agata").post(f"/api/budget/character/{CHAR}", json={"actor_name": "Натали Ким"})

        with SessionLocal() as db:
            assert db.query(RoleVote).filter(RoleVote.voter_uid == "agata").one().weight == 0

    def test_his_suggestion_stands_while_nobody_else_voted(self):
        _client("agent", uid="agata").post(f"/api/budget/character/{CHAR}", json={"actor_name": "Натали Ким"})

        assert _actor_name() == "Натали Ким?"


class TestEveryoneElse:
    def test_a_dictor_writes_nothing(self):
        assert _client("dictor").post(f"/api/budget/character/{CHAR}", json={"actor_name": "Он Сам"}).status_code == 403

    def test_the_author_still_writes_everything(self):
        response = _client("author").post(
            f"/api/budget/character/{CHAR}", json={"actor_name": "Роман Сомов", "race": "человек"}
        )

        assert response.status_code == 200
        with SessionLocal() as db:
            char = db.get(Character, CHAR)
        assert char.actor_name == "Роман Сомов" and char.race == "человек"


class TestARoleCastBeforeTheVotesExisted:
    """Каст старше таблицы голосов: у большинства назначений на бою голосов нет вовсе.

    Прежние тесты начинали с пустого `actor_name` — там предложению агента нечего
    было затирать, и дыра ни разу не показалась. Здесь актёр уже назначен, а голоса
    за ним нет: ровно так выглядят 65 назначений из 74 на боевой базе.
    """

    def _standing(self, name="Остапович Евгений"):
        with SessionLocal() as db:
            db.get(Character, CHAR).actor_name = name
            db.commit()

    def test_his_suggestion_does_not_overwrite_the_standing_name(self):
        self._standing()

        response = _client("agent", uid="agata").post(f"/api/budget/character/{CHAR}", json={"actor_name": "Натали Ким"})

        assert response.status_code == 200
        assert _actor_name() == "Остапович Евгений"

    def test_he_is_told_what_outweighed_him(self):
        self._standing()

        response = _client("agent", uid="agata").post(f"/api/budget/character/{CHAR}", json={"actor_name": "Натали Ким"})

        assert response.json()["vote"]["overridden_by"], "«overridden_by: \"\"» означало бы, что решение за ним"

    def test_his_empty_vote_does_not_erase_the_standing_name(self):
        self._standing()

        _client("agent", uid="agata").post(f"/api/budget/character/{CHAR}", json={"actor_name": ""})

        assert _actor_name() == "Остапович Евгений"

    def test_the_owner_still_reassigns_such_a_role(self):
        """Затравка весит один, а не два: назначенное до голосования владелец меняет как прежде."""
        self._standing()

        _client("admin", uid="kam", name="Max Ray").post(
            f"/api/budget/character/{CHAR}", json={"actor_name": "Роман Сомов"}
        )

        assert _actor_name() == "Роман Сомов"


class TestTheNarratorIsNotHisToCast:
    """Рассказчика выбирает владелец студии, и запрет обязан жить на сервере.

    Спека считала рассказчика недосягаемым: его имя лежит в
    `BookBudget.narrator_actor_name` за ручкой сметы книги. На бою это не так —
    рассказчик обычная строка `Character` («Рассказчик», актёр «Остапович Евгений»),
    и таблица касты отдаёт ей настоящий `character_id`, тот самый, по которому
    `CastPage` бьёт в `/api/budget/character/{id}`. Пока запрет держался на
    `canAssignActor`, прятавшем поле, его обходил адрес в строке браузера.
    """

    NARRATOR = "char-narrator"

    def _narrator(self):
        with SessionLocal() as db:
            db.add(Character(id=self.NARRATOR, book_id=BOOK, name="Рассказчик", actor_name="Остапович Евгений"))
            db.commit()

    def _narrator_actor(self):
        with SessionLocal() as db:
            return db.get(Character, self.NARRATOR).actor_name

    def test_he_is_refused(self):
        self._narrator()

        response = _client("agent").post(
            f"/api/budget/character/{self.NARRATOR}", json={"actor_name": "Натали Ким"}
        )

        assert response.status_code == 403

    def test_and_the_narrator_keeps_his_actor(self):
        self._narrator()

        _client("agent").post(f"/api/budget/character/{self.NARRATOR}", json={"actor_name": "Натали Ким"})

        assert self._narrator_actor() == "Остапович Евгений"

    def test_the_refusal_says_who_chooses_the_narrator(self):
        self._narrator()

        response = _client("agent").post(
            f"/api/budget/character/{self.NARRATOR}", json={"actor_name": "Натали Ким"}
        )

        assert "рассказчик" in response.json()["message"].lower()

    def test_the_owner_casts_the_narrator_as_before(self):
        self._narrator()

        response = _client("admin").post(
            f"/api/budget/character/{self.NARRATOR}", json={"actor_name": "Роман Сомов"}
        )

        assert response.status_code == 200
        assert self._narrator_actor() == "Роман Сомов"

    def test_an_unclaimed_narrator_is_refused_just_the_same(self):
        """Пустой рассказчик — единственный случай, где арифметике весов нечего перевесить:
        голосов нет, затравки нет, и без запрета на сервере предложение агента село бы."""
        with SessionLocal() as db:
            db.add(Character(id=self.NARRATOR, book_id=BOOK, name="Рассказчик", actor_name=""))
            db.commit()

        response = _client("agent").post(
            f"/api/budget/character/{self.NARRATOR}", json={"actor_name": "Натали Ким"}
        )

        assert response.status_code == 403
        assert self._narrator_actor() == ""


class TestApprovedToTentativeIsNotNews:
    """M2 (решение автора): утверждённого актёра переписали в то же имя со знаком
    вопроса — тот же человек, просто в статусе предложения. Новости в этом нет,
    `notify_role_approved` звать незачем (`should_notify_actor_change`,
    `app/services/role_approval.py`)."""

    def test_no_notification_call_happens(self, monkeypatch):
        calls: list = []
        monkeypatch.setattr("app.api.budget_api.notify_role_approved", lambda *a, **k: calls.append((a, k)) or None)
        _client("author").post(f"/api/budget/character/{CHAR}", json={"actor_name": "Роман Сомов"})
        # Первое назначение — своя новость (было пусто, стало «Роман Сомов»); она не
        # то, что проверяет этот тест — сбрасываем шпиона перед интересующим переходом.
        calls.clear()

        response = _client("author").post(f"/api/budget/character/{CHAR}", json={"actor_name": "Роман Сомов?"})

        assert response.status_code == 200
        assert calls == []
        assert _actor_name() == "Роман Сомов?"

    def test_the_reverse_still_notifies(self, monkeypatch):
        """Обратное направление — «Иван?» → «Иван» — это и есть утверждение, и
        `notify_role_approved` должен вызываться как раньше."""
        calls: list = []
        monkeypatch.setattr("app.api.budget_api.notify_role_approved", lambda *a, **k: calls.append((a, k)) or None)
        _client("agent", uid="agata").post(f"/api/budget/character/{CHAR}", json={"actor_name": "Роман Сомов"})
        assert _actor_name() == "Роман Сомов?"
        calls.clear()

        response = _client("author").post(f"/api/budget/character/{CHAR}", json={"actor_name": "Роман Сомов"})

        assert response.status_code == 200
        assert len(calls) == 1

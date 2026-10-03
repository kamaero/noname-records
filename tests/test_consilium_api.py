"""Ручки находок: смотреть может всякий вошедший, решать — только тот, кто правит."""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.auth import session_serializer
from app.main import app


def _client(roles, uid="u1", sub="u1"):
    client = TestClient(app)
    client.cookies.set("session", session_serializer.dumps(
        {"uid": uid, "sub": sub, "roles": roles, "display_name": "Кто-то"}))
    return client


@pytest.fixture()
def api_db(monkeypatch):
    """Своя пустая база под ручкой вместо боевой.

    Ручки ходят в базу через `SessionLocal` модуля; подменяем именно его, чтобы тест
    проверял настоящий путь запроса, а не подставную функцию поверх него.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    import app.models  # noqa: F401  — регистрация таблиц до create_all
    import app.v2.models  # noqa: F401
    from app.db import Base

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    monkeypatch.setattr("app.v2.api.SessionLocal", session_factory)
    return session_factory


def test_reading_findings_needs_a_session():
    client_anonymous = TestClient(app)

    response = client_anonymous.get("/api/v2/books/b1/consilium")

    assert response.status_code == 401


def test_deciding_needs_the_right_to_edit():
    client_dictor = _client(["dictor"])

    response = client_dictor.post("/api/v2/consilium/whatever/accept", json={})

    assert response.status_code == 403


def test_an_unknown_book_is_a_404_not_an_empty_list(api_db):
    """Опечатка в id книги обязана быть видна.

    Пустой список выглядит как «находок нет» — самый успокоительный из возможных
    ответов, и ровно тот, которого опечатка не заслуживает. Соседняя ручка спорных
    мест отвечает 404; эта отвечала 200.
    """
    response = _client(["author"]).get("/api/v2/books/nope/consilium")

    assert response.status_code == 404


def test_a_real_book_answers_with_its_findings(api_db):
    from app.models import ScriptBook
    from app.time_utils import utcnow_naive

    with api_db() as db:
        db.add(ScriptBook(id="b1", title="Книга", source_filename="k.txt",
                          source_format="txt", created_at=utcnow_naive()))
        db.commit()

    response = _client(["author"]).get("/api/v2/books/b1/consilium")

    assert response.status_code == 200
    assert response.json()["items"] == []


def test_the_journal_records_the_user_id_not_the_login(api_db):
    """«Кто изменил» должно соединяться так же, как для прочих правок v2.

    В журнал вмешательств ложится `actor_user_id`, и весь остальной v2 кладёт туда
    id пользователя. Логин в этой графе выглядит как id и тихо рвёт связь строки
    журнала с человеком.
    """
    from app.models import Character, ConsiliumFinding, OperatorIntervention, ScriptBook
    from app.time_utils import utcnow_naive
    from app.v2.models import V2Attribution, V2Segment

    with api_db() as db:
        db.add(ScriptBook(id="b1", title="Книга", source_filename="k.txt",
                          source_format="txt", created_at=utcnow_naive()))
        for name in ("Дгарнин", "Тупуг"):
            db.add(Character(id=str(uuid.uuid4()), book_id="b1", name=name, aliases="",
                             appears_in="", created_at=utcnow_naive()))
        db.add(V2Segment(id="ch:00358", book_id="b1", chapter_id="ch", ordinal=358,
                         kind="paragraph", text="— Почему ты так уверен? — спросил он."))
        db.add(V2Attribution(id=str(uuid.uuid4()), segment_id="ch:00358", version=1,
                             speaker="Дгарнин", span_start=0, span_end=24, source="llm",
                             confidence=0.5, created_at=utcnow_naive()))
        finding = ConsiliumFinding(
            id="f1", book_id="b1", chapter_index=25, ordinal=358, segment_id="ch:00358",
            span_start=0, span_end=24, kind="wrong_voice", current_speaker="Дгарнин",
            readers_speaker="Тупуг", arbiter_verdict="change", arbiter_speaker="Тупуг",
            created_at=utcnow_naive())
        db.add(finding)
        db.commit()

    response = _client(["author"], uid="u-42", sub="vladelec").post(
        "/api/v2/consilium/f1/accept", json={})

    assert response.status_code == 200
    with api_db() as db:
        row = db.query(OperatorIntervention).one()
        assert row.actor_user_id == "u-42"


def test_accept_passes_the_humans_name_through(api_db):
    """Тело `{"speaker": …}` доходит до приёма; пустое тело — прежний контракт."""
    from app.models import Character, ConsiliumFinding, ScriptBook
    from app.time_utils import utcnow_naive
    from app.v2.models import V2Attribution, V2Segment

    with api_db() as db:
        db.add(ScriptBook(id="b1", title="Книга", source_filename="k.txt",
                          source_format="txt", created_at=utcnow_naive()))
        for name in ("Дгарнин", "Тупуг"):
            db.add(Character(id=str(uuid.uuid4()), book_id="b1", name=name, aliases="",
                             appears_in="", created_at=utcnow_naive()))
        db.add(V2Segment(id="ch:00358", book_id="b1", chapter_id="ch", ordinal=358,
                         kind="paragraph", text="— Почему ты так уверен? — спросил он."))
        db.add(V2Attribution(id=str(uuid.uuid4()), segment_id="ch:00358", version=1,
                             speaker="Дгарнин", span_start=0, span_end=24, source="llm",
                             confidence=0.5, created_at=utcnow_naive()))
        for fid in ("f1", "f2"):
            db.add(ConsiliumFinding(
                id=fid, book_id="b1", chapter_index=25, ordinal=358, segment_id="ch:00358",
                span_start=0, span_end=24 if fid == "f1" else 23, kind="wrong_voice",
                current_speaker="Дгарнин", readers_speaker="Тупуг",
                arbiter_verdict="undecidable", arbiter_speaker="Тупуг",
                created_at=utcnow_naive()))
        db.commit()

    client = _client(["author"])
    refused = client.post("/api/v2/consilium/f2/accept")
    assert refused.status_code == 400 and "arbiter_says_undecidable" in refused.text

    accepted = client.post("/api/v2/consilium/f1/accept", json={"speaker": "Тупуг"})
    assert accepted.status_code == 200 and accepted.json()["speaker"] == "Тупуг"

    garbage = client.post("/api/v2/consilium/f2/accept", content=b"not json",
                          headers={"Content-Type": "application/json"})
    assert garbage.status_code == 400


def test_a_null_speaker_is_not_the_same_as_no_key_at_all(api_db):
    """`{"speaker": null}` называет пустое имя — не «ключа нет, решает арбитр».

    Тело парсит `null` в `None`, и то же самое значение стоит за «ключа не было».
    Не различая их, ручка тихо откатывалась бы на вердикт арбитра, хотя по форме
    запроса кто-то как раз пытался назвать имя (и не смог).
    """
    from app.models import Character, ConsiliumFinding, ScriptBook
    from app.time_utils import utcnow_naive
    from app.v2.models import V2Attribution, V2Segment

    with api_db() as db:
        db.add(ScriptBook(id="b1", title="Книга", source_filename="k.txt",
                          source_format="txt", created_at=utcnow_naive()))
        for name in ("Дгарнин", "Тупуг"):
            db.add(Character(id=str(uuid.uuid4()), book_id="b1", name=name, aliases="",
                             appears_in="", created_at=utcnow_naive()))
        db.add(V2Segment(id="ch:00358", book_id="b1", chapter_id="ch", ordinal=358,
                         kind="paragraph", text="— Почему ты так уверен? — спросил он."))
        db.add(V2Attribution(id=str(uuid.uuid4()), segment_id="ch:00358", version=1,
                             speaker="Дгарнин", span_start=0, span_end=24, source="llm",
                             confidence=0.5, created_at=utcnow_naive()))
        db.add(ConsiliumFinding(
            id="f1", book_id="b1", chapter_index=25, ordinal=358, segment_id="ch:00358",
            span_start=0, span_end=24, kind="wrong_voice", current_speaker="Дгарнин",
            readers_speaker="Тупуг", arbiter_verdict="change", arbiter_speaker="Тупуг",
            created_at=utcnow_naive()))
        db.commit()

    response = _client(["author"]).post("/api/v2/consilium/f1/accept", json={"speaker": None})

    assert response.status_code == 400 and "unknown_speaker" in response.text
    with api_db() as db:
        row = db.query(V2Attribution).filter(V2Attribution.segment_id == "ch:00358").one()
        assert (row.version, row.speaker) == (1, "Дгарнин")
        assert db.query(ConsiliumFinding).one().status == "new"


def test_recording_impact_needs_the_right_to_edit():
    response = _client(["dictor"]).get("/api/v2/consilium/whatever/recording-impact")
    assert response.status_code == 403


def test_accept_queues_a_realign_only_for_a_recorded_chapter(api_db, monkeypatch):
    queued = []
    monkeypatch.setattr("app.v2.api.enqueue_realign_for_chapter", lambda chapter_id: queued.append(chapter_id) or "j")
    monkeypatch.setattr("app.v2.api.accept_finding", lambda db, **kw: {"ok": True, "segment_id": "ch-11:00002",
                                                                        "speaker": "Гамук", "status": "accepted"})
    monkeypatch.setattr("app.v2.api.chapter_of_segment", lambda db, segment_id: "ch-11")
    monkeypatch.setattr("app.v2.api.chapter_has_recognition", lambda db, chapter_id: True)

    response = _client(["author"]).post("/api/v2/consilium/f1/accept", json={"speaker": "Гамук"})

    assert response.status_code == 200 and queued == ["ch-11"]


def test_accept_on_an_unrecorded_chapter_queues_nothing(api_db, monkeypatch):
    queued = []
    monkeypatch.setattr("app.v2.api.enqueue_realign_for_chapter", lambda chapter_id: queued.append(chapter_id) or "j")
    monkeypatch.setattr("app.v2.api.accept_finding", lambda db, **kw: {"ok": True, "segment_id": "s", "speaker": "Т",
                                                                        "status": "accepted"})
    monkeypatch.setattr("app.v2.api.chapter_of_segment", lambda db, segment_id: "ch-1")
    monkeypatch.setattr("app.v2.api.chapter_has_recognition", lambda db, chapter_id: False)

    _client(["author"]).post("/api/v2/consilium/f1/accept", json={"speaker": "Т"})

    assert queued == []


def test_a_failing_queue_does_not_undo_the_accept(api_db, monkeypatch):
    def boom(chapter_id):
        raise RuntimeError("redis down")
    monkeypatch.setattr("app.v2.api.enqueue_realign_for_chapter", boom)
    monkeypatch.setattr("app.v2.api.accept_finding", lambda db, **kw: {"ok": True, "segment_id": "s", "speaker": "Т",
                                                                        "status": "accepted"})
    monkeypatch.setattr("app.v2.api.chapter_of_segment", lambda db, segment_id: "ch-1")
    monkeypatch.setattr("app.v2.api.chapter_has_recognition", lambda db, chapter_id: True)

    response = _client(["author"]).post("/api/v2/consilium/f1/accept", json={"speaker": "Т"})

    assert response.status_code == 200 and response.json()["ok"] is True


def test_reassign_endpoint_computes_recording_impact_before_the_write(api_db, monkeypatch):
    """Доказывает, что предупреждение считается ДО записи новой версии, а не после."""
    from tests.asr_studio import add_take, make_chapter

    queued = []
    monkeypatch.setattr("app.v2.api.enqueue_realign_for_chapter", lambda chapter_id: queued.append(chapter_id) or "j")

    with api_db() as db:
        chapter = make_chapter(db, [("Тупуг", "Мы пойдём на север через перевал."),
                                     ("Гамук", "Там нас давно ждут старые друзья.")],
                               {"Тупуг": "Гончаров Иван", "Гамук": "Гончаров Иван"})
        add_take(db, chapter, role="Тупуг", actor="Гончаров Иван", said=["Мы пойдём на север через перевал."])
        add_take(db, chapter, role="Гамук", actor="Гончаров Иван", said=["Там нас давно ждут старые друзья."])
        db.commit()

    response = _client(["author"]).post(
        "/api/v2/segments/ch-11:00000/attribution",
        json={"spans": [{"start": 0, "end": len("Мы пойдём на север через перевал."), "speaker": "Гамук"}]},
    )

    assert response.status_code == 200
    impact = response.json()["recording_impact"]
    assert impact and impact[0]["state"] == "borrow"
    assert queued == ["ch-11"]


def test_a_failing_recording_impact_does_not_block_the_reassign(api_db, monkeypatch):
    from app.v2.models import V2Attribution
    from tests.asr_studio import make_chapter

    def boom(db, **kwargs):
        raise RuntimeError("ошибка в расчёте предупреждения")

    monkeypatch.setattr("app.v2.api.impacts_for_reassign", boom)
    with api_db() as db:
        make_chapter(db, [("Тупуг", "Мы пойдём на север через перевал."),
                          ("Гамук", "Там нас давно ждут старые друзья.")],
                     {"Тупуг": "Гончаров Иван", "Гамук": "Гончаров Иван"})
        db.commit()

    response = _client(["author"]).post(
        "/api/v2/segments/ch-11:00000/attribution",
        json={"spans": [{"start": 0, "end": len("Мы пойдём на север через перевал."), "speaker": "Гамук"}]},
    )

    assert response.status_code == 200
    assert response.json()["recording_impact"] == []
    with api_db() as db:
        latest = max(db.query(V2Attribution).filter(V2Attribution.segment_id == "ch-11:00000"),
                     key=lambda row: row.version)
        assert latest.speaker == "Гамук"

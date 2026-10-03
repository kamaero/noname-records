"""Кто не редактор — видит только опубликованные главы.

Интерфейс диктора и так уводит его из «Подготовки» в «Запись», но это редирект на
клиенте: маршрут перестал открываться, а ручка продолжала отвечать. Здесь проверка
на той стороне, где её нельзя обойти адресной строкой.

Неопубликованная глава ещё правится автором: реплика из неё может исчезнуть или
сменить роль, и диктор, записавший её, потратит время впустую.
"""
import json
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.v2.api as api
from app.db import Base
from app.models import Character, ScriptBook, ScriptChapter
from app.v2.models import V2Attribution, V2Segment

BOOK = "book-gate"


@pytest.fixture()
def sessions():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    with factory() as db:
        db.add(ScriptBook(id=BOOK, title="Крылья полумрака", source_filename="k.txt", source_format="txt"))
        db.add(Character(id="c1", book_id=BOOK, name="Бимькмолепус"))
        for index, (chapter_id, status) in enumerate((("ch-open", "published"), ("ch-wip", "final")), start=1):
            db.add(ScriptChapter(id=chapter_id, book_id=BOOK, chapter_index=index,
                                 chapter_title=f"Глава {index}", status=status))
            for ordinal in range(3):
                text = f"Абзац {index}.{ordinal}."
                seg = f"{chapter_id}:{ordinal:05d}"
                db.add(V2Segment(id=seg, book_id=BOOK, chapter_id=chapter_id, ordinal=ordinal,
                                 kind="paragraph", text=text, char_start=0, char_end=len(text)))
                db.add(V2Attribution(id=str(uuid.uuid4()), segment_id=seg, span_start=0,
                                     span_end=len(text), speaker="Бимькмолепус", confidence=0.9,
                                     source="llm", version=1))
        db.commit()
    return factory


def _as(monkeypatch, sessions, *, editor: bool):
    monkeypatch.setattr(api, "SessionLocal", sessions)
    monkeypatch.setattr(api, "_is_authenticated", lambda request: True)
    monkeypatch.setattr(api, "_can_edit", lambda request: editor)
    monkeypatch.setattr(api, "_can_voice", lambda request: True)


def _body(response) -> dict:
    return json.loads(bytes(response.body).decode("utf-8"))


def test_a_dictor_cannot_open_an_unpublished_chapter_by_its_address(sessions, monkeypatch):
    _as(monkeypatch, sessions, editor=False)

    assert api.api_v2_chapter_script(object(), "ch-open").status_code == 200
    denied = api.api_v2_chapter_script(object(), "ch-wip")
    assert denied.status_code == 404


def test_an_author_still_opens_the_chapter_he_is_working_on(sessions, monkeypatch):
    """Иначе шлюз отнял бы у автора ровно то, ради чего «Подготовка» и существует."""
    _as(monkeypatch, sessions, editor=True)

    assert api.api_v2_chapter_script(object(), "ch-wip").status_code == 200


def test_the_role_script_hides_unpublished_chapters_from_a_dictor(sessions, monkeypatch):
    """Сбор реплик роли идёт по всей книге сразу — значит и утечь может вся книга."""
    _as(monkeypatch, sessions, editor=False)

    class _Req:
        query_params = {"role": "Бимькмолепус"}

    seen = {c["chapter_id"] for c in _body(api.api_v2_role_script(_Req(), BOOK))["chapters"]}
    assert seen == {"ch-open"}


def test_the_role_script_still_shows_everything_to_an_author(sessions, monkeypatch):
    _as(monkeypatch, sessions, editor=True)

    class _Req:
        query_params = {"role": "Бимькмолепус"}

    seen = {c["chapter_id"] for c in _body(api.api_v2_role_script(_Req(), BOOK))["chapters"]}
    assert seen == {"ch-open", "ch-wip"}


@pytest.mark.parametrize("voice", [True, False])
def test_the_role_script_says_whether_the_reader_may_set_stress(sessions, monkeypatch, voice):
    """Актёры ставят ударения прямо в «Всех репликах роли» — страница должна знать,
    включать ли конструктор. Право то же, что у сохранения ударения (`_can_voice`):
    кнопка, которая заведомо получит 403, хуже, чем её отсутствие."""
    _as(monkeypatch, sessions, editor=False)
    monkeypatch.setattr(api, "_can_voice", lambda request: voice)

    class _Req:
        query_params = {"role": "Бимькмолепус"}

    assert _body(api.api_v2_role_script(_Req(), BOOK))["can_voice"] is voice


def test_the_role_traps_hide_unpublished_chapters_from_a_dictor(sessions, monkeypatch):
    """«Слова-ловушки» собираются по всей книге — та же граница, что у сбора реплик."""
    class _Req:
        query_params = {"role": "Бимькмолепус", "fresh": "0"}

    # «Абзац» в фикстуре — частое слово; здесь важна граница глав, а не частота
    monkeypatch.setattr("app.v2.word_rarity.is_rare", lambda word: True)
    _as(monkeypatch, sessions, editor=False)
    dictor = {c["chapter_id"] for c in _body(api.api_v2_role_traps(_Req(), BOOK))["chapters"]}
    _as(monkeypatch, sessions, editor=True)
    author = {c["chapter_id"] for c in _body(api.api_v2_role_traps(_Req(), BOOK))["chapters"]}
    assert author == {"ch-open", "ch-wip"}, "фикстура не даёт слов — тест бессмыслен"
    assert dictor == {"ch-open"}

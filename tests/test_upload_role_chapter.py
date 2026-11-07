"""Запись главы не ложится в главу, где у её роли нет ни одной реплики.

03.10 Лирвин Ланта загрузила «фыегу люпкетфу» в главу 1 (назвала файл `Ch01` и
выбрала главу 1 в форме — сверка номера промолчала), а Фыеги говорит только в главе 32.
Решение владельца — жёсткий отказ.
"""
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Character, ScriptBook, ScriptChapter
from app.services import upload_role_chapter as urc

NOW = datetime(2026, 10, 3, 12, 0, 0)


@pytest.fixture()
def db(monkeypatch):
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        book = ScriptBook(id="b1", title="Карта Приозерья", source_filename="k.txt", source_format="txt", created_at=NOW)
        session.add(book)
        session.add(ScriptChapter(id="ch1", book_id="b1", chapter_index=1, chapter_title="Глава 1. Стул шатается"))
        session.add(ScriptChapter(id="ch32", book_id="b1", chapter_index=32, chapter_title="Глава 32. Фыеги"))
        session.add(ScriptChapter(id="ch40", book_id="b1", chapter_index=40, chapter_title=""))
        session.add(Character(book_id="b1", name="Фыеги Люпкетфа", aliases="Фыеги", actor_name="Лирвин Ланта"))
        session.commit()
        counts = {
            "ch1": {"Хдыбдяр": 12, "Рассказчик": 40},
            "ch32": {"Фыеги Люпкетфа": 2, "Рассказчик": 30},
            "ch40": {},
        }
        monkeypatch.setattr(urc, "chapter_role_counts", lambda db, chapter_id: counts.get(chapter_id, {}))
        monkeypatch.setattr(urc, "book_role_counts_by_chapter", lambda db, book_id: counts)
        yield session


def _check(db, chapter, role):
    return urc.role_not_in_chapter(db, book_code="КП", chapter_label=chapter, role=role)


def test_a_role_without_lines_in_the_chapter_is_refused_with_where_it_speaks(db):
    assert _check(db, "Глава 1. Стул шатается", "Фыеги Люпкетфа") == [32]


def test_a_role_that_speaks_in_the_chapter_passes(db):
    assert _check(db, "Глава 32. Фыеги", "Фыеги Люпкетфа") is None
    assert _check(db, "Глава 1. Стул шатается", "Рассказчик") is None


def test_case_and_aliases_do_not_matter(db):
    assert _check(db, "Глава 32. Фыеги", "фыеги люпкетфа") is None
    assert _check(db, "Глава 32. Фыеги", "Фыеги") is None
    assert _check(db, "Глава 1. Стул шатается", "фыеги") == [32]


def test_a_role_that_speaks_nowhere_is_refused_with_an_empty_list(db):
    assert _check(db, "Глава 1. Стул шатается", "Тик-Так") == []


def test_nothing_to_check_against_means_no_refusal(db):
    """Глава без разметки (ничего не посчитано), неизвестная глава или книга — не повод
    отказывать: отказ должен опираться на знание, а не на его отсутствие."""
    assert _check(db, "Глава 40", "Фыеги Люпкетфа") is None
    assert _check(db, "Глава 99. Нет такой", "Фыеги Люпкетфа") is None
    assert urc.role_not_in_chapter(db, book_code="ЖЖ", chapter_label="Глава 1. Стул шатается", role="Фыеги Люпкетфа") is None


def test_the_refusal_text_names_the_role_and_the_chapters():
    assert urc.refusal_text("Фыеги Люпкетфа", 1, [32]) == (
        "role «Фыеги Люпкетфа» has no lines in chapter 1; this role speaks in chapter 32"
    )
    assert urc.refusal_text("Тик-Так", 1, []) == (
        "role «Тик-Так» has no lines in chapter 1; this role has no attributed lines in any chapter"
    )
    assert "in chapters 3, 7, 12" in urc.refusal_text("Икс", 1, [3, 7, 12])


# ---------- ручки: загрузка и предпросмотр ----------

import asyncio  # noqa: E402
import json  # noqa: E402

from app.models import AudioFile  # noqa: E402
from tests.test_chapter_mismatch_guard import (  # noqa: E402
    TestThePreviewFlagsTheSameMismatch, _JsonRequest, _meta, _post, _sessions, _upload,
)


def _sessions_with_chapters(monkeypatch):
    """Ермаков утверждён на «Нрарсх»; Нрарсх говорит в главе 34, а в главе 1 — нет."""
    sessions = _sessions()
    with sessions() as db:
        book = db.query(ScriptBook).one()
        db.add(ScriptChapter(id="c1", book_id=book.id, chapter_index=1, chapter_title="Глава 1. Ничего особенного"))
        db.add(ScriptChapter(id="c34", book_id=book.id, chapter_index=34, chapter_title="Глава 34. Поздравляю с бракосочетанием"))
        db.add(Character(book_id=book.id, name="Нрарсх", actor_name="Нартия Нилдар"))
        db.commit()
    counts = {"c1": {"Хдыбдяр": 3}, "c34": {"Нрарсх": 5}}
    monkeypatch.setattr(urc, "chapter_role_counts", lambda db, chapter_id: counts.get(chapter_id, {}))
    monkeypatch.setattr(urc, "book_role_counts_by_chapter", lambda db, book_id: counts)
    return sessions


def _meta_bhulh(chapter: str, **extra):
    return {"book_code": "KP", "chapter": chapter, "role": "Нрарсх", "actor_name": "Нартия Нилдар", **extra}


def test_the_upload_refuses_a_role_with_no_lines_and_keeps_nothing(monkeypatch):
    sessions = _sessions_with_chapters(monkeypatch)

    status_code, body, rows = _post(sessions, [_upload("KP_Ch01_Nrarsh_NartiyaNildar.wav")],
                                    [_meta_bhulh("Глава 1. Ничего особенного")])

    assert status_code == 400 and rows == []
    assert body["items"][0]["error"] == "role_not_in_chapter"
    assert "no lines in chapter 1" in body["message"] and "in chapter 34" in body["message"]


def test_confirming_the_chapter_does_not_lift_this_refusal(monkeypatch):
    sessions = _sessions_with_chapters(monkeypatch)

    status_code, body, rows = _post(sessions, [_upload("KP_Ch01_Nrarsh_NartiyaNildar.wav")],
                                    [_meta_bhulh("Глава 1. Ничего особенного", confirm_chapter=True)])

    assert status_code == 400 and rows == []


def test_the_right_chapter_is_accepted(monkeypatch):
    sessions = _sessions_with_chapters(monkeypatch)

    status_code, _body, rows = _post(sessions, [_upload("KP_Ch34_Nrarsh_NartiyaNildar.wav")],
                                     [_meta_bhulh("Глава 34. Поздравляю с бракосочетанием")])

    assert status_code == 200 and len(rows) == 1 and isinstance(rows[0], AudioFile)


def test_the_preview_shows_the_same_refusal_in_words(monkeypatch):
    sessions = _sessions_with_chapters(monkeypatch)
    handler = TestThePreviewFlagsTheSameMismatch()._handlers(sessions)["dictor_pro_batch_validate"]
    payload = {
        "files": [{"name": "KP_Ch01_Nrarsh_NartiyaNildar.wav", "size": 4}],
        "overrides": [{"chapter": "Глава 1. Ничего особенного", "role": "Нрарсх",
                       "actor_name": "Нартия Нилдар", "book_code": "KP"}],
        "book_code": "KP", "actor_name": "Нартия Нилдар",
    }

    body = json.loads(asyncio.run(handler(_JsonRequest(payload))).body)

    item = body["items"][0]
    assert item["ok"] is False and "role_not_in_chapter" in item["errors"]
    assert item["error_text"] == "role «Нрарсх» has no lines in chapter 1; this role speaks in chapter 34"

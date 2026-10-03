"""Один отчёт владельцу на собранную главу — вместо письма на каждый дубль.

`build_chapter_report` собирает картину по уже посчитанным `AsrJob` главы: что не
нашлось (`missing`), что нашлось, но легло у порога (`weak`), и какие куски
разметки не несут речи вовсе (`markup`, сигнал о сценарии, не о дикторе).

`render_chapter_report` даёт текст письма — по образцу `asr_notice`: заголовок,
реплики первыми словами, а не номером, длинные списки обрезаны.
"""
import json
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import AsrJob, ScriptBook, ScriptChapter
from app.services.chapter_coverage_report import build_chapter_report, render_chapter_report
from app.v2.models import V2Attribution, V2Segment


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    with sessionmaker(bind=engine)() as session:
        yield session


def _add_job(db, chapter: str, role: str, lines: list[dict]) -> None:
    """Задание распознавания дубля роли: уже разобранное, со статусом `done`."""
    payload_lines = []
    missing = []
    for index, line in enumerate(lines):
        payload_lines.append({
            "index": index,
            "text": line["text"],
            "matched": bool(line["matched"]),
            "score": float(line["score"]),
        })
        if not line["matched"]:
            missing.append(index)
    alignment = {"lines": payload_lines, "missing": missing, "matched": len(lines) - len(missing), "total": len(lines)}
    db.add(AsrJob(
        id=str(uuid.uuid4()),
        audio_file_id=str(uuid.uuid4()),
        chapter_id=chapter,
        expected_role=role,
        status="done",
        alignment_json=json.dumps(alignment, ensure_ascii=False),
    ))


def _add_failed_job(db, chapter: str, role: str, error: str = "stream_timeout") -> None:
    """Задание, у которого расшифровка упала: `alignment_json` пуст, счётчики нулевые."""
    db.add(AsrJob(
        id=str(uuid.uuid4()),
        audio_file_id=str(uuid.uuid4()),
        chapter_id=chapter,
        expected_role=role,
        status="failed",
        error_message=error,
        alignment_json="",
    ))


def _say(db, chapter: str, ordinal: int, speaker: str, text: str) -> None:
    seg = f"{chapter}:{ordinal:05d}"
    db.add(V2Segment(id=seg, book_id="book-1", chapter_id=chapter, ordinal=ordinal,
                     kind="paragraph", text=text, char_start=0, char_end=len(text)))
    db.add(V2Attribution(id=str(uuid.uuid4()), segment_id=seg, span_start=0,
                         span_end=len(text), speaker=speaker, confidence=0.9,
                         source="llm", version=1))


def test_отчёт_считает_пропуски_и_слабые_совпадения(db):
    """Порог слабого совпадения выбран по живым данным: из 273 совпавших реплик
    94 процента сходятся на 0.90 и выше, ниже 0.62 нет ничего."""
    _add_job(db, chapter="ch-1", role="Гамук", lines=[
        {"text": "— Первая.", "matched": True, "score": 0.99},
        {"text": "— Вторая.", "matched": True, "score": 0.68},
        {"text": "— Третья.", "matched": False, "score": 0.11},
    ])
    db.commit()

    report = build_chapter_report(db, "ch-1")

    assert [item["role"] for item in report["missing"]] == ["Гамук"]
    assert report["missing"][0]["lines"] == ["— Третья."]
    assert [item["text"] for item in report["weak"]] == ["— Вторая."]


def test_кусок_без_речи_идёт_сигналом_о_разметке_а_не_пропуском(db):
    _say(db, "ch-1", 0, "Гамук", "Гамук подплыл ближе и доверительно сказал:")
    db.commit()

    report = build_chapter_report(db, "ch-1")

    assert report["markup"] == [{"role": "Гамук", "text": "Гамук подплыл ближе и доверительно сказал:"}]
    assert report["missing"] == []


def test_текст_чистого_отчёта_короток_и_говорит_что_всё_на_месте():
    text = render_chapter_report({
        "chapter": "Глава 7", "book_title": "Крылья полумрака",
        "roles": 5, "recorded_roles": 5, "takes": 6,
        "missing": [], "weak": [], "markup": [], "link": "",
    })
    assert "Глава 7" in text
    assert "всё на месте" in text.lower()


def test_текст_отчёта_называет_роль_и_первые_слова():
    text = render_chapter_report({
        "chapter": "Глава 7", "book_title": "Крылья полумрака",
        "roles": 5, "recorded_roles": 5, "takes": 6,
        "missing": [{"role": "Гамук", "lines": ["— Проводил твоих друзей."]}],
        "weak": [], "markup": [], "link": "",
    })
    assert "Гамук" in text
    assert "Проводил твоих друзей" in text


def test_упавшее_распознавание_идёт_отдельным_разделом(db):
    """Статус `failed` не попадает под `status == "done"`, но пропасть молча не должен:
    роль с упавшей расшифровкой не проверена, и владелец должен это увидеть, а не
    прочитать «всё на месте» про то, чего никто не слушал."""
    _add_failed_job(db, chapter="ch-1", role="Гамук", error="stream_timeout")
    db.commit()

    report = build_chapter_report(db, "ch-1")

    assert report["failed"] == [{"role": "Гамук", "error": "stream_timeout"}]
    assert report["missing"] == []
    assert report["weak"] == []


def test_текст_отчёта_с_упавшим_распознаванием_не_говорит_что_всё_на_месте():
    text = render_chapter_report({
        "chapter": "Глава 7", "book_title": "Крылья полумрака",
        "roles": 5, "recorded_roles": 5, "takes": 6,
        "missing": [], "weak": [], "markup": [],
        "failed": [{"role": "Гамук", "error": "stream_timeout"}],
        "link": "",
    })
    assert "всё на месте" not in text.lower()
    assert "Гамук" in text


def test_ссылка_ведёт_в_конкретную_главу(db):
    """Отчёт про эту главу — ссылка должна открывать именно её, а не первую попавшуюся."""
    db.add(ScriptBook(id="book-1", title="Крылья полумрака", source_filename="x.docx", source_format="docx"))
    db.add(ScriptChapter(id="ch-1", book_id="book-1", chapter_index=1, chapter_title="Глава 1"))
    db.commit()

    report = build_chapter_report(db, "ch-1")

    assert "book_id=book-1" in report["link"]
    assert "chapter_id=ch-1" in report["link"]


def test_реплики_в_разделах_идут_по_ролям_а_не_вперемешку(db):
    """Несколько ролей — вперемешку раздражает. Все реплики одной роли должны стоять
    рядом, независимо от порядка, в котором задания легли в базу."""
    _add_job(db, chapter="ch-1", role="Тупуг", lines=[
        {"text": "— Тупуг первая.", "matched": True, "score": 0.5},
    ])
    _add_job(db, chapter="ch-1", role="Гамук", lines=[
        {"text": "— Гамук первая.", "matched": True, "score": 0.5},
    ])
    _add_job(db, chapter="ch-1", role="Тупуг", lines=[
        {"text": "— Тупуг вторая.", "matched": True, "score": 0.5},
    ])
    db.commit()

    report = build_chapter_report(db, "ch-1")

    assert [item["role"] for item in report["weak"]] == ["Гамук", "Тупуг", "Тупуг"]


def test_упавший_до_создания_задания_дубль_идёт_в_тот_же_раздел(db):
    """`run_asr_for_take` умеет упасть до того, как заведёт строку `AsrJob`:
    файла нет, это не дубль, главы не нашлось. По базе такой дубль неотличим от
    нераспознанного вовсе, и отчёт, читающий только `AsrJob`, промолчал бы о нём.
    Прогон эти падения считает — и передаёт сюда списком."""
    report = build_chapter_report(db, "ch-1", lost_takes=[{"role": "Тупуг", "error": "chapter_not_found"}])

    assert report["failed"] == [{"role": "Тупуг", "error": "chapter_not_found"}]
    assert "всё на месте" not in render_chapter_report(report).lower()


def test_упавшие_с_заданием_и_без_идут_вместе(db):
    """Оба пути ведут к одному: роль не проверена. Разделять их в письме владельцу
    нечем — причина у обоих в той же строке."""
    _add_failed_job(db, chapter="ch-1", role="Гамук", error="stream_timeout")
    db.commit()

    report = build_chapter_report(db, "ch-1", lost_takes=[{"role": "Тупуг", "error": "audio_not_found"}])

    assert report["failed"] == [
        {"role": "Гамук", "error": "stream_timeout"},
        {"role": "Тупуг", "error": "audio_not_found"},
    ]

"""CLI-пересчёт по главам (`scripts/realign_chapters.py`): холостой прогон ничего не пишет.

`run()` — та же логика, что и у `main()`, но без парсинга аргументов и без своей сессии,
чтобы её можно было прогнать на подготовленной в тесте базе и проверить результат.
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from tests.asr_studio import add_take, make_chapter, move_line

LINES = [
    ("Тупуг", "Мы пойдём на север через перевал."),
    ("Гамук", "Там нас давно ждут старые друзья."),
    ("Тупуг", "Я знаю короткую дорогу к реке."),
]


@pytest.fixture()
def db():
    import app.models  # noqa: F401

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        yield session


def test_a_dry_run_reports_the_borrow_and_leaves_the_database_unchanged(db):
    from scripts.realign_chapters import run

    chapter = make_chapter(db, LINES, {"Тупуг": "Гончаров Иван", "Гамук": "Гончаров Иван"})
    add_take(db, chapter, role="Тупуг", actor="Гончаров Иван",
             said=["Мы пойдём на север через перевал.", "Я знаю короткую дорогу к реке."])
    gamuk = add_take(db, chapter, role="Гамук", actor="Гончаров Иван", said=["Там нас давно ждут старые друзья."])
    move_line(db, chapter, 2, "Гамук")
    db.commit()  # база «уже принятого» — то, с чем работал бы прод-скрипт
    was = gamuk.alignment_json

    reports = run(db, [chapter], apply=False, notify=False)

    assert len(reports) == 1
    report = reports[0]
    assert report["borrowed"] == 1
    assert report["lost_matches"] == []

    # Холостой прогон откатился: пересчёт джобы Гамука не осел в базе.
    assert gamuk.alignment_json == was


def test_a_move_before_a_still_heard_line_is_not_a_lost_match(db):
    """Реплика ушла из середины роли — последующие строки сдвинулись, но остались найденными."""
    from scripts.realign_chapters import run

    lines = LINES + [("Тупуг", "Держите оружие наготове у костра.")]
    chapter = make_chapter(db, lines, {"Тупуг": "Гончаров Иван", "Гамук": "Иванов Пётр"})
    add_take(db, chapter, role="Тупуг", actor="Гончаров Иван",
             said=["Мы пойдём на север через перевал.", "Я знаю короткую дорогу к реке.",
                   "Держите оружие наготове у костра."])
    add_take(db, chapter, role="Гамук", actor="Иванов Пётр", said=["Там нас давно ждут старые друзья."])
    move_line(db, chapter, 2, "Гамук")
    db.commit()

    report = run(db, [chapter], apply=False, notify=False)[0]

    assert report["new_missing"] == {"Гамук": ["Я знаю короткую дорогу к реке."]}
    assert report["lost_matches"] == []


def test_a_genuinely_lost_match_is_reported(db, monkeypatch):
    """Потеря за перенесённой строкой: по номеру её не видно, по тексту — видно."""
    import json

    from app.models import AsrJob
    from app.services import asr_replay
    from scripts.realign_chapters import run

    lines = LINES + [("Тупуг", "Держите оружие наготове у костра.")]
    chapter = make_chapter(db, lines, {"Тупуг": "Гончаров Иван", "Гамук": "Иванов Пётр"})
    tupug = add_take(db, chapter, role="Тупуг", actor="Гончаров Иван",
                     said=["Мы пойдём на север через перевал.", "Я знаю короткую дорогу к реке.",
                           "Держите оружие наготове у костра."])
    add_take(db, chapter, role="Гамук", actor="Иванов Пётр", said=["Там нас давно ждут старые друзья."])
    move_line(db, chapter, 2, "Гамук")
    db.commit()
    real = asr_replay.realign_chapter

    def broken_rule(db, chapter_id, **kwargs):
        # Новое правило сверки «разучилось» слышать последнюю строку Тупуга.
        report = real(db, chapter_id, **kwargs)
        job = db.get(AsrJob, tupug.id)
        alignment = json.loads(job.alignment_json)
        alignment["lines"][-1]["matched"] = False
        job.alignment_json = json.dumps(alignment, ensure_ascii=False)
        db.flush()
        return report

    monkeypatch.setattr(asr_replay, "realign_chapter", broken_rule)

    report = run(db, [chapter], apply=False, notify=False)[0]

    assert report["lost_matches"] == [("Тупуг", "Держите оружие наготове у костра.")]


def test_apply_notify_without_a_chapter_is_refused(monkeypatch, capsys):
    import sys

    from scripts import realign_chapters

    called = []
    monkeypatch.setattr(realign_chapters, "run", lambda *a, **k: called.append(1) or [])
    monkeypatch.setattr(sys, "argv", ["realign_chapters.py", "--apply", "--notify"])

    assert realign_chapters.main() == 2
    assert called == []
    assert "--chapter" in capsys.readouterr().err


def test_apply_notify_sends_letters_only_after_the_commit(db, monkeypatch):
    from app.services import asr_replay
    from scripts.realign_chapters import run

    chapter = make_chapter(db, LINES, {"Тупуг": "Гончаров Иван", "Гамук": "Иванов Пётр"})
    add_take(db, chapter, role="Тупуг", actor="Гончаров Иван",
             said=["Мы пойдём на север через перевал.", "Я знаю короткую дорогу к реке."])
    add_take(db, chapter, role="Гамук", actor="Иванов Пётр", said=["Там нас давно ждут старые друзья."])
    move_line(db, chapter, 2, "Гамук")
    db.commit()
    events = []
    real_commit = db.commit
    monkeypatch.setattr(db, "commit", lambda: events.append("commit") or real_commit())
    monkeypatch.setattr(asr_replay, "send_moved_letters",
                        lambda db, pending: events.append(("send", len(pending))) or [{}] * len(pending))

    report = run(db, [chapter], apply=True, notify=True)[0]

    assert events == ["commit", ("send", 1)]
    assert report["notified"] == 1

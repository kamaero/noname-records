"""Реплику отдали другой роли того же актёра — звук берётся из соседнего файла."""
import json

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
    import app.v2.models  # noqa: F401

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        yield session


def _lines(job):
    return json.loads(job.alignment_json)["lines"]


def _realign(db, chapter_id):
    from app.models import AsrJob
    from app.services.asr_replay import realign_job

    for job in db.query(AsrJob).filter(AsrJob.chapter_id == chapter_id).all():
        realign_job(db, job.id)


def test_same_actor_line_is_taken_from_the_neighbour_file(db):
    from app.services.asr_borrow import borrow_across_roles

    chapter = make_chapter(db, LINES, {"Тупуг": "Гончаров Иван", "Гамук": "Гончаров Иван"})
    tupug = add_take(db, chapter, role="Тупуг", actor="Гончаров Иван",
                     said=["Мы пойдём на север через перевал.", "Я знаю короткую дорогу к реке."])
    gamuk = add_take(db, chapter, role="Гамук", actor="Гончаров Иван",
                     said=["Там нас давно ждут старые друзья."])
    move_line(db, chapter, 2, "Гамук")
    _realign(db, chapter)
    assert [line["matched"] for line in _lines(gamuk)] == [True, False]

    report = borrow_across_roles(db, chapter)

    line = _lines(gamuk)[1]
    assert line["matched"] is True
    assert line["source_audio_file_id"] == tupug.audio_file_id
    assert line["takes"] and line["takes"][0]["start"] > 0
    assert gamuk.coverage == 1.0
    assert report["borrowed"] == 1
    assert report["searched"] == 1
    assert report["roles"] == {"Гамук": 1}


def test_a_different_actor_is_never_a_donor(db):
    from app.services.asr_borrow import borrow_across_roles

    chapter = make_chapter(db, LINES, {"Тупуг": "Гончаров Иван", "Гамук": "Иванов Пётр"})
    add_take(db, chapter, role="Тупуг", actor="Гончаров Иван",
             said=["Мы пойдём на север через перевал.", "Я знаю короткую дорогу к реке."])
    gamuk = add_take(db, chapter, role="Гамук", actor="Иванов Пётр", said=["Там нас давно ждут старые друзья."])
    move_line(db, chapter, 2, "Гамук")
    _realign(db, chapter)

    borrow_across_roles(db, chapter)

    assert _lines(gamuk)[1]["matched"] is False
    assert "source_audio_file_id" not in _lines(gamuk)[1]


def test_a_tentative_cast_actor_is_not_the_same_actor(db):
    from app.services.asr_borrow import borrow_across_roles

    chapter = make_chapter(db, LINES, {"Тупуг": "Гончаров Иван", "Гамук": "Гончаров Иван?"})
    add_take(db, chapter, role="Тупуг", actor="Гончаров Иван",
             said=["Мы пойдём на север через перевал.", "Я знаю короткую дорогу к реке."])
    gamuk = add_take(db, chapter, role="Гамук", actor="Гончаров Иван", said=["Там нас давно ждут старые друзья."])
    move_line(db, chapter, 2, "Гамук")
    _realign(db, chapter)

    borrow_across_roles(db, chapter)

    assert _lines(gamuk)[1]["matched"] is False


def test_words_of_the_donor_own_lines_are_not_taken(db):
    """«Да, конечно.» Гамука не должна найтись в «Да, конечно.» самого Тупуга."""
    from app.services.asr_borrow import borrow_across_roles

    lines = [("Тупуг", "Да, конечно, мы идём."), ("Гамук", "Там нас давно ждут старые друзья."),
             ("Тупуг", "Да, конечно, мы идём.")]
    chapter = make_chapter(db, lines, {"Тупуг": "Гончаров Иван", "Гамук": "Гончаров Иван"})
    add_take(db, chapter, role="Тупуг", actor="Гончаров Иван", said=["Да, конечно, мы идём."])
    gamuk = add_take(db, chapter, role="Гамук", actor="Гончаров Иван", said=["Там нас давно ждут старые друзья."])
    move_line(db, chapter, 2, "Гамук")
    _realign(db, chapter)

    borrow_across_roles(db, chapter)

    # Второй «Да, конечно» диктор Тупуга не читал: единственное вхождение — реплика самого Тупуга.
    assert _lines(gamuk)[1]["matched"] is False


def test_two_identical_missing_lines_do_not_share_one_place(db):
    from app.services.asr_borrow import borrow_across_roles

    lines = [("Тупуг", "Мы пойдём на север через перевал."), ("Гамук", "Там нас давно ждут старые друзья."),
             ("Тупуг", "Держись ближе ко мне, брат."), ("Тупуг", "Держись ближе ко мне, брат.")]
    chapter = make_chapter(db, lines, {"Тупуг": "Гончаров Иван", "Гамук": "Гончаров Иван"})
    add_take(db, chapter, role="Тупуг", actor="Гончаров Иван",
             said=["Мы пойдём на север через перевал.", "Держись ближе ко мне, брат."])
    gamuk = add_take(db, chapter, role="Гамук", actor="Гончаров Иван", said=["Там нас давно ждут старые друзья."])
    move_line(db, chapter, 2, "Гамук")
    move_line(db, chapter, 3, "Гамук")
    _realign(db, chapter)

    borrow_across_roles(db, chapter)

    assert [line["matched"] for line in _lines(gamuk)] == [True, True, False]


def test_a_repeat_call_does_not_reuse_an_already_borrowed_spot(db):
    """Вторая ненайденная реплика того же текста не находится в уже отданном месте."""
    from app.services.asr_borrow import borrow_across_roles

    lines = [("Тупуг", "Мы пойдём на север через перевал."), ("Гамук", "Там нас давно ждут старые друзья."),
             ("Тупуг", "Держись ближе ко мне, брат."), ("Тупуг", "Держись ближе ко мне, брат.")]
    chapter = make_chapter(db, lines, {"Тупуг": "Гончаров Иван", "Гамук": "Гончаров Иван"})
    add_take(db, chapter, role="Тупуг", actor="Гончаров Иван",
             said=["Мы пойдём на север через перевал.", "Держись ближе ко мне, брат."])
    gamuk = add_take(db, chapter, role="Гамук", actor="Гончаров Иван", said=["Там нас давно ждут старые друзья."])
    move_line(db, chapter, 2, "Гамук")
    move_line(db, chapter, 3, "Гамук")
    _realign(db, chapter)

    borrow_across_roles(db, chapter)
    assert [line["matched"] for line in _lines(gamuk)] == [True, True, False]

    # Второй, «холостой» проход не должен найти для той же непрочитанной строки то же
    # самое место, которое первый проход уже отдал предыдущей одинаковой реплике.
    second_report = borrow_across_roles(db, chapter)

    assert [line["matched"] for line in _lines(gamuk)] == [True, True, False]
    assert second_report["borrowed"] == 0


def test_two_identical_read_lines_each_get_their_own_place(db):
    """Обе одинаковые перенесённые реплики звучали в файле донора — обе и находятся."""
    from app.services.asr_borrow import borrow_across_roles

    lines = [("Тупуг", "Мы пойдём на север через перевал."), ("Гамук", "Там нас давно ждут старые друзья."),
             ("Тупуг", "Держись ближе ко мне, брат."), ("Тупуг", "Держись ближе ко мне, брат.")]
    chapter = make_chapter(db, lines, {"Тупуг": "Гончаров Иван", "Гамук": "Гончаров Иван"})
    add_take(db, chapter, role="Тупуг", actor="Гончаров Иван",
             said=["Мы пойдём на север через перевал.", "Держись ближе ко мне, брат.",
                   "Держись ближе ко мне, брат."])
    gamuk = add_take(db, chapter, role="Гамук", actor="Гончаров Иван", said=["Там нас давно ждут старые друзья."])
    move_line(db, chapter, 2, "Гамук")
    move_line(db, chapter, 3, "Гамук")
    _realign(db, chapter)

    report = borrow_across_roles(db, chapter)

    assert [line["matched"] for line in _lines(gamuk)] == [True, True, True]
    assert report["borrowed"] == 2


def test_a_weak_resemblance_is_not_borrowed(db):
    from app.services.asr_borrow import borrow_across_roles

    chapter = make_chapter(db, LINES, {"Тупуг": "Гончаров Иван", "Гамук": "Гончаров Иван"})
    add_take(db, chapter, role="Тупуг", actor="Гончаров Иван",
             said=["Мы пойдём на север через перевал.", "Я знаю длинную тропу к озеру."])
    gamuk = add_take(db, chapter, role="Гамук", actor="Гончаров Иван", said=["Там нас давно ждут старые друзья."])
    move_line(db, chapter, 2, "Гамук")
    _realign(db, chapter)

    borrow_across_roles(db, chapter)

    assert _lines(gamuk)[1]["matched"] is False


def test_narrator_actor_from_book_budget_can_still_borrow(db):
    """У «Рассказчика» нет своей записи Character с актёром — актёр только в BookBudget."""
    from app.models import BookBudget
    from app.services.asr_borrow import borrow_across_roles

    lines = [("Тупуг", "Мы пойдём на север через перевал."), ("Рассказчик", "Там нас давно ждут старые друзья."),
             ("Тупуг", "Я знаю короткую дорогу к реке.")]
    chapter = make_chapter(db, lines, {"Тупуг": "Гончаров Иван"})
    db.add(BookBudget(book_id="b1", narrator_actor_name="Гончаров Иван"))
    # Тупуг в своём дубле заодно проговорил и реплику рассказчика — она не его собственная
    # ожидаемая строка и остаётся в файле свободной, донором для настоящего получателя.
    tupug = add_take(db, chapter, role="Тупуг", actor="Гончаров Иван",
                     said=["Мы пойдём на север через перевал.", "Там нас давно ждут старые друзья.",
                           "Я знаю короткую дорогу к реке."])
    narrator = add_take(db, chapter, role="Рассказчик", actor="Гончаров Иван", said=["что-то совсем другое"])

    report = borrow_across_roles(db, chapter)

    assert _lines(narrator)[0]["matched"] is True
    assert _lines(narrator)[0]["source_audio_file_id"] == tupug.audio_file_id
    assert report["borrowed"] == 1


def test_a_line_heard_in_another_file_of_the_same_role_is_not_borrowed(db):
    from app.services.asr_borrow import borrow_across_roles, role_unheard_texts

    chapter = make_chapter(db, LINES, {"Тупуг": "Гончаров Иван", "Гамук": "Гончаров Иван"})
    add_take(db, chapter, role="Тупуг", actor="Гончаров Иван",
             said=["Мы пойдём на север через перевал.", "Я знаю короткую дорогу к реке."])
    first = add_take(db, chapter, role="Гамук", actor="Гончаров Иван", said=["Там нас давно ждут старые друзья."])
    second = add_take(db, chapter, role="Гамук", actor="Гончаров Иван", said=["Я знаю короткую дорогу к реке."])
    move_line(db, chapter, 2, "Гамук")
    _realign(db, chapter)

    borrow_across_roles(db, chapter)

    # Строку даёт второй файл той же роли, а не чужой донор.
    assert _lines(first)[1]["source_audio_file_id"] == second.audio_file_id
    assert _lines(second)[1]["matched"] is True
    assert "source_audio_file_id" not in _lines(second)[1]
    assert role_unheard_texts(db, chapter).get("Гамук", []) == []


def test_a_one_word_line_is_not_borrowed_even_if_the_word_is_free_in_the_donor(db):
    from app.services.asr_borrow import borrow_across_roles

    lines = [("Тупуг", "Мы пойдём на север через перевал."), ("Гамук", "Там нас давно ждут старые друзья."),
             ("Тупуг", "Да.")]
    chapter = make_chapter(db, lines, {"Тупуг": "Гончаров Иван", "Гамук": "Гончаров Иван"})
    # «Да» звучит в файле Тупуга свободным словом — не его реплика после переноса.
    add_take(db, chapter, role="Тупуг", actor="Гончаров Иван", said=["Мы пойдём на север через перевал.", "Да."])
    gamuk = add_take(db, chapter, role="Гамук", actor="Гончаров Иван", said=["Там нас давно ждут старые друзья."])
    move_line(db, chapter, 2, "Гамук")
    _realign(db, chapter)

    report = borrow_across_roles(db, chapter)

    assert _lines(gamuk)[1]["matched"] is False
    assert report["borrowed"] == 0


def test_a_repeat_call_after_a_sibling_copy_changes_nothing(db):
    from app.services.asr_borrow import borrow_across_roles

    chapter = make_chapter(db, LINES, {"Тупуг": "Гончаров Иван", "Гамук": "Иванов Пётр"})
    add_take(db, chapter, role="Тупуг", actor="Гончаров Иван",
             said=["Мы пойдём на север через перевал.", "Я знаю короткую дорогу к реке."])
    first = add_take(db, chapter, role="Гамук", actor="Иванов Пётр", said=["Там нас давно ждут старые друзья."])
    second = add_take(db, chapter, role="Гамук", actor="Иванов Пётр", said=["Я знаю короткую дорогу к реке."])
    move_line(db, chapter, 2, "Гамук")
    _realign(db, chapter)
    borrow_across_roles(db, chapter)
    was = (first.alignment_json, second.alignment_json)

    report = borrow_across_roles(db, chapter)
    _realign(db, chapter)
    borrow_across_roles(db, chapter)

    assert report["borrowed"] == 0
    assert (first.alignment_json, second.alignment_json) == was
    assert _lines(second)[0]["source_audio_file_id"] == first.audio_file_id


def test_a_line_borrowed_from_another_role_reaches_every_file_of_the_role(db):
    from app.services.asr_borrow import borrow_across_roles

    lines = LINES + [("Гамук", "Держите оружие наготове у костра.")]
    chapter = make_chapter(db, lines, {"Тупуг": "Гончаров Иван", "Гамук": "Гончаров Иван"})
    tupug = add_take(db, chapter, role="Тупуг", actor="Гончаров Иван",
                     said=["Мы пойдём на север через перевал.", "Я знаю короткую дорогу к реке."])
    first = add_take(db, chapter, role="Гамук", actor="Гончаров Иван", said=["Там нас давно ждут старые друзья."])
    second = add_take(db, chapter, role="Гамук", actor="Гончаров Иван", said=["Держите оружие наготове у костра."])
    move_line(db, chapter, 2, "Гамук")
    _realign(db, chapter)

    report = borrow_across_roles(db, chapter)

    assert report["borrowed"] == 1
    for job in (first, second):
        assert [line["matched"] for line in _lines(job)] == [True, True, True]
        assert _lines(job)[1]["source_audio_file_id"] == tupug.audio_file_id
        assert job.coverage == 1.0


def test_a_moved_line_rerecorded_in_a_separate_file_closes_the_chapter(db, monkeypatch):
    """Реплику отдали другому актёру, он дописал её отдельным файлом — писем и пропусков нет."""
    from app.services import asr_notice, audio_storage, telegram
    from app.services.asr_replay import realign_chapter
    from app.services.asr_run import chapter_replicas, run_asr_for_chapter
    from app.services.chapter_delivery import _lines_by_audio, _place_by_script, book_recording_status
    from app.models import AudioFile
    from tests.asr_studio import heard

    letters, owner = [], []
    monkeypatch.setattr(asr_notice, "resolve_actor_candidates", lambda db, name: [(f"chat:{name}", name)])
    monkeypatch.setattr(asr_notice, "send_telegram_message",
                        lambda db, text, chat_ids=None, **k: letters.append((chat_ids, text)) or 1)
    monkeypatch.setattr(telegram, "send_telegram_message", lambda db, text, **k: owner.append(text) or 1)
    monkeypatch.setattr(audio_storage, "resolve_path", lambda key, location="local": key)

    lines = LINES + [("Гамук", "Держите оружие наготове у костра.")]
    chapter = make_chapter(db, lines, {"Тупуг": "Гончаров Иван", "Гамук": "Иванов Пётр"})
    said = {"tupug": ["Мы пойдём на север через перевал.", "Я знаю короткую дорогу к реке."],
            "gamuk": ["Там нас давно ждут старые друзья.", "Держите оружие наготове у костра."],
            "gamuk2": ["Я знаю короткую дорогу к реке."]}
    add_take(db, chapter, role="Тупуг", actor="Гончаров Иван", said=said["tupug"], take_id="tupug")
    add_take(db, chapter, role="Гамук", actor="Иванов Пётр", said=said["gamuk"], take_id="gamuk")
    move_line(db, chapter, 2, "Гамук")
    realign_chapter(db, chapter)
    assert len(letters) == 1  # письмо о переносе — одно, настоящее
    letters.clear()

    add_take(db, chapter, role="Гамук", actor="Иванов Пётр", said=said["gamuk2"], take_id="gamuk2")

    def transcribe(path):
        name = path.rsplit("/", 1)[-1].removesuffix(".wav")
        return heard(*said[name])(path)

    run_asr_for_chapter(db, chapter, transcribe=transcribe)

    assert letters == []
    assert not any("Не дочитано" in text for text in owner)
    row = book_recording_status(db, "b1")["chapters"][0]
    assert row["missing_lines"] == 0
    assert row["matched_lines"] == row["total_lines"] == 4

    files = db.query(AudioFile).filter(AudioFile.kind == "take").order_by(AudioFile.uploaded_at.asc()).all()
    by_role = {}
    for item in files:
        by_role.setdefault(item.role, []).append(item)
    placed = _place_by_script(chapter_replicas(db, chapter), by_role, _lines_by_audio(db, [f.id for f in files]),
                              relative=False, files_by_id={f.id: f for f in files})
    moved = [clip for role, clip in placed if role == "Гамук" and clip.name.startswith("Я знаю короткую")]
    assert len(moved) == 1 and moved[0].path == "k/gamuk2.wav"
    assert len(placed) == 4

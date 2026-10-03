"""Что станет со звуком реплики, если отдать её другой роли, — до того, как отдали."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from tests.asr_studio import add_take, make_chapter

LINES = [("Тупуг", "Мы пойдём на север через перевал."),
         ("Гамук", "Там нас давно ждут старые друзья."),
         ("Тупуг", "Я знаю короткую дорогу к реке."),
         ("Хор", "Ветер поёт над горами.")]
SEG = "ch-11:00002"
SPAN = (0, len(LINES[2][1]))


@pytest.fixture()
def db():
    import app.models  # noqa: F401
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        yield session


def _impact(db, to_role):
    from app.services.recording_impact import recording_impacts
    out = recording_impacts(db, segment_id=SEG, span_start=SPAN[0], span_end=SPAN[1], to_roles=[to_role])
    return out["by_speaker"][to_role]


def _studio(db, cast, *, tupug_said=True, gamuk_file=True):
    chapter = make_chapter(db, LINES, cast)
    add_take(db, chapter, role="Тупуг", actor=cast["Тупуг"].rstrip("?"),
             said=["Мы пойдём на север через перевал."] + (["Я знаю короткую дорогу к реке."] if tupug_said else []))
    if gamuk_file:
        add_take(db, chapter, role="Гамук", actor=cast["Гамук"].rstrip("?"), said=["Там нас давно ждут старые друзья."])
    return chapter


def test_not_recorded_chapter_says_nothing(db):
    from app.services.recording_impact import recording_impacts
    make_chapter(db, LINES, {"Тупуг": "Гончаров Иван", "Гамук": "Иванов Пётр"})
    out = recording_impacts(db, segment_id=SEG, span_start=SPAN[0], span_end=SPAN[1], to_roles=["Гамук"])
    assert out == {"recorded": False, "by_speaker": {"Гамук": {"state": "not_recorded", "level": "info", "text": ""}}}


def test_borrow(db):
    _studio(db, {"Тупуг": "Гончаров Иван", "Гамук": "Гончаров Иван", "Хор": ""})
    got = _impact(db, "Гамук")
    assert got["state"] == "borrow" and got["level"] == "info"
    assert got["text"] == "Глава записана. Звук возьмём из файла роли «Тупуг» — читает тот же актёр (Гончаров Иван)."


def test_borrow_no_file(db):
    _studio(db, {"Тупуг": "Гончаров Иван", "Гамук": "Гончаров Иван", "Хор": "Гончаров Иван"})
    got = _impact(db, "Хор")
    assert got["state"] == "borrow_no_file" and got["level"] == "warn"
    assert got["text"] == ("Глава записана, звук лежит у роли «Тупуг» (тот же актёр), но у роли «Хор» в этой главе "
                           "нет своей записи — сборка его не возьмёт, реплику придётся дописать.")


def test_rerecord(db):
    _studio(db, {"Тупуг": "Гончаров Иван", "Гамук": "Иванов Пётр", "Хор": ""})
    got = _impact(db, "Гамук")
    assert got["state"] == "rerecord" and got["level"] == "warn"
    assert got["text"] == ("Глава записана — реплика лежит у роли «Тупуг» (Гончаров Иван). После правки её "
                           "дозапишет «Гамук» (Иванов Пётр), ему придёт уведомление.")


def test_not_in_audio(db):
    _studio(db, {"Тупуг": "Гончаров Иван", "Гамук": "Иванов Пётр", "Хор": ""}, tupug_said=False)
    got = _impact(db, "Гамук")
    assert got["state"] == "not_in_audio" and got["level"] == "warn"
    assert got["text"] == ("Глава записана, но этой реплики в записи нет. Актёру роли «Гамук» (Иванов Пётр) "
                           "придёт уведомление её дописать.")


def test_new_role_unrecorded(db):
    _studio(db, {"Тупуг": "Гончаров Иван", "Гамук": "Иванов Пётр", "Хор": "Сидорова Анна"})
    got = _impact(db, "Хор")
    assert got["state"] == "new_role_unrecorded" and got["level"] == "info"
    assert got["text"] == "У роли «Хор» в этой главе ещё нет записи — реплика войдёт в её обычную запись."


def test_unassigned_actor_is_said_plainly(db):
    _studio(db, {"Тупуг": "Гончаров Иван", "Гамук": "", "Хор": ""})
    got = _impact(db, "Гамук")
    assert got["state"] == "rerecord"
    assert "(актёр не назначен)" in got["text"] and "уведомлять некого" in got["text"]


def test_reassign_impacts_only_for_changed_spans(db):
    from app.services.recording_impact import impacts_for_reassign
    _studio(db, {"Тупуг": "Гончаров Иван", "Гамук": "Гончаров Иван", "Хор": ""})

    same = impacts_for_reassign(db, segment_id=SEG, spans=[{"start": SPAN[0], "end": SPAN[1], "speaker": "Тупуг"}])
    moved = impacts_for_reassign(db, segment_id=SEG, spans=[{"start": SPAN[0], "end": SPAN[1], "speaker": "Гамук"}])

    assert same == []
    assert [(item["from_role"], item["to_role"], item["state"]) for item in moved] == [("Тупуг", "Гамук", "borrow")]


def test_reassign_impact_canonicalises_the_speaker_before_comparing(db):
    """`spans` несёт то, что напечатал человек, — сравнивать с ролью в разметке надо после канонизации."""
    from app.services.recording_impact import impacts_for_reassign
    _studio(db, {"Тупуг": "Гончаров Иван", "Гамук": "Гончаров Иван", "Хор": ""})

    moved = impacts_for_reassign(db, segment_id=SEG, spans=[{"start": SPAN[0], "end": SPAN[1], "speaker": "гамук"}])

    assert [(item["from_role"], item["to_role"], item["state"]) for item in moved] == [("Тупуг", "Гамук", "borrow")]


def test_narrator_actor_from_book_budget_appears_in_the_text(db):
    """У «Рассказчика» нет своей записи Character с актёром — актёр только в BookBudget."""
    from app.models import AudioFile, BookBudget
    from app.time_utils import utcnow_naive
    from tests.asr_studio import CHAPTER_TITLE

    _studio(db, {"Тупуг": "Гончаров Иван", "Гамук": "Иванов Пётр", "Хор": ""})
    db.add(AudioFile(id="take-narrator", book_code="КП", original_filename="n.wav", stored_key="k/n.wav",
                     canonical_filename="KP_Ch11_n.wav", mime_type="audio/wav", size_bytes=10,
                     chapter=CHAPTER_TITLE, role="Рассказчик", actor_name="Кто-то", kind="take",
                     uploaded_at=utcnow_naive()))
    db.add(BookBudget(book_id="b1", narrator_actor_name="Сидорова Анна"))
    db.flush()

    got = _impact(db, "Рассказчик")

    assert got["state"] == "rerecord"
    assert "Сидорова Анна" in got["text"]


def test_reassign_in_an_unrecorded_chapter_does_not_read_the_chapter_replicas(db, monkeypatch):
    from app.services import asr_run
    from app.services.recording_impact import impacts_for_reassign

    make_chapter(db, LINES, {"Тупуг": "Гончаров Иван", "Гамук": "Иванов Пётр"})
    monkeypatch.setattr(asr_run, "chapter_replicas", lambda *a, **k: pytest.fail("реплики главы читать незачем"))
    monkeypatch.setattr("app.v2.reader.effective_attributions", lambda *a, **k: pytest.fail("разметку читать незачем"))

    assert impacts_for_reassign(db, segment_id=SEG, spans=[{"start": SPAN[0], "end": SPAN[1], "speaker": "Гамук"}]) == []

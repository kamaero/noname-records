"""Пробы, собранные в пары «актёр + роль»: оценки, занятость, отказ — по правам смотрящего."""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import AudioFile, AuditionRejection, AuditionScore, Character, ScriptBook
from app.services.audio_uploads import derive_book_code
from app.services.audition_pairs import book_pairs

NOW = datetime(2026, 10, 3, 12, 0, 0)


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        yield session


@pytest.fixture()
def book(db):
    book = ScriptBook(title="Карта приозерья", display_title="Карта Приозерья", source_filename="k.txt",
                      source_format="txt", created_at=NOW)
    db.add(book)
    db.flush()
    return book


def _audition(db, book, *, role="Саяху", actor="Примершупядяобб Примерцвфмллрив", minutes=0):
    item = AudioFile(book_code=derive_book_code(book.title), original_filename="p.wav",
                     stored_key=f"k/{role}/{actor}/{minutes}", mime_type="audio/wav", size_bytes=10, chapter="",
                     role=role, actor_name=actor, kind="audition", uploaded_at=NOW + timedelta(minutes=minutes))
    db.add(item)
    db.commit()
    return item


def _cast(db, book, role, actor):
    db.add(Character(book_id=book.id, name=role, actor_name=actor))
    db.commit()


def test_three_files_of_one_pair_are_one_row_newest_first(db, book):
    ids = [_audition(db, book, minutes=m).id for m in (0, 20, 10)]
    _audition(db, book, actor="Ветрова Ольга", minutes=5)

    pairs = book_pairs(db, book, sees_scores=True, viewer_name="Владелец")

    assert [p["actor_name"] for p in pairs] == ["Примершупядяобб Примерцвфмллрив", "Ветрова Ольга"]
    assert [a["id"] for a in pairs[0]["auditions"]] == [ids[1], ids[2], ids[0]]
    assert pairs[0]["uploaded_at"] == pairs[0]["auditions"][0]["uploaded_at"]
    assert pairs[0]["role_auditions"] == 2


def test_scores_and_labels_for_those_who_see_them(db, book):
    _audition(db, book)
    db.add(AuditionScore(book_id=book.id, role="Саяху", actor_name="Примершупядяобб Примерцвфмллрив", lane="author",
                         voter_uid="a", voter_name="Примервлщющючб Примернкяхаххчб", score=8))
    db.commit()

    [pair] = book_pairs(db, book, sees_scores=True, viewer_name="Владелец")

    assert pair["scores"] == {"author": {"score": 8, "name": "Примервлщющючб Примернкяхаххчб", "reason": "", "comment": ""}, "studio": None}
    assert set(pair["lane_labels"]) == {"author", "studio"}


def test_a_dictor_gets_no_scores_even_on_own_pair(db, book):
    _audition(db, book)
    db.add(AuditionScore(book_id=book.id, role="Саяху", actor_name="Примершупядяобб Примерцвфмллрив", lane="author",
                         voter_uid="a", voter_name="Примервлщющючб Примернкяхаххчб", score=8))
    db.commit()

    [pair] = book_pairs(db, book, sees_scores=False, viewer_name="Примершупядяобб Примерцвфмллрив")

    assert pair["scores"] is None and pair["lane_labels"] is None


def test_busy_on_another_role_by_fuzzy_name(db, book):
    _audition(db, book)
    _cast(db, book, "Фэдывнютд", "Примерцвфмллрив Примершупядяобб")

    [pair] = book_pairs(db, book, sees_scores=True, viewer_name="")

    assert pair["busy_on"] == ["Фэдывнютд"] and pair["approved"] is False


def test_a_tentative_name_does_not_make_busy_and_own_role_is_approved_not_busy(db, book):
    _audition(db, book)
    _audition(db, book, role="Сылядир", actor="Ветрова Ольга")
    _cast(db, book, "Фэдывнютд", "Примершупядяобб Примерцвфмллрив?")
    _cast(db, book, "Сылядир", "Ольга Ветрова")

    pairs = {p["actor_name"]: p for p in book_pairs(db, book, sees_scores=True, viewer_name="")}

    assert pairs["Примершупядяобб Примерцвфмллрив"]["busy_on"] == []
    assert (pairs["Ветрова Ольга"]["approved"], pairs["Ветрова Ольга"]["busy_on"]) == (True, [])
    assert pairs["Ветрова Ольга"]["role_actor"] == "Ольга Ветрова"


def test_rejection_is_seen_by_the_studio_and_by_the_dictor_only_once_sent(db, book):
    _audition(db, book)
    rej = AuditionRejection(book_id=book.id, book_code=derive_book_code(book.title), role="Саяху",
                            actor_name="Примершупядяобб Примерцвфмллрив", due_at=NOW, status="pending")
    db.add(rej)
    db.commit()

    assert book_pairs(db, book, sees_scores=True, viewer_name="")[0]["rejection"]["status"] == "pending"
    assert book_pairs(db, book, sees_scores=False, viewer_name="Примершупядяобб Примерцвфмллрив")[0]["rejection"] is None

    rej.status, rej.sent_at = "sent", NOW
    db.commit()
    assert book_pairs(db, book, sees_scores=False, viewer_name="Примерцвфмллрив Примершупядяобб")[0]["rejection"]["status"] == "sent"
    assert book_pairs(db, book, sees_scores=False, viewer_name="Ветрова Ольга")[0]["rejection"] is None


def test_role_is_found_in_the_cast_regardless_of_case_and_by_alias(db, book):
    """Роль пробы берётся из имени файла («тик-так»), в касте — «Тик-Так»: строка должна
    знать персонажа, иначе «Утвердить» отвечает «роли нет в касте»."""
    _audition(db, book, role="тик-так", actor="Примергдпнкюцю Примероюбргфюу")
    _audition(db, book, role="Отец Чудовищ", actor="Ветрова Ольга")
    db.add(Character(id="c-tik", book_id=book.id, name="Тик-Так", actor_name="Примергдпнкюцю Примероюбргфюу"))
    db.add(Character(id="c-org", book_id=book.id, name="Убкнэляб", aliases="Отец Чудовищ", actor_name=""))
    db.commit()

    pairs = {p["actor_name"]: p for p in book_pairs(db, book, sees_scores=True, viewer_name="")}

    assert (pairs["Примергдпнкюцю Примероюбргфюу"]["character_id"], pairs["Примергдпнкюцю Примероюбргфюу"]["approved"]) == ("c-tik", True)
    assert pairs["Примергдпнкюцю Примероюбргфюу"]["busy_on"] == []
    assert pairs["Ветрова Ольга"]["character_id"] == "c-org"

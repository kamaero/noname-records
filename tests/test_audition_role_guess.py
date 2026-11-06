"""Роль пробы под другим написанием: «Лйра» из имени файла — это «Лира» книги.

11.10 окно утверждения завело вторую роль «Лйра» без реплик: диктор открыла пустую
Читалку, а срок пробы на настоящей роли прислал ей «просрочено». Теперь лента
подсказывает похожую роль, а утверждение переименовывает пробу — опечатка диктора
вторым именем роли в каст не попадает (решение владельца 13.10).
"""
from datetime import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import AudioFile, AuditionRejection, AuditionScore, Character, ScriptBook
from app.services.audio_uploads import derive_book_code
from app.services.audition_pairs import book_pairs
from app.services.audition_rejections import character_for_role, similar_character
from app.v2.cast_ops import CreateRoleError, move_auditions_to_role

NOW = datetime(2026, 10, 13, 2, 0, 0)


def _ch(name, aliases=""):
    return SimpleNamespace(id=name, name=name, aliases=aliases)


@pytest.mark.parametrize("role, cast, expected", [
    ("Лйра", ["Лира", "Ао"], "Лира"),          # й/и
    ("Алёна", ["Алена"], "Алена"),             # ё/е
    ("Хдыбдярл", ["Хдыбдяр"], "Хдыбдяр"),      # одна лишняя буква
    ("Скардоваю", ["скардоваи"], "скардоваи"), # одна замена
    ("Пыфбу", ["Пыдбу", "Зловещий"], "Пыдбу"),  # «Карта», проба Ваваева
    ("Хлод", ["Хлод Рулыт", "Ао"], "Хлод Рулыт"),  # первое слово полного имени
    ("Ао", ["Ао Ти"], None),                    # двухбуквенное — не угадывать
    ("Дракон", ["Хлод Рулыт"], None),
    ("Бухгалтерша", ["Лира"], None),           # непохоже
    ("Ай", ["Ао"], None),                       # короткие — только й/ё
    ("Майа", ["Майя", "Маия"], None),          # похожих двое — не угадывать
    ("Лйра", ["Лира", "Лйра"], None),          # и дубль, и оригинал — двое, не угадывать
])
def test_similar_character(role, cast, expected):
    found = similar_character([_ch(name) for name in cast], role)
    assert (found.name if found else None) == expected


def test_alias_counts_as_a_spelling():
    assert similar_character([_ch("Убкнэляб", "Отец Чудовищ")], "отец чудовищь").name == "Убкнэляб"


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        book = ScriptBook(id="b1", title="Карта приозерья", display_title="Карта Приозерья",
                          source_filename="k.txt", source_format="txt", created_at=NOW)
        session.add(book)
        session.add(Character(id="c-eina", book_id="b1", name="Лира", aliases="бухгалтер скардоваи",
                              actor_name="Харвин Хортен?"))
        session.add(Character(id="c-ao", book_id="b1", name="Ао"))
        session.add(AudioFile(book_code=derive_book_code(book.title), original_filename="p.wav", stored_key="k/1",
                              mime_type="audio/wav", size_bytes=10, chapter="", role="Лйра",
                              actor_name="Харвин Хортен", kind="audition", uploaded_at=NOW))
        session.commit()
        yield session


def test_feed_offers_the_similar_role_with_who_is_on_it(db):
    [pair] = book_pairs(db, db.get(ScriptBook, "b1"), sees_scores=True, viewer_name="")

    assert pair["character_id"] == ""
    assert pair["role_guess"] == {"character_id": "c-eina", "name": "Лира", "actor_name": "Харвин Хортен?"}


def test_a_found_role_has_no_guess(db):
    db.get(Character, "c-eina").aliases = "Лйра"
    db.commit()
    [pair] = book_pairs(db, db.get(ScriptBook, "b1"), sees_scores=True, viewer_name="")

    assert (pair["character_id"], pair["role_guess"]) == ("c-eina", None)


def _score(db, role, lane, score, actor="Харвин Хортен"):
    db.add(AuditionScore(book_id="b1", role=role, actor_name=actor, lane=lane, voter_uid="v", voter_name="V",
                         score=score, created_at=NOW, updated_at=NOW))
    db.commit()


def test_the_audition_moves_under_the_book_spelling_and_the_cast_is_untouched(db):
    _score(db, "Лйра", "author", 9)
    result = move_auditions_to_role(db, book_id="b1", character_id="c-eina", role="Лйра",
                                    audition_actor="Харвин Хортен", actor_uid="u1")
    db.commit()

    assert result == {"moved": 1, "character_id": "c-eina", "name": "Лира"}
    assert db.get(Character, "c-eina").aliases == "бухгалтер скардоваи"
    audio = db.query(AudioFile).one()
    assert (audio.role, audio.kind) == ("Лира", "audition")
    assert "Lira" in audio.canonical_filename
    assert db.query(AuditionScore).one().role == "Лира"
    [pair] = book_pairs(db, db.get(ScriptBook, "b1"), sees_scores=True, viewer_name="")
    assert (pair["role"], pair["character_id"], pair["role_guess"]) == ("Лира", "c-eina", None)
    assert pair["scores"]["author"]["score"] == 9


def test_a_score_the_right_pair_already_has_wins(db):
    _score(db, "Лйра", "author", 6)
    _score(db, "Лйра", "studio", 8)
    _score(db, "Лира", "author", 9)
    db.add(AuditionRejection(book_id="b1", book_code="КП", role="Лйра", actor_name="Харвин Хортен",
                             due_at=NOW, status="cancelled"))
    db.commit()
    move_auditions_to_role(db, book_id="b1", character_id="c-eina", role="Лйра",
                           audition_actor="Харвин Хортен", actor_uid="u1")
    db.commit()

    scores = {(s.role, s.lane): s.score for s in db.query(AuditionScore)}
    assert scores == {("Лира", "author"): 9, ("Лира", "studio"): 8}
    assert [r.role for r in db.query(AuditionRejection)] == ["Лира"]


def test_another_actors_audition_stays(db):
    move_auditions_to_role(db, book_id="b1", character_id="c-eina", role="Лйра",
                           audition_actor="Ревента Ревмар", actor_uid="u1")
    assert db.query(AudioFile).one().role == "Лйра"


def test_a_role_of_another_book_is_not_found(db):
    db.add(ScriptBook(id="b2", title="Другая", source_filename="d.txt", source_format="txt", created_at=NOW))
    db.commit()
    with pytest.raises(CreateRoleError) as exc:
        move_auditions_to_role(db, book_id="b2", character_id="c-eina", role="Лйра",
                               audition_actor="Харвин Хортен", actor_uid="u1")
    assert exc.value.code == "character_not_found"

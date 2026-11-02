"""Оценка 1–10 актёру на роль: одна на дорожку, последнее нажатие побеждает."""
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import AudioFile, Author, AuditionScore, ScriptBook, TelegramAuthAccount
from app.services import audition_scores as sc
from app.services.audio_uploads import derive_book_code

NOW = datetime(2026, 10, 3, 12, 0, 0)


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        yield session


def _book(db, title="Карта приозерья", author_id=""):
    book = ScriptBook(title=title, source_filename="k.txt", source_format="txt", created_at=NOW, author_id=author_id)
    db.add(book)
    db.flush()
    return book


def _audition(db, book, *, role="Саяху", actor="Примершупядяобб Примерцвфмллрив"):
    item = AudioFile(
        book_code=derive_book_code(book.title), original_filename="p.wav", stored_key=f"k/{role}/{actor}/{NOW}",
        mime_type="audio/wav", size_bytes=10, chapter="", role=role, actor_name=actor, kind="audition", uploaded_at=NOW,
    )
    db.add(item)
    db.commit()
    return item


def _set(db, book, score, *, lane="author", uid="author-1", name="Примервлщющючб Примернкяхаххчб", actor="Примершупядяобб Примерцвфмллрив"):
    return sc.set_score(db, book_id=book.id, role="Саяху", actor_name=actor, lane=lane, score=score,
                        voter_uid=uid, voter_name=name)


def test_a_score_is_set_changed_and_taken_back(db):
    book = _book(db)
    _audition(db, book)

    assert _set(db, book, 8) == {"author": {"score": 8, "name": "Примервлщющючб Примернкяхаххчб", "reason": "", "comment": ""}, "studio": None}
    assert _set(db, book, 3)["author"]["score"] == 3
    assert _set(db, book, None) == {"author": None, "studio": None}
    assert db.query(AuditionScore).count() == 0


def test_two_lanes_are_independent(db):
    book = _book(db)
    _audition(db, book)
    _set(db, book, 8)

    result = _set(db, book, 5, lane="studio", uid="owner-1", name="Примеряфеицшпчб Примерщюагоэсгв")

    assert result == {"author": {"score": 8, "name": "Примервлщющючб Примернкяхаххчб", "reason": "", "comment": ""}, "studio": {"score": 5, "name": "Примеряфеицшпчб Примерщюагоэсгв", "reason": "", "comment": ""}}


def test_the_last_press_on_a_lane_wins(db):
    book = _book(db)
    _audition(db, book)
    _set(db, book, 4, lane="studio", uid="admin-1", name="Админ")

    _set(db, book, 9, lane="studio", uid="owner-1", name="Примеряфеицшпчб Примерщюагоэсгв")

    assert db.query(AuditionScore).count() == 1
    assert sc.scores_by_pair(db, book.id)[("Саяху", "Примершупядяобб Примерцвфмллрив")]["studio"] == {"score": 9, "name": "Примеряфеицшпчб Примерщюагоэсгв", "reason": "", "comment": ""}


@pytest.mark.parametrize("bad", [0, 11, -1, 7.5, "7", True, False])
def test_garbage_scores_are_refused(db, bad):
    book = _book(db)
    _audition(db, book)

    with pytest.raises(sc.ScoreError) as error:
        _set(db, book, bad)

    assert error.value.code == "bad_score"
    assert db.query(AuditionScore).count() == 0


def test_unknown_lane_is_refused(db):
    book = _book(db)
    _audition(db, book)
    with pytest.raises(sc.ScoreError) as error:
        _set(db, book, 5, lane="dictor")
    assert error.value.code == "bad_lane"


def test_a_pair_without_auditions_cannot_be_scored(db):
    book = _book(db)
    _audition(db, book, actor="Ветрова Ольга")
    with pytest.raises(sc.ScoreError) as error:
        _set(db, book, 5)
    assert error.value.code == "not_found"


def test_an_unknown_book_cannot_be_scored(db):
    with pytest.raises(sc.ScoreError) as error:
        sc.set_score(db, book_id="nope", role="Саяху", actor_name="X", lane="author", score=5, voter_uid="u", voter_name="n")
    assert error.value.code == "not_found"


def test_lane_labels_come_from_the_book_author_and_the_owner_account(db, monkeypatch):
    monkeypatch.setattr(sc.settings, "owner_telegram_id", "777")
    db.add(Author(id="au1", name="Примервлщющючб Примернкяхаххчб", slug="user_example_нзбпщнюиб"))
    db.add(TelegramAuthAccount(telegram_user_id="777", role="admin", display_name="Примеряфеицшпчб Примерщюагоэсгв"))
    book = _book(db, author_id="au1")

    assert sc.lane_labels(db, book) == {"author": "Пяволов", "studio": "Сыкыдов"}


def test_studio_label_prefers_the_linked_account_name_over_the_telegram_name(db, monkeypatch):
    """На проде Telegram-имя владельца — «Примерфчяблеяд Примербиебипге», а учётка переименована в «Примеряфеицшпчб Примерщюагоэсгв»:
    подпись «Aero» под шкалой никто не узнаёт."""
    from app.models import User

    monkeypatch.setattr(sc.settings, "owner_telegram_id", "777")
    db.add(User(id="u-owner", login="owner", password_hash="x", display_name="Примеряфеицшпчб Примерщюагоэсгв"))
    db.add(TelegramAuthAccount(telegram_user_id="777", role="admin", display_name="Примерфчяблеяд Примербиебипге", user_id="u-owner"))

    assert sc.lane_labels(db, _book(db))["studio"] == "Сыкыдов"


def test_lane_labels_fall_back_to_words(db, monkeypatch):
    monkeypatch.setattr(sc.settings, "owner_telegram_id", "")
    assert sc.lane_labels(db, _book(db)) == {"author": "Автор", "studio": "Студия"}


# ---------- причина низкой оценки автора (04.10) ----------

def test_the_author_gives_a_reason_for_a_low_score(db):
    book = _book(db)
    _audition(db, book)
    result = sc.set_score(db, book_id=book.id, role="Саяху", actor_name="Примершупядяобб Примерцвфмллрив", lane="author",
                          score=4, reason="manner", voter_uid="a", voter_name="Примервлщющючб Примернкяхаххчб")
    assert result["author"] == {"score": 4, "name": "Примервлщющючб Примернкяхаххчб", "reason": "manner", "comment": ""}


def test_raising_the_score_to_seven_or_more_clears_the_reason(db):
    book = _book(db)
    _audition(db, book)
    kw = dict(book_id=book.id, role="Саяху", actor_name="Примершупядяобб Примерцвфмллрив", lane="author", voter_uid="a", voter_name="А")
    sc.set_score(db, score=3, reason="voice", **kw)
    assert sc.set_score(db, score=7, **kw)["author"]["reason"] == ""


def test_a_six_asks_for_a_reason_like_any_score_that_leads_to_a_rejection(db):
    """С 11.10 причину спрашивают при любой оценке ниже 7 — там же, где начинается отказ."""
    book = _book(db)
    _audition(db, book)
    kw = dict(book_id=book.id, role="Саяху", actor_name="Примершупядяобб Примерцвфмллрив", lane="author", voter_uid="a", voter_name="А")
    assert sc.set_score(db, score=6, reason="voice", **kw)["author"]["reason"] == "voice"


def test_a_score_without_reason_keeps_the_given_one_only_when_still_low(db):
    """Повторное нажатие той же низкой оценки без причины не стирает уже данную."""
    book = _book(db)
    _audition(db, book)
    kw = dict(book_id=book.id, role="Саяху", actor_name="Примершупядяобб Примерцвфмллрив", lane="author", voter_uid="a", voter_name="А")
    sc.set_score(db, score=3, reason="all", **kw)
    assert sc.set_score(db, score=2, **kw)["author"]["reason"] == "all"


@pytest.mark.parametrize("lane,score,reason", [
    ("studio", 3, "voice"),     # причину спрашивают только у автора
    ("author", 7, "voice"),     # и только при оценке ниже 7
    ("author", 3, "смех"),      # и только из трёх вариантов
])
def test_a_reason_where_it_does_not_belong_is_refused(db, lane, score, reason):
    book = _book(db)
    _audition(db, book)
    with pytest.raises(sc.ScoreError) as error:
        sc.set_score(db, book_id=book.id, role="Саяху", actor_name="Примершупядяобб Примерцвфмллрив", lane=lane,
                     score=score, reason=reason, voter_uid="a", voter_name="А")
    assert error.value.code == "bad_reason"


def _scored(db, score=4):
    book = _book(db)
    _audition(db, book)
    kw = dict(book_id=book.id, role="Саяху", actor_name="Примершупядяобб Примерцвфмллрив", lane="author", voter_uid="a",
              voter_name="Примернкяхаххчб Примервлщющючб")
    sc.set_score(db, score=score, **kw)
    return book, kw


def test_the_author_comments_in_own_words_and_it_survives_a_score_change(db):
    book, kw = _scored(db)
    sc.set_comment(db, comment="  Голос должен быть звонким и девчачьим ", **kw)
    assert sc.set_score(db, score=8, **kw)["author"]["comment"] == "Голос должен быть звонким и девчачьим"


def test_a_comment_needs_a_score_first(db):
    book = _book(db)
    _audition(db, book)
    with pytest.raises(sc.ScoreError) as error:
        sc.set_comment(db, book_id=book.id, role="Саяху", actor_name="Примершупядяобб Примерцвфмллрив", lane="author",
                       comment="звонче", voter_uid="a", voter_name="А")
    assert error.value.code == "no_score"


def test_a_too_long_comment_is_refused(db):
    book, kw = _scored(db)
    with pytest.raises(sc.ScoreError) as error:
        sc.set_comment(db, comment="а" * (sc.COMMENT_MAX_CHARS + 1), **kw)
    assert error.value.code == "comment_too_long"


def test_the_owner_notice_carries_score_reason_and_comment(db):
    book, kw = _scored(db, score=4)
    sc.set_score(db, score=4, reason="voice", **kw)
    sc.set_comment(db, comment="звонче и моложе", **kw)
    text = sc.reaction_notice(db, book_id=book.id, role="Саяху", actor_name="Примершупядяобб Примерцвфмллрив")
    assert "о пробе «Саяху»" in text and "Примершупядяобб Примерцвфмллрив, 4/10" in text
    assert "Тембр не этого героя" in text and "«звонче и моложе»" in text


def test_no_notice_for_a_bare_score(db):
    book, _ = _scored(db)
    assert sc.reaction_notice(db, book_id=book.id, role="Саяху", actor_name="Примершупядяобб Примерцвфмллрив") == ""

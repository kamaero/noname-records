"""Утверждение одной функцией (пакет C1): бот и сайт зовут одну цепочку."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import AudioFile, AuditionRejection, Character, ScriptBook, ScriptChapter
from app.services import cast_assign
from app.services.audio_uploads import derive_book_code


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, autoflush=False)() as session:
        session.add(ScriptBook(id="b1", title="Карта Приозерья", display_title="Карта Приозерья",
                               source_filename="x", source_format="docx", total_chars=0, author_sheets_x1000=0,
                               chapter_count=1, has_chapters="true", status="processing"))
        session.add(ScriptChapter(id="b1-1", book_id="b1", chapter_index=1, chapter_title="Глава 1"))
        session.add(Character(id="c1", book_id="b1", name="Дракон", actor_name="", appears_in="1"))
        for actor in ("Ветрова Ольга", "Иванов Пётр"):
            session.add(AudioFile(book_code=derive_book_code("Карта Приозерья"), original_filename="p.wav",
                                  stored_key=f"k/{actor}", mime_type="audio/wav", size_bytes=1, chapter="",
                                  role="Дракон", actor_name=actor, kind="audition"))
        session.commit()
        yield session


def test_approval_votes_schedules_rejections_and_notifies(db):
    sent = []
    result = cast_assign.assign_actor(db, character=db.get(Character, "c1"), actor_name="Ветрова Ольга",
                                      voter_uid="u11", voter_name="Фернос Фарита", roles={"admin"},
                                      notify=lambda db, **kw: sent.append(kw) or {"notified": True})
    assert db.get(Character, "c1").actor_name == "Ветрова Ольга"
    assert result["vote"]["changed"] and result["approval"] == {"notified": True}
    assert sent[0]["actor_name"] == "Ветрова Ольга" and sent[0]["role"] == "Дракон"
    assert [r.actor_name for r in db.query(AuditionRejection).all()] == ["Иванов Пётр"]


def test_an_agent_only_proposes(db):
    cast_assign.assign_actor(db, character=db.get(Character, "c1"), actor_name="Ветрова Ольга",
                             voter_uid="u70", voter_name="Инга", roles={"agent"}, agent_only=True,
                             notify=lambda db, **kw: {})
    assert db.query(AuditionRejection).count() == 0

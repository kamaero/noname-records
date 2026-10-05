"""Движок звуковой разметки: подменённая модель, база в памяти, временный каталог."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
import app.v2.models  # noqa: F401
from app.db import Base
from tests.consilium_book import BOOK, build_book


@pytest.fixture()
def book():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as db:
        build_book(db)
    return factory


def new_run(factory):
    from app.models import BackgroundRun
    with factory() as db:
        run = BackgroundRun(job_kind="sound", entity_type="script_book", entity_id=BOOK,
                            run_key=f"sound:{BOOK}", status="running", meta_json="{}")
        db.add(run); db.commit()
        return run.id


def fake_ask(calls):
    def ask(model, system, user, schema):
        calls.append(user)
        if "pairs" in schema["properties"]:
            return {"pairs": []}
        return {"scenes": [{"start": 0, "place": {"id": "", "name": "Лес", "description": "чаща",
                                                  "ambience_queries": ["forest"]},
                            "time_of_day": "день", "weather": "", "ambience": "птицы", "mood": "покой",
                            "music_queries": ["calm"]}], "transitions": [], "sounds": []}
    return ask


def test_run_marks_every_chapter_and_second_chapter_sees_first_place(book, tmp_path):
    from app.models import SoundMarker, SoundPlace
    from app.services.sound_engine import run_sound
    calls = []
    result = run_sound(session_factory=book, book_id=BOOK, run_id=new_run(book), mode="all",
                       ask=fake_ask(calls), read_credits=lambda: None, notify=lambda _t: None, root=str(tmp_path))
    assert result["status"] == "done" and result["chapters_done"] == 2
    assert "Лес: чаща" in calls[1]                       # вторая глава видит место первой
    with book() as db:
        assert db.query(SoundPlace).count() == 1
        assert db.query(SoundMarker).filter_by(kind="scene").count() == 2


def test_rest_mode_skips_chapters_already_read(book, tmp_path):
    from app.services.sound_engine import run_sound
    run_sound(session_factory=book, book_id=BOOK, run_id=new_run(book), mode="all",
              ask=fake_ask([]), read_credits=lambda: None, notify=lambda _t: None, root=str(tmp_path))
    calls = []
    result = run_sound(session_factory=book, book_id=BOOK, run_id=new_run(book), mode="rest",
                       ask=fake_ask(calls), read_credits=lambda: None, notify=lambda _t: None, root=str(tmp_path))
    assert result["chapters_skipped"] == 2 and all("Глава:" not in c for c in calls)


def test_chapter_mode_reads_only_that_chapter(book, tmp_path):
    from app.services.sound_engine import run_sound
    calls = []
    run_sound(session_factory=book, book_id=BOOK, run_id=new_run(book), mode="chapter", chapter_id="c2",
              ask=fake_ask(calls), read_credits=lambda: None, notify=lambda _t: None, root=str(tmp_path))
    assert len([c for c in calls if "Глава:" in c]) == 1 and "Стой" in calls[0]


def test_reading_a_chapter_outdates_its_archived_session(book, tmp_path):
    """Прогон применяет маркеры главы (`sound_store.apply_chapter`) — её архивный
    проект (если есть) с этого момента не соответствует разметке."""
    from app.models import ScriptChapter
    from app.services.sound_engine import run_sound
    from app.time_utils import utcnow_naive

    with book() as db:
        db.get(ScriptChapter, "c2").session_archived_at = utcnow_naive()
        db.commit()

    run_sound(session_factory=book, book_id=BOOK, run_id=new_run(book), mode="chapter", chapter_id="c2",
              ask=fake_ask([]), read_credits=lambda: None, notify=lambda _t: None, root=str(tmp_path))

    with book() as db:
        assert db.get(ScriptChapter, "c2").session_outdated_at is not None
        # Другая глава прогоном не тронута — своей архивной сессии у неё не было.
        assert db.get(ScriptChapter, "c1").session_outdated_at is None


def test_failed_chapter_is_incomplete_and_run_goes_on(book, tmp_path):
    from app.services.sound_engine import load_sidecar, run_sound
    def ask(model, system, user, schema):
        if "Стой" in user:
            raise RuntimeError("503")
        return fake_ask([])(model, system, user, schema)
    result = run_sound(session_factory=book, book_id=BOOK, run_id=new_run(book), mode="all", ask=ask,
                       read_credits=lambda: None, notify=lambda _t: None, root=str(tmp_path))
    assert result["status"] == "done" and result["chapters_incomplete"] == 1


def test_malformed_answer_marks_chapter_incomplete_and_run_goes_on(book, tmp_path):
    """RULING: `validate()` может упасть на кривом ответе модели (`"start": null`) —
    падает только эта глава, прогон книги идёт дальше."""
    from app.services.sound_engine import run_sound

    def ask(model, system, user, schema):
        if "pairs" in schema["properties"]:
            return {"pairs": []}
        if "Стой" in user:
            return {"scenes": [{"start": None, "place": {"id": "", "name": "Лес", "description": "чаща",
                                                          "ambience_queries": ["forest"]},
                                "time_of_day": "день", "weather": "", "ambience": "птицы", "mood": "покой",
                                "music_queries": ["calm"]}], "transitions": [], "sounds": []}
        return fake_ask([])(model, system, user, schema)

    result = run_sound(session_factory=book, book_id=BOOK, run_id=new_run(book), mode="all", ask=ask,
                       read_credits=lambda: None, notify=lambda _t: None, root=str(tmp_path))
    assert result["status"] == "done" and result["chapters_incomplete"] == 1


def test_estimate_counts_unread_paragraphs(book, tmp_path):
    from app.services.sound_engine import estimate
    with book() as db:
        plan = estimate(db, BOOK, root=str(tmp_path))
    assert plan["chapters_total"] == 2 and plan["chapters_to_read"] == {"rest": 2, "all": 2}
    assert plan["estimate_rub"]["all"] >= 10


def test_latest_run_is_separate_from_consilium(book):
    from app.services.consilium_engine import latest_run
    new_run(book)
    with book() as db:
        assert latest_run(db, BOOK) is None
        assert latest_run(db, BOOK, kind="sound")["status"] == "running"


# --- Final review fixes: I2, I3 -------------------------------------------------


def _ask_two_places(pairs_answer_or_raise):
    """Первая глава — место «Лес», вторая («Стой» в тексте) — «Гора»: две карточки места,
    чтобы был смысл звать платную склейку. Ответ/исключение на вызове со схемой пар
    приходит из `pairs_answer_or_raise`."""
    def ask(model, system, user, schema):
        if "pairs" in schema["properties"]:
            if callable(pairs_answer_or_raise):
                return pairs_answer_or_raise()
            return pairs_answer_or_raise
        if "Стой" in user:
            return {"scenes": [{"start": 0, "place": {"id": "", "name": "Гора", "description": "скалы",
                                                       "ambience_queries": ["wind"]},
                                "time_of_day": "день", "weather": "", "ambience": "ветер", "mood": "тревога",
                                "music_queries": ["epic"]}], "transitions": [], "sounds": []}
        return fake_ask([])(model, system, user, schema)
    return ask


def test_merge_call_failure_keeps_run_done_and_records_error(book, tmp_path):
    """I2: сорвавшийся вызов склейки не валит весь прогон — главы применены, run 'done'."""
    from app.services.sound_engine import run_sound

    def boom():
        raise RuntimeError("503")

    result = run_sound(session_factory=book, book_id=BOOK, run_id=new_run(book), mode="all",
                       ask=_ask_two_places(boom), read_credits=lambda: None, notify=lambda _t: None,
                       root=str(tmp_path))
    assert result["status"] == "done"
    assert result["merge_error"] == "RuntimeError: 503"
    assert result["chapters_done"] == 2 and result["markers"] == 2
    with book() as db:
        from app.models import SoundMarker
        assert db.query(SoundMarker).filter_by(kind="scene").count() == 2


def test_rest_run_with_nothing_new_to_read_skips_merge_call(book, tmp_path):
    """I3: весь `rest` уже прочитан раньше — платный вызов склейки не нужен и не делается."""
    from app.services.sound_engine import run_sound

    run_sound(session_factory=book, book_id=BOOK, run_id=new_run(book), mode="all",
             ask=_ask_two_places({"pairs": []}), read_credits=lambda: None, notify=lambda _t: None,
             root=str(tmp_path))

    def forbidden(model, system, user, schema):
        raise AssertionError("модель не должна была понадобиться — читать уже нечего")

    result = run_sound(session_factory=book, book_id=BOOK, run_id=new_run(book), mode="rest",
                       ask=forbidden, read_credits=lambda: None, notify=lambda _t: None, root=str(tmp_path))
    assert result["status"] == "done" and result["chapters_skipped"] == 2
    with book() as db:
        from app.models import SoundPlace
        assert db.query(SoundPlace).count() == 2  # оба места — от первого прогона


def test_the_sound_step_model_from_settings_reaches_the_call(book, tmp_path, monkeypatch):
    from app.services.sound_engine import run_sound
    from app.services import step_models
    monkeypatch.setattr(step_models, "step_model", lambda key: ("openrouter", "vendor/sound-model"))
    monkeypatch.setattr(step_models, "require_key_for", lambda key: None)
    seen = []

    def ask(model, system, user, schema):
        seen.append(model)
        return fake_ask([])(model, system, user, schema)
    run_sound(session_factory=book, book_id=BOOK, run_id=new_run(book), mode="chapter", chapter_id="c2",
              ask=ask, read_credits=lambda: None, notify=lambda _t: None, root=str(tmp_path))
    assert seen and set(seen) == {"vendor/sound-model"}


def test_sound_without_its_key_stops_before_any_call(book, tmp_path, monkeypatch):
    from app.services.sound_engine import run_sound
    from app.services import provider_keys
    monkeypatch.setattr(provider_keys, "provider_key", lambda name: "")
    monkeypatch.setattr("app.pipeline.llm_client.call_chat",
                        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("платный вызов без ключа")))
    result = run_sound(session_factory=book, book_id=BOOK, run_id=new_run(book), mode="all",
                       read_credits=lambda: None, notify=lambda _t: None, root=str(tmp_path))
    assert result["status"] == "stopped" and "RouterAI" in result["reason"]

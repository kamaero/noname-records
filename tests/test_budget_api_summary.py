import json
from asyncio import run
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.budget_api import SaveBookCardPayload, SaveBudgetCharacterPayload, build_budget_api_handlers
from app.db import Base
from app.models import BookBudget, Character, CharacterBudgetSnapshot, ScriptBook, ScriptChapter


def test_budget_api_prices_narrator_as_fixed_cost_not_replicas() -> None:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(engine)

    with SessionLocal() as db:
        db.add(
            ScriptBook(
                id="b1",
                title="Budget Book",
                display_title="Budget Book",
                source_filename="book.txt",
                source_format="txt",
                chapter_count=2,
                status="author_review",
            )
        )
        db.add_all(
            [
                ScriptChapter(id="c1", book_id="b1", chapter_index=1, chapter_title="Глава 1", status="pending_review"),
                ScriptChapter(id="c2", book_id="b1", chapter_index=2, chapter_title="Глава 3", status="pending_review"),
            ]
        )
        db.add(Character(id="n1", book_id="b1", char_map_id="mn", name="Рассказчик"))
        db.add(Character(id="d1", book_id="b1", char_map_id="md", name="Дгарнин"))
        db.add(CharacterBudgetSnapshot(book_id="b1", character_id="n1", lines_count=5, approx_seconds=300, total_rub=999999))
        db.add(CharacterBudgetSnapshot(book_id="b1", character_id="d1", lines_count=2, approx_seconds=60, total_rub=1000))
        db.add(BookBudget(book_id="b1", narrative_cost_rub=4200, sound_engineer_cost_rub=500, extra_cost_rub=100))
        db.commit()

    handlers = build_budget_api_handlers(
        {
            "is_authenticated": lambda _request: True,
            "has_workspace_full_access": lambda _request: True,
            "has_any_role": lambda _request, _roles: True,
            "is_agent": lambda _request: False,
            "session_payload": lambda _request: {"uid": "u1"},
            "session_roles": lambda _request: {"admin"},
            "session_display_name": lambda _request: "tester",
            "SessionLocal": SessionLocal,
            "get_book": lambda db, book_id: db.query(ScriptBook).filter(ScriptBook.id == book_id).first(),
            "build_character_style_maps": lambda _characters: None,
            "format_dt": lambda value: str(value) if value else None,
            "BookBudget": BookBudget,
            "Character": Character,
            "CharacterBudgetSnapshot": CharacterBudgetSnapshot,
            "ScriptChapter": ScriptChapter,
        }
    )

    response = handlers["api_book_budget"](object(), "b1")
    payload = json.loads(response.body)

    assert payload["totals"]["character_total_rub"] == 1000
    assert payload["totals"]["narrator_total_rub"] == 4200
    assert payload["totals"]["grand_total_rub"] == 5800
    assert payload["summary"]["dialogue_lines_count"] == 2
    assert payload["summary"]["narrator_source_lines_count"] == 5
    assert payload["summary"]["audiobook_seconds"] == 360
    assert payload["summary"]["source_chapter_count"] == 3
    assert payload["summary"]["processing_chapter_count"] == 2

    narrator = next(row for row in payload["characters"] if row["is_narrator"])
    assert narrator["lines_count"] == 0
    assert narrator["approx_seconds"] == 300
    assert narrator["total_rub"] == 4200


def test_budget_api_allows_author_to_save_book_card_and_character_palette() -> None:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(engine)

    with SessionLocal() as db:
        db.add(
            ScriptBook(
                id="b1",
                title="Budget Book",
                display_title="Budget Book",
                source_filename="book.txt",
                source_format="txt",
                status="author_review",
            )
        )
        db.add(Character(id="c1", book_id="b1", char_map_id="m1", name="Хубол"))
        db.commit()

    def has_any_role(request, roles):
        return bool("admin" in request.roles or set(request.roles).intersection(roles))

    handlers = build_budget_api_handlers(
        {
            "is_authenticated": lambda _request: True,
            "has_workspace_full_access": lambda _request: False,
            "has_any_role": has_any_role,
            "is_agent": lambda _request: False,
            "session_payload": lambda _request: {"uid": "author-1"},
            "session_roles": lambda _request: {"author"},
            "session_display_name": lambda _request: "Author",
            "SessionLocal": SessionLocal,
            "get_book": lambda db, book_id: db.query(ScriptBook).filter(ScriptBook.id == book_id).first(),
            "build_character_style_maps": lambda _characters: None,
            "format_dt": lambda value: str(value) if value else None,
            "BookBudget": BookBudget,
            "Character": Character,
            "CharacterBudgetSnapshot": CharacterBudgetSnapshot,
            "ScriptChapter": ScriptChapter,
        }
    )
    request = SimpleNamespace(roles={"author"})

    card_response = run(
        handlers["api_save_book_card"](
            request,
            "b1",
            SaveBookCardPayload(book_annotation="Тестовая аннотация"),
        )
    )
    palette_response = run(
        handlers["api_save_budget_character"](
            request,
            "c1",
            SaveBudgetCharacterPayload(
                character_color="#112233",
                character_text_color="#EEDDEE",
                character_font_weight="800",
                character_font_style="italic",
            ),
        )
    )

    assert card_response == {"ok": True}
    assert palette_response == {"ok": True}
    with SessionLocal() as db:
        book = db.query(ScriptBook).filter(ScriptBook.id == "b1").one()
        character = db.query(Character).filter(Character.id == "c1").one()
        assert book.book_annotation == "Тестовая аннотация"
        assert character.character_color == "#112233"
        assert character.character_text_color == "#EEDDEE"
        assert character.character_font_weight == "800"
        assert character.character_font_style == "italic"


def test_saving_a_tentative_actor_on_the_character_card_returns_an_audition_approval() -> None:
    """Карточка персонажа — один из двух вызывающих `notify_role_approved`
    (`app/api/budget_api.py`, ~572). Проверяется сквозной путь: «Имя?» доходит до
    ответа API как `approval.kind == "audition"`, а не как прежнее молчаливое
    `reason: "tentative"`. Учётки в базе нет — отправка реальная не случится
    (`TELEGRAM_NOTIFY_ENABLED=false` из `tests/conftest.py`), а путь всё равно должен
    дойти до конца и вернуть форму ответа."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(engine)

    with SessionLocal() as db:
        db.add(
            ScriptBook(
                id="b1",
                title="Крылья Полумрака",
                display_title="Крылья Полумрака",
                source_filename="book.txt",
                source_format="txt",
                status="author_review",
            )
        )
        db.add(Character(id="c1", book_id="b1", char_map_id="m1", name="Химера"))
        db.commit()

    handlers = build_budget_api_handlers(
        {
            "is_authenticated": lambda _request: True,
            "has_workspace_full_access": lambda _request: True,
            "has_any_role": lambda _request, _roles: True,
            "is_agent": lambda _request: False,
            "session_payload": lambda _request: {"uid": "u1"},
            "session_roles": lambda _request: {"admin"},
            "session_display_name": lambda _request: "tester",
            "SessionLocal": SessionLocal,
            "get_book": lambda db, book_id: db.query(ScriptBook).filter(ScriptBook.id == book_id).first(),
            "build_character_style_maps": lambda _characters: None,
            "format_dt": lambda value: str(value) if value else None,
            "BookBudget": BookBudget,
            "Character": Character,
            "CharacterBudgetSnapshot": CharacterBudgetSnapshot,
            "ScriptChapter": ScriptChapter,
        }
    )
    request = SimpleNamespace(roles={"admin"})

    response = run(
        handlers["api_save_budget_character"](
            request,
            "c1",
            SaveBudgetCharacterPayload(actor_name="Гульнара Ткач?"),
        )
    )

    assert response["ok"] is True
    assert response["approval"]["kind"] == "audition"
    assert response["approval"]["actor_name"] == "Гульнара Ткач"
    with SessionLocal() as db:
        assert db.get(Character, "c1").actor_name == "Гульнара Ткач?"

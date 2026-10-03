"""The v2 reader routes exist, are guarded by the session cookie, and answer JSON."""
from fastapi.testclient import TestClient

from app import main as app_main
from app.auth import session_serializer
from app.main import app


def _client_with_session(roles):
    client = TestClient(app)
    token = session_serializer.dumps({"uid": "u1", "sub": "dictor", "roles": roles})
    client.cookies.set("session", token)
    return client


def test_v2_routes_require_a_session():
    client = TestClient(app)
    assert client.get("/api/v2/chapters/some-id/script").status_code == 401
    assert client.get("/api/v2/books/some-id/chapters").status_code == 401


def test_a_published_chapter_is_open_to_any_logged_in_user(monkeypatch):
    """Опубликованную главу читают все вошедшие; про неопубликованную — тест ниже."""
    monkeypatch.setattr("app.v2.api._chapter_is_published", lambda db, chapter_id: True)
    calls = []

    def fake_chapter(db, chapter_id, *, can_edit=False, can_voice=False):
        calls.append(("chapter", chapter_id))
        return {"book": {"id": "b", "title": "B"}, "chapter": {"id": chapter_id}, "chapters": [], "cast": [],
                "segments": [], "can_edit": can_edit, "can_voice": can_voice}

    def fake_book(db, book_id):
        calls.append(("book", book_id))
        return None

    monkeypatch.setattr("app.v2.api.build_chapter_payload", fake_chapter)
    monkeypatch.setattr("app.v2.api.build_book_chapters", fake_book)

    client = _client_with_session(["dictor"])
    response = client.get("/api/v2/chapters/ch-9/script")
    assert response.status_code == 200
    assert response.json()["chapter"] == {"id": "ch-9"}
    assert response.json()["can_edit"] is False

    assert client.get("/api/v2/books/missing/chapters").status_code == 404
    assert calls == [("chapter", "ch-9"), ("book", "missing")]


def test_the_payload_tells_the_reader_which_of_the_two_doors_is_open(monkeypatch):
    """`can_edit` opens the markup layer; `can_voice` opens stress and palette."""
    monkeypatch.setattr(
        "app.v2.api.build_chapter_payload",
        lambda db, chapter_id, *, can_edit=False, can_voice=False: {
            "chapter": {"id": chapter_id}, "can_edit": can_edit, "can_voice": can_voice,
        },
    )

    monkeypatch.setattr("app.v2.api._chapter_is_published", lambda db, chapter_id: True)

    def flags(role: str) -> tuple[bool, bool]:
        body = _client_with_session([role]).get("/api/v2/chapters/c/script").json()
        return body["can_edit"], body["can_voice"]

    assert flags("author") == (True, True)
    assert flags("admin") == (True, True)
    assert flags("dictor") == (False, True)


def test_an_unpublished_chapter_is_closed_to_everyone_but_an_editor(monkeypatch):
    """Редирект «Подготовки» в «Запись» живёт на клиенте: маршрут перестал
    открываться, а ручка отвечала. Здесь та сторона, которую не обойти адресом."""
    monkeypatch.setattr("app.v2.api._chapter_is_published", lambda db, chapter_id: False)
    monkeypatch.setattr(
        "app.v2.api.build_chapter_payload",
        lambda db, chapter_id, *, can_edit=False, can_voice=False: {"chapter": {"id": chapter_id}},
    )

    assert _client_with_session(["dictor"]).get("/api/v2/chapters/ch-9/script").status_code == 404
    assert _client_with_session(["dictor"]).get("/api/v2/chapters/ch-9/source").status_code == 404
    assert _client_with_session(["author"]).get("/api/v2/chapters/ch-9/script").status_code == 200


def test_chapter_source_is_open_to_any_logged_in_user(monkeypatch):
    monkeypatch.setattr("app.v2.api._chapter_is_published", lambda db, chapter_id: True)
    monkeypatch.setattr(
        "app.v2.api.build_source_view",
        lambda db, chapter_id: None if chapter_id == "missing" else {"ok": True, "chapter": {"id": chapter_id}, "blocks": []},
    )
    client = _client_with_session(["dictor"])
    assert client.get("/api/v2/chapters/ch-9/source").json()["chapter"] == {"id": "ch-9"}
    assert client.get("/api/v2/chapters/missing/source").status_code == 404
    assert TestClient(app).get("/api/v2/chapters/ch-9/source").status_code == 401


def test_routes_are_registered_directly_on_the_app():
    paths = {getattr(route, "path", "") for route in app_main.app.routes}
    assert "/api/v2/chapters/{chapter_id}/script" in paths
    assert "/api/v2/chapters/{chapter_id}/source" in paths
    assert "/api/v2/books/{book_id}/chapters" in paths
    assert "/api/v2/books/{book_id}/model" in paths
    assert "/api/v2/books/{book_id}/characters" in paths
    assert "/api/v2/chapters/{chapter_id}/approve" in paths
    for suffix in ("stress-queue", "stress-term", "cast", "profile", "profile/sync", "profile/apply"):
        assert f"/api/v2/books/{{book_id}}/{suffix}" in paths


# --- the book-level endpoints against a seeded in-memory database ---------------------------
#
# The app's own SessionLocal points at a per-thread in-memory sqlite; TestClient runs
# handlers in another thread, so the routes get a StaticPool session factory instead.

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Author, AuthorPronunciation, Character, OperatorIntervention, ScriptBook, ScriptChapter
from app.v2.models import V2Attribution, V2Segment, V2StressMark

BOOK, AUTHOR, CH1 = "book-api", "author-api", "ch-api"


def _shared_session_factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)


def _seed_book(SessionLocal, *, author_id=AUTHOR):
    with SessionLocal() as db:
        db.add(Author(id=AUTHOR, name="Белозёров", slug="belozerov"))
        db.add(ScriptBook(id=BOOK, title="Крылья", source_filename="k.txt", source_format="txt", author_id=author_id))
        db.add(ScriptChapter(id=CH1, book_id=BOOK, chapter_index=1, chapter_title="Глава 1"))
        db.add(Character(id="c1", book_id=BOOK, name="Дгарнин", actor_name="Иван Петров"))
        text = "Дгарнин сидел в изгибе ветвей."
        seg = f"{CH1}:00000"
        db.add(V2Segment(id=seg, book_id=BOOK, chapter_id=CH1, ordinal=0, text=text, char_end=len(text)))
        db.add(V2Attribution(id="a1", segment_id=seg, span_start=0, span_end=len(text), speaker="Дгарнин", version=1))
        db.add(V2StressMark(id="s1", segment_id=seg, word_start=0, word_end=7, vowel_offset=1, source="dict", version=1))
        db.commit()


def _patched_routes(monkeypatch):
    SessionLocal = _shared_session_factory()
    _seed_book(SessionLocal)
    monkeypatch.setattr("app.v2.api.SessionLocal", SessionLocal)
    # The real lookup would load the 5.7 MB base dictionary; the queue only needs «is it a homograph».
    monkeypatch.setattr("app.v2.stress_ops.default_dict_lookup", lambda word: None)
    return SessionLocal


def test_book_endpoints_require_a_session(monkeypatch):
    _patched_routes(monkeypatch)
    client = TestClient(app)
    assert client.get(f"/api/v2/books/{BOOK}/stress-queue").status_code == 401
    assert client.get(f"/api/v2/books/{BOOK}/cast").status_code == 401
    assert client.get(f"/api/v2/books/{BOOK}/profile").status_code == 401
    assert client.post(f"/api/v2/books/{BOOK}/stress-term", json={}).status_code == 401
    assert client.post(f"/api/v2/books/{BOOK}/profile/sync").status_code == 401
    assert client.post(f"/api/v2/books/{BOOK}/profile/apply", json={}).status_code == 401
    # Через настоящий маршрут, а не вызовом функции: опечатка в пути, чужой метод
    # или незарегистрированный роут прошли бы мимо теста, зовущего ручку напрямую.
    assert client.get(f"/api/v2/books/{BOOK}/character-map").status_code == 401
    assert client.post(f"/api/v2/books/{BOOK}/character-map/delete", json={}).status_code == 401
    assert client.post(f"/api/v2/books/{BOOK}/character-map/adopt", json={}).status_code == 401
    assert client.post(f"/api/v2/books/{BOOK}/character-map/merge", json={}).status_code == 401
    assert client.post(f"/api/v2/books/{BOOK}/character-map/rename", json={}).status_code == 401
    assert client.post(f"/api/v2/books/{BOOK}/character-map/update", json={}).status_code == 401
    assert client.post(f"/api/v2/books/{BOOK}/character-map/acknowledge", json={}).status_code == 401
    assert client.post(f"/api/v2/books/{BOOK}/character-map/forget", json={}).status_code == 401
    assert client.post(f"/api/v2/books/{BOOK}/character-map/checked", json={}).status_code == 401
    # Не v2-маршрут, но тот же обычай: любая ручка записи требует сессии.
    assert client.post("/api/recording/files/whatever/delete").status_code == 401


def test_the_character_map_is_closed_to_a_dictor_through_the_real_route(monkeypatch):
    """Тот же рубеж, но пройденный маршрутом целиком.

    Час назад в этом проекте редирект уводил диктора из «Подготовки», а ручки
    продолжали отвечать любому вошедшему. Проверка, зовущая функцию напрямую,
    такую дыру не увидит: она минует и путь, и метод, и саму регистрацию."""
    _patched_routes(monkeypatch)

    for path in (
        f"/api/v2/books/{BOOK}/character-map",
        f"/api/v2/books/{BOOK}/character-map/delete",
        f"/api/v2/books/{BOOK}/character-map/adopt",
        f"/api/v2/books/{BOOK}/character-map/merge",
        f"/api/v2/books/{BOOK}/character-map/rename",
        f"/api/v2/books/{BOOK}/character-map/update",
        f"/api/v2/books/{BOOK}/character-map/acknowledge",
        f"/api/v2/books/{BOOK}/character-map/forget",
        f"/api/v2/books/{BOOK}/character-map/checked",
    ):
        client = _client_with_session(["dictor"])
        is_post = path.endswith(("delete", "adopt", "merge", "rename", "update", "acknowledge", "forget", "checked"))
        response = client.post(path, json={}) if is_post else client.get(path)
        assert response.status_code == 403, path


def test_the_delete_route_is_closed_to_a_role_outside_the_studio():
    """Тот же обычай списка маршрутов для новой ручки удаления — но не с ролью
    `dictor` (той роли путь как раз открыт, только не на чужую запись — это
    проверено через настоящую строку в `tests/test_audio_deletion.py`), а с ролью,
    которую в раздел записи не пускают вовсе, — как `dictor_pro`/`dictor_neo` в
    `tests/test_recording_access.py`. Здесь это решает `require_recording_access`
    до всякого обращения к базе."""
    assert _client_with_session(["dictor_pro"]).post("/api/recording/files/whatever/delete").status_code == 403


def test_a_dictor_may_set_stress_but_not_touch_the_markup(monkeypatch):
    """The studio's line: stress and palette are his craft, the pipeline is not.

    This used to refuse him the stress term too, which left «работа с ударениями»
    meaning nothing for the people who do the reading.
    """
    _patched_routes(monkeypatch)
    client = _client_with_session(["dictor"])
    assert client.get(f"/api/v2/books/{BOOK}/stress-queue").status_code == 200
    assert client.get(f"/api/v2/books/{BOOK}/cast").status_code == 200
    assert client.get(f"/api/v2/books/{BOOK}/profile").status_code == 200
    body = {"word": "дгарнин", "stressed": "дгарни́н", "scope": "book"}
    assert client.post(f"/api/v2/books/{BOOK}/stress-term", json=body).status_code == 200
    # the author's profile spans every book of his: not a dictor's decision
    assert client.post(f"/api/v2/books/{BOOK}/profile/sync").status_code == 403
    assert client.post(f"/api/v2/books/{BOOK}/profile/apply", json={"overwrite": False}).status_code == 403
    assert client.post(f"/api/v2/books/{BOOK}/run", json={"steps": ["stress"]}).status_code == 403
    # Unknown book → 404 on every route.
    assert client.get("/api/v2/books/nope/stress-queue").status_code == 404
    assert client.get("/api/v2/books/nope/cast").status_code == 404
    assert client.get("/api/v2/books/nope/profile").status_code == 404


def test_stress_queue_and_cast_payloads(monkeypatch):
    _patched_routes(monkeypatch)
    client = _client_with_session(["dictor"])
    queue = client.get(f"/api/v2/books/{BOOK}/stress-queue").json()
    assert queue["ok"] is True
    assert [row["word"] for row in queue["unresolved"]] == ["ветвей", "изгибе", "сидел"]
    assert queue["homographs"] == []
    assert queue["counts"]["marked"] == 1

    cast = client.get(f"/api/v2/books/{BOOK}/cast").json()
    assert cast["ok"] is True and cast["book_id"] == BOOK
    assert [(c["name"], c["lines_count"], c["is_narrator"]) for c in cast["characters"]] == [("Рассказчик", 0, True), ("Дгарнин", 1, False)]
    assert cast["characters"][1]["appears_in_chapters"] == [1]


def test_stress_term_as_editor_writes_and_validates(monkeypatch):
    SessionLocal = _patched_routes(monkeypatch)
    client = _client_with_session(["author"])
    response = client.post(f"/api/v2/books/{BOOK}/stress-term", json={"word": "Дгарнин", "stressed": "Дгарни́н", "scope": "book"})
    assert response.status_code == 200
    assert response.json() == {"ok": True, "word": "дгарнин", "stressed": "Дгарни́н", "scope": "book",
                               "segments_updated": 1, "occurrences": 1, "skipped": 0, "notes": []}
    with SessionLocal() as db:
        assert db.get(ScriptBook, BOOK).pronunciation_notes == "дгарнин=Дгарни́н"
        assert db.query(V2StressMark).filter_by(version=2).count() == 1
        assert db.query(OperatorIntervention).filter_by(action_type="v2_stress_term").count() == 1

    for body, code in (
        ({"word": "дгарнин", "stressed": "дгарнин"}, "no_vowel_marked"),
        ({"word": "дгарнин", "stressed": "пупи́п"}, "stressed_does_not_match_word"),
        ({"word": "", "stressed": "пупи́п"}, "word_and_stressed_required"),
    ):
        response = client.post(f"/api/v2/books/{BOOK}/stress-term", json=body)
        assert response.status_code == 400 and response.json()["error"] == code, body
    assert client.post(f"/api/v2/books/{BOOK}/stress-term", content=b"not json").status_code == 400

    # scope=author without an author is a 400 too.
    with SessionLocal() as db:
        db.get(ScriptBook, BOOK).author_id = ""
        db.commit()
    response = client.post(f"/api/v2/books/{BOOK}/stress-term", json={"word": "дгарнин", "stressed": "дгарни́н", "scope": "author"})
    assert response.status_code == 400 and response.json()["error"] == "book_has_no_author"


def test_stress_term_with_author_scope_lands_in_the_profile(monkeypatch):
    SessionLocal = _patched_routes(monkeypatch)
    client = _client_with_session(["admin"])
    response = client.post(f"/api/v2/books/{BOOK}/stress-term", json={"word": "Дгарнин", "stressed": "Дгарни́н", "scope": "author"})
    assert response.status_code == 200 and response.json()["scope"] == "author"
    with SessionLocal() as db:
        row = db.query(AuthorPronunciation).one()
        assert (row.author_id, row.term, row.stressed) == (AUTHOR, "Дгарнин", "Дгарни́н")


def test_profile_sync_and_apply_as_editor(monkeypatch):
    SessionLocal = _patched_routes(monkeypatch)
    client = _client_with_session(["author"])
    profile = client.get(f"/api/v2/books/{BOOK}/profile").json()
    assert profile["ok"] is True and profile["author"]["slug"] == "belozerov" and profile["last_sync"] is None
    assert profile["counts"]["v2_segments"] == 1 and profile["counts"]["linked_characters"] == 0

    response = client.post(f"/api/v2/books/{BOOK}/profile/sync")
    assert response.status_code == 200
    assert response.json()["summary"]["characters_created"] == 1

    profile = client.get(f"/api/v2/books/{BOOK}/profile").json()
    assert profile["counts"]["linked_characters"] == 1 and profile["counts"]["author_characters"] == 1
    assert profile["last_sync"]["summary"]["characters_created"] == 1

    response = client.post(f"/api/v2/books/{BOOK}/profile/apply", json={"overwrite": True})
    assert response.status_code == 200 and response.json()["summary"]["overwrite"] is True
    assert client.post(f"/api/v2/books/{BOOK}/profile/apply", content=b"nope").status_code == 400
    with SessionLocal() as db:
        actions = sorted(r.action_type for r in db.query(OperatorIntervention).all())
        assert actions == ["v2_profile_apply", "v2_profile_sync"]


def test_choosing_a_model_stores_it_on_the_book_and_refuses_unknown_ones(monkeypatch):
    class FakeBook:
        def __init__(self):
            self.id = "b1"
            self.llm_provider = ""
            self.llm_model = ""

    book = FakeBook()

    class FakeSession:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, model, key): return book if key == "b1" else None
        def add(self, row): pass
        def commit(self): pass

    monkeypatch.setattr("app.v2.api.SessionLocal", lambda: FakeSession())
    monkeypatch.setattr("app.v2.api.record_operator_intervention", lambda *a, **k: None)

    client = _client_with_session(["admin"])
    ok = client.post("/api/v2/books/b1/model", json={"model_key": "muse-spark-1.3-contributor"})
    assert ok.status_code == 200
    assert ok.json()["model"]["trains_on_text"] is True
    assert (book.llm_provider, book.llm_model) == ("routerai", "meta/muse-spark-1.3-contributor")

    assert client.post("/api/v2/books/b1/model", json={"model_key": "gpt-9"}).status_code == 400
    assert _client_with_session(["dictor"]).post("/api/v2/books/b1/model", json={"model_key": "deepseek-v4-pro"}).status_code == 403


def test_creating_a_role_from_the_reader(monkeypatch):
    from app.v2.cast_ops import CreateRoleError

    def fake(db, *, book_id, name, actor_uid, actor_name=""):
        if not name.strip():
            raise CreateRoleError("name_required")
        if book_id == "missing":
            raise CreateRoleError("book_not_found")
        return {"created": True, "character_id": "c-new", "name": name.strip()}

    monkeypatch.setattr("app.v2.api.create_role", fake)
    monkeypatch.setattr("app.v2.api.SessionLocal", _FakeSessionFactory())

    client = _client_with_session(["author"])
    ok = client.post("/api/v2/books/b1/characters", json={"name": " Староста "})
    assert ok.status_code == 200 and ok.json()["name"] == "Староста" and ok.json()["created"] is True

    assert client.post("/api/v2/books/b1/characters", json={"name": "  "}).status_code == 400
    assert client.post("/api/v2/books/missing/characters", json={"name": "Кто-то"}).status_code == 404
    assert _client_with_session(["dictor"]).post("/api/v2/books/b1/characters", json={"name": "X"}).status_code == 403


def test_approving_a_chapter_is_for_editors_only(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        "app.v2.api.set_chapter_approved",
        lambda db, *, chapter_id, approved, actor_uid, actor_name="", notify=None: seen.update(chapter_id=chapter_id, approved=approved)
        or {"chapter_id": chapter_id, "chapter_index": 1, "status": "approved", "approved": approved},
    )
    monkeypatch.setattr("app.v2.api.SessionLocal", _FakeSessionFactory())

    ok = _client_with_session(["author"]).post("/api/v2/chapters/ch-1/approve", json={"approved": True})
    assert ok.status_code == 200 and ok.json()["approved"] is True
    assert seen == {"chapter_id": "ch-1", "approved": True}

    # an empty body means «проверено»; dictors may not touch it at all
    _client_with_session(["admin"]).post("/api/v2/chapters/ch-1/approve")
    assert seen["approved"] is True
    assert _client_with_session(["dictor"]).post("/api/v2/chapters/ch-1/approve").status_code == 403
    assert TestClient(app).post("/api/v2/chapters/ch-1/approve").status_code == 401


def test_verify_already_running_says_so_instead_of_lying_ok(monkeypatch):
    """Дедупликация сверки не должна выглядеть как «запущено»: кнопка сверки — зеркало
    кнопки распознавания, у которой та же ситуация честно отвечает 409."""
    monkeypatch.setattr("app.v2.api.enqueue_verify_for_chapter", lambda chapter_id: None)

    # Роль сменилась с диктора на автора: сверку заказывает тот же круг, что и
    # распознавание, — очередь `high` одна на всех и занимается надолго.
    response = _client_with_session(["author"]).post("/api/v2/chapters/ch-1/verify")

    assert response.status_code == 409
    assert response.json()["error"] == "already_queued"


def test_session_archive_reports_unchanged_instead_of_a_conflict(monkeypatch):
    """Отпечаток сессии сделал «нечего класть» законным, частым исходом кнопки —
    раньше `archive_chapter_session` не различал его среди прочих отказов, и любой
    повторный клик отвечал бы тем же 409, что и настоящий сбой."""
    monkeypatch.setattr(
        "app.v2.api.archive_chapter_session",
        lambda db, chapter_id: {"written": False, "reason": "unchanged", "replaced": ""},
    )
    monkeypatch.setattr("app.v2.api.SessionLocal", _FakeSessionFactory())

    response = _client_with_session(["author"]).post("/api/v2/chapters/ch-1/session-archive")

    assert response.status_code == 200
    assert response.json() == {"ok": True, "unchanged": True}


def test_session_archive_reports_the_backup_name_when_rebuilt(monkeypatch):
    monkeypatch.setattr(
        "app.v2.api.archive_chapter_session",
        lambda db, chapter_id: {
            "written": True, "reason": "",
            "replaced": "KP/Глава5/KP_Ch05 до 2026-09-15 10-42.sesx",
        },
    )
    monkeypatch.setattr("app.v2.api.SessionLocal", _FakeSessionFactory())

    response = _client_with_session(["author"]).post("/api/v2/chapters/ch-1/session-archive")

    assert response.status_code == 200
    assert response.json() == {"ok": True, "replaced": "KP/Глава5/KP_Ch05 до 2026-09-15 10-42.sesx"}


def test_session_archive_still_refuses_a_real_gap(monkeypatch):
    """Отрицательный контроль: настоящий отказ («asr_gaps» и подобные) остаётся 409,
    а не превращается в успех вместе с «unchanged»."""
    monkeypatch.setattr(
        "app.v2.api.archive_chapter_session",
        lambda db, chapter_id: {"written": False, "reason": "asr_gaps", "replaced": ""},
    )
    monkeypatch.setattr("app.v2.api.SessionLocal", _FakeSessionFactory())

    response = _client_with_session(["author"]).post("/api/v2/chapters/ch-1/session-archive")

    assert response.status_code == 409
    assert response.json()["error"] == "asr_gaps"


class _FakeSession:
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def get(self, model, key): return object()
    def add(self, row): pass
    def commit(self): pass


class _FakeSessionFactory:
    def __call__(self): return _FakeSession()

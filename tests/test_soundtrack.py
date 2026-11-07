"""Черновик, история, утверждение и права проверяются без внешней сети."""
import copy
import importlib.util
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models
import app.v2.models
from app.auth import session_serializer
from app.db import Base
from app.main import app
from app.models import (AmbientTrack, AudioFile, SoundMarker, SoundtrackEntry,
                        SoundtrackSnapshot, SoundtrackState, SoundtrackWork)
from app.services.openverse_client import normalize_audio
from app.services.soundtrack_store import SoundtrackError, chapter_payload, credits_text, mutate
from tests.consilium_book import build_book
from tests.test_openverse_client import IDENTITY, audio


@pytest.fixture()
def factory(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr("app.v2.soundtrack_api.SessionLocal", factory)
    monkeypatch.setattr("app.v2.soundtrack_api.openverse.detail", lambda identity: normalize_audio(audio(id=identity)))
    monkeypatch.setattr("app.v2.soundtrack_api.openverse.search", lambda q, page, layer: {"results": [normalize_audio(audio())], "page": page, "page_count": 1})
    with factory() as db:
        build_book(db)
        db.add_all([SoundMarker(id="scene1", book_id="b1", chapter_id="c1", kind="scene", payload={"title": "Лес"}),
                    SoundMarker(id="scene2", book_id="b1", chapter_id="c1", kind="scene", payload={"title": "Лес"}),
                    SoundMarker(id="other", book_id="b1", chapter_id="c2", kind="scene")])
        db.commit()
    yield factory
    engine.dispose()


def client(roles=("author",)):
    value = TestClient(app)
    if roles is not None:
        value.cookies.set("session", session_serializer.dumps({"uid": "u1", "sub": "u1", "roles": list(roles)}))
    return value


def change(factory, action, version=None, **body):
    with factory() as db:
        if version is None:
            state = db.get(SoundtrackState, "c1")
            version = state.version if state else 0
        result = mutate(db, "c1", {"action": action, "version": version, "request_id": uuid.uuid4().hex, **body},
                        openverse_detail=lambda identity: normalize_audio(audio(id=identity)))
        db.commit()
        return result


def manual(factory, **kwargs):
    return change(factory, "select", source="collection", title="Мелодия", **kwargs)


def test_roundtrip_draft_finalize_unlock_history_and_export(factory):
    first = manual(factory, scene_id="scene1", used=True)
    assert first["version"] == 1 and not first["locked"]
    entry_id = first["entries"][0]["id"]
    final = change(factory, "finalize")
    assert final["locked"] and len(final["snapshots"]) == 1
    old = copy.deepcopy(final["snapshots"][0])
    with factory() as db:
        assert "Мелодия" in credits_text(db, "c1")
        assert "not listed" in credits_text(db, "c1")
    with pytest.raises(SoundtrackError, match="version_conflict"):
        change(factory, "update", entry_id=entry_id, title="Новая")
    change(factory, "unlock")
    with factory() as db:
        with pytest.raises(SoundtrackError, match="not_finalized"):
            credits_text(db, "c1")
    change(factory, "update", entry_id=entry_id, title="Новая")
    final = change(factory, "finalize")
    assert len(final["snapshots"]) == 2
    assert final["snapshots"][1] == old
    with factory() as db:
        text = credits_text(db, "c1")
        assert "Новая" in text and "Мелодия" not in text


def test_only_used_entries_are_final_and_empty_requires_confirmation(factory):
    manual(factory)
    with pytest.raises(SoundtrackError, match="confirm_empty_required"):
        change(factory, "finalize")
    final = change(factory, "finalize", confirm_empty=True)
    assert final["snapshots"][0]["entries"] == []
    assert len(final["entries"]) == 1


def test_manual_same_titles_never_merge_but_external_work_deduplicates_uses(factory):
    manual(factory, scene_id="scene1")
    manual(factory, scene_id="scene1")
    for scene in ("scene1", "scene2", "scene2"):
        result = change(factory, "select", source="openverse", openverse_id=IDENTITY, scene_id=scene, used=True)
    assert len(result["entries"]) == 4
    external = [e for e in result["entries"] if e["source"] == "openverse"]
    assert len(external) == 2 and external[0]["work_id"] == external[1]["work_id"]
    change(factory, "finalize")
    with factory() as db:
        assert credits_text(db, "c1").count("Title: Тихий лес") == 1
        assert db.query(SoundtrackWork).count() == 3


def test_original_metadata_is_immutable_and_manual_license_correction_is_unknown(factory):
    result = change(factory, "select", source="openverse", openverse_id=IDENTITY)
    entry = result["entries"][0]
    result = change(factory, "update", entry_id=entry["id"], title="Правка", license="all rights reserved")
    changed = result["entries"][0]
    assert changed["original"]["title"] == "Тихий лес"
    assert changed["title"] == "Правка"
    assert changed["rights_status"] == "unknown"


def test_missing_or_lost_scene_survives_and_can_be_rebound_without_changing_snapshot(factory):
    result = manual(factory, scene_id="scene1", used=True)
    change(factory, "finalize")
    with factory() as db:
        db.delete(db.get(SoundMarker, "scene1"))
        db.commit()
        payload = chapter_payload(db, "c1")
    assert payload["entries"][0]["scene_missing"]
    assert not payload["snapshots"][0]["entries"][0]["scene_missing"]
    change(factory, "unlock")
    changed = change(factory, "update", entry_id=result["entries"][0]["id"], scene_id="scene2")
    assert changed["entries"][0]["scene_id"] == "scene2"
    assert not changed["entries"][0]["scene_missing"]


def test_scene_and_entry_must_belong_to_chapter_and_failures_rollback_version(factory):
    with pytest.raises(SoundtrackError, match="not_found"):
        manual(factory, scene_id="other")
    added = manual(factory)
    assert added["version"] == 1
    with factory() as db:
        with pytest.raises(SoundtrackError, match="not_found"):
            mutate(db, "c2", {"version": 0, "request_id": "cross", "action": "remove", "entry_id": added["entries"][0]["id"]})


def test_request_replay_and_version_conflict_do_not_duplicate_or_lose_entries(factory):
    body = {"version": 0, "request_id": "retry", "action": "select", "source": "collection", "title": "Дождь"}
    for _ in range(2):
        with factory() as db:
            response = mutate(db, "c1", body)
            db.commit()
            assert response["version"] == 1
    assert len(response["entries"]) == 1
    with factory() as db:
        with pytest.raises(SoundtrackError, match="request_conflict"):
            mutate(db, "c1", {**body, "title": "Ветер"})
    with pytest.raises(SoundtrackError, match="version_conflict"):
        manual(factory, version=0)
    with factory() as db:
        assert db.query(SoundtrackEntry).count() == 1
        assert db.get(SoundtrackState, "c1").version == 1


def test_remove_retains_history_and_reselect_restores_external_entry(factory):
    added = change(factory, "select", source="openverse", openverse_id=IDENTITY, used=True)
    entry_id = added["entries"][0]["id"]
    result = change(factory, "remove", entry_id=entry_id)
    assert result["entries"] == []
    with factory() as db:
        assert db.get(SoundtrackEntry, entry_id).removed
    result = change(factory, "select", source="openverse", openverse_id=IDENTITY)
    assert result["entries"][0]["id"] == entry_id


def test_rejections_are_scoped_to_scene_layer_and_can_be_restored(factory):
    for scene, layer in (("scene1", "music"), ("scene1", "fx"), ("scene2", "music")):
        response = change(factory, "reject", scene_id=scene, layer=layer, openverse_id=IDENTITY)
    assert len(response["rejections"]) == 3
    response = change(factory, "restore", scene_id="scene1", layer="music", openverse_id=IDENTITY)
    assert len(response["rejections"]) == 2


def test_legacy_replaced_track_can_be_selected_but_wrong_chapter_is_rejected(factory):
    with factory() as db:
        audio = AudioFile(id="audio", book_code="К", original_filename="old.mp3", stored_key="old.mp3",
                          mime_type="audio/mpeg", size_bytes=1, chapter="1", role="ambient")
        db.add(audio)
        db.add(AmbientTrack(id="track", book_id="b1", chapter_id="c1", marker_id="scene1",
                            audio_file_id="audio", status="replaced", duration_seconds=100))
        db.add(AmbientTrack(id="track2", book_id="b1", chapter_id="c2", marker_id="other",
                            audio_file_id="audio", status="done"))
        db.commit()
    response = change(factory, "select", source="elevenlabs", ambient_track_id="track", used=True)
    entry = response["entries"][0]
    assert entry["title"] == "old.mp3" and entry["ambient_track_id"] == "track"
    assert response["legacy_tracks"][0]["status"] == "replaced"
    assert entry["rights_status"] == "unknown"
    with pytest.raises(SoundtrackError, match="not_found"):
        change(factory, "select", source="elevenlabs", ambient_track_id="track2")


@pytest.mark.parametrize("roles,status", [(None,401), (("dictor",),403), (("agent",),403)])
def test_all_routes_require_editor(factory, roles, status):
    c = client(roles)
    for suffix in ("", "/search?q=forest", "/credits"):
        assert c.get("/api/v2/chapters/c1/soundtrack" + suffix).status_code == status
    assert c.post("/api/v2/chapters/c1/soundtrack", json={}).status_code == status


def test_api_roundtrip_utf8_export_and_missing_chapter_search(factory):
    c = client()
    base = "/api/v2/chapters/c1/soundtrack"
    assert c.get(base).json()["version"] == 0
    assert c.get(base + "/credits").status_code == 409
    added = c.post(base, json={"version": 0, "request_id": "1", "action": "select", "source": "collection", "title": "Ёлки", "used": True})
    assert added.status_code == 200
    assert c.post(base, json={"version": 1, "request_id": "2", "action": "finalize"}).status_code == 200
    exported = c.get(base + "/credits")
    assert exported.status_code == 200 and "Ёлки" in exported.content.decode("utf-8")
    assert "attachment" in exported.headers["Content-Disposition"]
    assert c.get("/api/v2/chapters/missing/soundtrack/search?q=forest").status_code == 404


@pytest.mark.parametrize("extra", [{"action": []}, {"source": []}, {"layer": {}},
    {"used": "false"}, {"title": ""}, {"source_url": "file:///etc/passwd"}, {"scene_id": {}},
    {"source": "elevenlabs", "ambient_track_id": {"id": "evil"}}])
def test_malformed_mutations_do_not_write_or_raise_500(factory, extra):
    response = client().post("/api/v2/chapters/c1/soundtrack", json={"version": 0, "request_id": "bad",
        "action": "select", "source": "collection", "title": "Трек", **extra})
    assert response.status_code in {400,404}
    with factory() as db:
        assert chapter_payload(db, "c1")["version"] == 0
        assert db.query(SoundtrackEntry).count() == 0


def test_two_concurrent_editors_cannot_both_write_same_version(tmp_path):
    engine = create_engine("sqlite:///" + str(tmp_path / "race.db"), connect_args={"timeout": 10})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as db:
        build_book(db)
    def run(index):
        try:
            with factory() as db:
                result = mutate(db, "c1", {"version": 0, "request_id": str(index), "action": "select",
                    "source": "collection", "title": "Трек"})
                db.commit()
                return result["version"]
        except SoundtrackError as exc:
            return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, [1,2]))
    assert sorted(str(result) for result in results) == ["1", "version_conflict"]
    with factory() as db:
        assert db.query(SoundtrackEntry).count() == 1
    engine.dispose()


def test_migration_is_idempotent_and_preserves_existing_book(tmp_path):
    path = "alembic/versions/0066_soundtrack.py"
    spec = importlib.util.spec_from_file_location("soundtrack_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine("sqlite://")
    Base.metadata.tables["script_books"].create(engine)
    with engine.begin() as conn:
        conn.execute(Base.metadata.tables["script_books"].insert().values(id="keep", title="Книга", source_filename="k.txt", source_format="txt"))
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
            migration.upgrade()
            assert set(migration.TABLES) <= set(inspect(conn).get_table_names())
            conn.execute(Base.metadata.tables["soundtrack_states"].insert().values(chapter_id="c", book_id="keep", version=2, locked=False))
            migration.upgrade()
            assert conn.scalar(select(SoundtrackState.version)) == 2
            migration.downgrade()
            migration.downgrade()
        assert conn.scalar(select(Base.metadata.tables["script_books"].c.title)) == "Книга"


def test_openverse_network_wait_does_not_hold_sqlite_write_lock(tmp_path):
    engine = create_engine("sqlite:///" + str(tmp_path / "network.db"), connect_args={"timeout": 0.1})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as db:
        build_book(db)
    def detail(identity):
        # Отдельный запрос сайта должен свободно записывать в БД, пока каталог ждёт.
        with factory() as other:
            other.add(SoundtrackState(chapter_id="c2", book_id="b1", version=1, locked=False))
            other.commit()
        return normalize_audio(audio(id=identity))
    with factory() as db:
        response = mutate(db, "c1", {"action": "select", "source": "openverse", "version": 0,
            "request_id": "network", "openverse_id": IDENTITY}, openverse_detail=detail)
        db.commit()
    assert response["version"] == 1
    with factory() as db:
        assert db.get(SoundtrackState, "c2").version == 1
    engine.dispose()


def test_real_scene_fields_use_place_and_order_and_export_distinct_same_named_scenes(factory):
    from app.models import SoundPlace
    with factory() as db:
        db.add(SoundPlace(id="place", book_id="b1", name="Тихий лес"))
        for index, scene_id in enumerate(("scene1", "scene2")):
            scene = db.get(SoundMarker, scene_id)
            scene.place_id = "place"
            scene.segment_id = f"c1:{index:05d}"
            scene.payload = {"weather": "дождь", "time_of_day": "вечер", "ambience": "ветер",
                             "mood": "тревога", "music_queries": ["forest drone"]}
        db.commit()
    for scene_id in ("scene1", "scene2"):
        result = change(factory, "select", source="openverse", openverse_id=IDENTITY, scene_id=scene_id, used=True)
    assert {entry["scene_title"] for entry in result["entries"]} == {"Scene 1 · Тихий лес", "Scene 2 · Тихий лес"}
    change(factory, "finalize")
    with factory() as db:
        text = credits_text(db, "c1")
        assert text.count("Title: Тихий лес") == 1
        assert "Scene 1 · Тихий лес [scene1]" in text
        assert "Scene 2 · Тихий лес [scene2]" in text


def test_collection_title_cannot_be_bypassed_with_ambient_track_id(factory):
    response = client().post("/api/v2/chapters/c1/soundtrack", json={"action": "select", "version": 0,
        "request_id": "empty-title", "source": "collection", "ambient_track_id": "arbitrary"})
    assert response.status_code == 400 and response.json()["error"] == "bad_title"


def test_upstream_429_does_not_change_version_or_draft_and_api_preserves_retry_after(factory, monkeypatch):
    from app.services.openverse_client import OpenverseError
    def fail(*args, **kwargs):
        raise OpenverseError("openverse_rate_limited", 429, "17")
    manual(factory)
    monkeypatch.setattr("app.v2.soundtrack_api.openverse.detail", fail)
    monkeypatch.setattr("app.v2.soundtrack_api.openverse.search", fail)
    base = "/api/v2/chapters/c1/soundtrack"
    response = client().post(base, json={"action": "select", "version": 1, "request_id": "429",
        "source": "openverse", "openverse_id": IDENTITY})
    assert response.status_code == 429 and int(response.headers["Retry-After"]) >= 16
    assert response.json()["error"] == "openverse_rate_limited"
    search = client().get(base + "/search?q=forest")
    assert search.status_code == 429
    current = client().get(base).json()
    assert current["version"] == 1 and len(current["entries"]) == 1


def test_finalization_replay_is_idempotent_and_new_request_with_old_version_conflicts(factory):
    manual(factory, used=True)
    body = {"action": "finalize", "version": 1, "request_id": "final"}
    for _ in range(2):
        with factory() as db:
            result = mutate(db, "c1", body)
            db.commit()
            assert result["version"] == 2 and result["locked"]
    with factory() as db:
        assert db.query(SoundtrackSnapshot).count() == 1
        with pytest.raises(SoundtrackError, match="version_conflict"):
            mutate(db, "c1", {**body, "request_id": "different"})


@pytest.mark.parametrize("language", ["en", "ru"])
def test_manual_selection_preserves_book_language_and_typed_character_identity(factory, language):
    from app.models import Character, ScriptBook
    with factory() as db:
        book = db.get(ScriptBook, "b1")
        book.language, book.role_identity_mode = language, "typed_v1"
        character = db.query(Character).filter_by(book_id="b1").first()
        character.name = "Author"
        character_id = character.id
        db.commit()
    title = "Forest atmosphere" if language == "en" else "Лесная атмосфера"
    change(factory, "select", source="collection", title=title, scene_id="scene1", used=True)
    final = change(factory, "finalize")
    assert final["snapshots"][0]["entries"][0]["title"] == title
    with factory() as db:
        book = db.get(ScriptBook, "b1")
        assert (book.language, book.role_identity_mode) == (language, "typed_v1")
        assert db.get(Character, character_id).name == "Author"
        assert title in credits_text(db, "c1")


@pytest.mark.parametrize("language", ["en", "ru"])
def test_existing_generated_tracks_remain_scoped_to_current_book_language(factory, language):
    from app.models import ScriptBook
    with factory() as db:
        db.get(ScriptBook, "b1").language = language
        db.add(AudioFile(id="language-audio", book_code="K", original_filename="ambient.mp3", stored_key="ambient.mp3",
            mime_type="audio/mpeg", size_bytes=1, chapter="1", role="ambient"))
        for track_language in ("en", "ru"):
            db.add(AmbientTrack(id="language-" + track_language, book_id="b1", chapter_id="c1", marker_id="scene1",
                audio_file_id="language-audio", status="done", language_snapshot=track_language))
        db.commit()
        assert [item["ambient_track_id"] for item in chapter_payload(db, "c1")["legacy_tracks"]] == ["language-" + language]
    other = "ru" if language == "en" else "en"
    with pytest.raises(SoundtrackError, match="not_found"):
        change(factory, "select", source="elevenlabs", ambient_track_id="language-" + other)
    result = change(factory, "select", source="elevenlabs", ambient_track_id="language-" + language)
    assert result["entries"][0]["ambient_track_id"] == "language-" + language

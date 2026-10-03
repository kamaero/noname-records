"""Звуковая разметка: таблицы, применение прогона, правки человека. База в памяти."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
import app.v2.models  # noqa: F401
from app.db import Base
from tests.consilium_book import BOOK, build_book


def memory_session():
    """Своя база в памяти с книгой — чтобы прогнать один и тот же случай дважды."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    build_book(session)
    return session


@pytest.fixture()
def db():
    with memory_session() as session:
        yield session


def test_tables_hold_a_place_and_a_marker(db):
    from app.models import SoundMarker, SoundPlace

    place = SoundPlace(book_id=BOOK, name="Таверна у Ворот", description="шумный зал",
                       ambience_queries=["fantasy tavern crowd"])
    db.add(place)
    db.flush()
    db.add(SoundMarker(book_id=BOOK, chapter_id="c1", segment_id="c1:00000", kind="scene",
                       place_id=place.id, payload={"mood": "тревога"}, text_sha256="x"))
    db.commit()
    marker = db.query(SoundMarker).one()
    assert (marker.status, marker.source, marker.payload["mood"]) == ("active", "llm", "тревога")


from app.pipeline.sound_markers import Checked

TEXTS = {0: "— Идём, — сказал Гамук.", 1: "— Куда?", 2: "Они ушли."}


def checked(place_id="", sound_quote="Они ушли"):
    return Checked(
        scenes=[{"ordinal": 0, "place": {"id": place_id, "name": "Лес", "description": "чаща",
                                         "ambience_queries": ["forest"]},
                 "time_of_day": "день", "weather": "", "ambience": "птицы", "mood": "покой",
                 "music_queries": ["calm"]}],
        transitions=[{"ordinal": 1, "what": "флешбэк"}],
        sounds=[{"ordinal": 2, "quote": sound_quote, "description": "шаги прочь", "queries": ["steps"]}],
    )


def apply(db, check=None, run="r1"):
    from app.services.sound_store import apply_chapter
    out = apply_chapter(db, book_id=BOOK, chapter_id="c1", text_sha256="h1", checked=check or checked(),
                        run_id=run, texts=TEXTS)
    db.commit()
    return out


def test_apply_creates_place_and_three_markers(db):
    from app.models import SoundMarker, SoundPlace
    assert apply(db) == {"added": 3, "places_new": 1}
    assert db.query(SoundPlace).one().name == "Лес"
    scene = db.query(SoundMarker).filter_by(kind="scene").one()
    assert scene.place_id and scene.payload["anchor_text"] == TEXTS[0][:80]


def test_same_name_reuses_place(db):
    from app.models import SoundPlace
    apply(db)
    apply(db, run="r2")
    assert db.query(SoundPlace).count() == 1


def test_rerun_replaces_llm_keeps_human_and_does_not_resurrect_dismissed(db):
    from app.models import SoundMarker
    from app.services.sound_store import dismiss_marker, edit_marker
    apply(db)
    sound = db.query(SoundMarker).filter_by(kind="sound").one()
    transition = db.query(SoundMarker).filter_by(kind="transition").one()
    assert dismiss_marker(db, sound.id)
    edit_marker(db, transition.id, {"what": "сон"})
    db.commit()
    apply(db, run="r2")
    rows = db.query(SoundMarker).all()
    assert [r.status for r in rows if r.kind == "sound"] == ["dismissed"]          # не воскрес
    assert [(r.source, r.payload["what"]) for r in rows if r.kind == "transition"] == [("human", "сон")]
    assert len([r for r in rows if r.kind == "scene"]) == 1                          # llm заменён, не удвоен


def test_changed_text_reanchors_by_quote_or_marks_lost(db):
    from app.models import SoundMarker
    from app.services.sound_store import reanchor_chapter
    from app.v2.models import V2Segment
    apply(db)
    # в начало главы вставлен абзац: всё сдвинулось на один
    for seg in sorted(db.query(V2Segment).filter_by(chapter_id="c1").all(), key=lambda s: -s.ordinal):
        db.delete(seg)
    db.flush()
    for ordinal, text in enumerate(["Эпиграф."] + [TEXTS[i] for i in range(3)]):
        db.add(V2Segment(id=f"c1:{ordinal:05d}", book_id=BOOK, chapter_id="c1", ordinal=ordinal,
                         kind="paragraph", text=text))
    db.commit()
    assert reanchor_chapter(db, "c1") == 3
    db.commit()
    by_kind = {r.kind: r for r in db.query(SoundMarker).all()}
    assert by_kind["sound"].segment_id == "c1:00003" and by_kind["sound"].status == "active"
    assert by_kind["scene"].segment_id == "c1:00001"


def test_edit_marker_rejects_segment_from_other_chapter(db):
    from app.models import SoundMarker
    from app.services.sound_store import edit_marker
    apply(db)
    scene = db.query(SoundMarker).filter_by(kind="scene").one()
    assert edit_marker(db, scene.id, {"segment_id": "c2:00000"}) == "bad_segment"
    moved = edit_marker(db, scene.id, {"segment_id": "c1:00001"})
    assert moved.segment_id == "c1:00001" and moved.source == "human"


def test_pairs_apart_is_remembered(db):
    from app.models import SoundPlace
    from app.services.sound_store import decide_pair, record_pairs
    a, b = SoundPlace(book_id=BOOK, name="Таверна"), SoundPlace(book_id=BOOK, name="Кабак")
    db.add_all([a, b]); db.flush()
    assert record_pairs(db, BOOK, [{"a": a.id, "b": b.id, "reason": "одно место"}]) == 1
    from app.models import SoundPlacePair
    pair = db.query(SoundPlacePair).one()
    assert decide_pair(db, pair.id, merge=False) == "apart"
    assert record_pairs(db, BOOK, [{"a": b.id, "b": a.id, "reason": "снова"}]) == 0


# --- Fix round 1: F1-F4, M1-M2 (regression tests per finding) ------------------


def test_dismissed_sound_reanchors_and_is_not_resurrected_after_text_change(db):
    """F1: смена текста не должна воскрешать отклонённое — отказ должен переехать вместе с абзацем."""
    from app.models import SoundMarker
    from app.pipeline.sound_markers import Checked
    from app.services.sound_store import apply_chapter, dismiss_marker
    from app.v2.models import V2Segment
    apply(db)
    sound = db.query(SoundMarker).filter_by(kind="sound").one()
    assert dismiss_marker(db, sound.id)
    db.commit()
    for seg in sorted(db.query(V2Segment).filter_by(chapter_id="c1").all(), key=lambda s: -s.ordinal):
        db.delete(seg)
    db.flush()
    shifted = {0: "Эпиграф.", **{i + 1: TEXTS[i] for i in range(3)}}
    for ordinal, text in shifted.items():
        db.add(V2Segment(id=f"c1:{ordinal:05d}", book_id=BOOK, chapter_id="c1", ordinal=ordinal,
                         kind="paragraph", text=text))
    db.commit()
    shifted_checked = Checked(
        scenes=[{"ordinal": 1, "place": {"id": "", "name": "Лес", "description": "чаща",
                                         "ambience_queries": ["forest"]},
                 "time_of_day": "день", "weather": "", "ambience": "птицы", "mood": "покой",
                 "music_queries": ["calm"]}],
        transitions=[{"ordinal": 2, "what": "флешбэк"}],
        sounds=[{"ordinal": 3, "quote": "Они ушли", "description": "шаги прочь", "queries": ["steps"]}],
    )
    out = apply_chapter(db, book_id=BOOK, chapter_id="c1", text_sha256="h2", checked=shifted_checked,
                        run_id="r2", texts=shifted)
    db.commit()
    # Звук не добавлен повторно (отказ переехал вместе с абзацем), сцена узнана на новом
    # абзаце и добавленной не считается — добавлен только переход.
    assert out == {"added": 1, "places_new": 0}
    rows = db.query(SoundMarker).filter_by(kind="sound").all()
    assert [(r.status, r.segment_id) for r in rows] == [("dismissed", "c1:00003")]  # переехал, не воскрес


def test_human_move_blocks_llm_reinsertion_at_old_position(db):
    """F2: перенос человеком не должен отменяться следующим прогоном на старом месте."""
    from app.models import SoundMarker
    from app.services.sound_store import edit_marker
    apply(db)
    transition = db.query(SoundMarker).filter_by(kind="transition").one()
    moved = edit_marker(db, transition.id, {"segment_id": "c1:00002"})
    assert moved.segment_id == "c1:00002" and moved.source == "human"
    db.commit()
    apply(db, run="r2")
    rows = db.query(SoundMarker).filter_by(kind="transition").all()
    assert [(r.segment_id, r.source) for r in rows] == [("c1:00002", "human")]  # только один, на новом месте


def test_edit_marker_moving_sound_checks_quote_in_target_paragraph(db):
    """F3: перенос звука проверяет цитату в целевом абзаце, иначе он потом снова уедет."""
    from app.models import SoundMarker
    from app.services.sound_store import edit_marker
    apply(db)
    sound = db.query(SoundMarker).filter_by(kind="sound").one()
    assert edit_marker(db, sound.id, {"segment_id": "c1:00000"}) == "bad_quote"
    unchanged = db.query(SoundMarker).filter_by(kind="sound").one()
    assert unchanged.segment_id == "c1:00002" and unchanged.source == "llm"  # без изменений
    moved = edit_marker(db, sound.id, {"segment_id": "c1:00000", "quote": "Идём"})
    assert moved.segment_id == "c1:00000" and moved.quote == "Идём" and moved.source == "human"


def test_decide_pair_rewrites_stale_candidates_and_rejects_merged_away_place(db):
    """F4: склейка A-B переносит кандидата B-C на A-C; решить пару со склеенным местом нельзя."""
    from app.models import SoundPlace, SoundPlacePair
    from app.services.sound_store import decide_pair, record_pairs
    a, b, c = (SoundPlace(book_id=BOOK, name="A"), SoundPlace(book_id=BOOK, name="B"),
              SoundPlace(book_id=BOOK, name="C"))
    db.add_all([a, b, c]); db.flush()
    record_pairs(db, BOOK, [{"a": a.id, "b": b.id, "reason": "1"}, {"a": b.id, "b": c.id, "reason": "2"}])
    db.commit()
    ab = db.query(SoundPlacePair).filter_by(place_a=a.id, place_b=b.id).one()
    bc = db.query(SoundPlacePair).filter_by(place_a=b.id, place_b=c.id).one()
    assert decide_pair(db, ab.id, merge=True) == "merged"
    db.commit()
    db.refresh(bc)
    assert {bc.place_a, bc.place_b} == {a.id, c.id} and bc.status == "candidate"  # переехала, не потерялась
    d, e = SoundPlace(book_id=BOOK, name="D"), SoundPlace(book_id=BOOK, name="E")
    db.add_all([d, e]); db.flush()
    stale = SoundPlacePair(book_id=BOOK, place_a=d.id, place_b=e.id, status="candidate", reason="stale")
    db.add(stale); db.flush()
    d.merged_into = a.id  # место склеено в обход этой пары
    db.commit()
    assert decide_pair(db, stale.id, merge=False) == "not_found"


def test_decide_pair_merge_marks_rewritten_duplicate_obsolete(db):
    """F4: если перенесённая пара совпала с уже существующей — она гасится, а не дублируется."""
    from app.models import SoundPlace, SoundPlacePair
    from app.services.sound_store import decide_pair, record_pairs
    x, y, z = (SoundPlace(book_id=BOOK, name="X"), SoundPlace(book_id=BOOK, name="Y"),
              SoundPlace(book_id=BOOK, name="Z"))
    db.add_all([x, y, z]); db.flush()
    record_pairs(db, BOOK, [{"a": x.id, "b": z.id, "reason": "existing"}])
    record_pairs(db, BOOK, [{"a": x.id, "b": y.id, "reason": "1"}, {"a": y.id, "b": z.id, "reason": "2"}])
    db.commit()
    xy = db.query(SoundPlacePair).filter_by(place_a=x.id, place_b=y.id).one()
    yz = db.query(SoundPlacePair).filter_by(place_a=y.id, place_b=z.id).one()
    assert decide_pair(db, xy.id, merge=True) == "merged"
    db.commit()
    db.refresh(yz)
    assert {yz.place_a, yz.place_b} == {x.id, z.id} and yz.status == "obsolete"


def test_edit_marker_rejects_malformed_segment_id(db):
    """M1: битый segment_id — код ошибки, а не ValueError/500."""
    from app.models import SoundMarker
    from app.services.sound_store import edit_marker
    apply(db)
    scene = db.query(SoundMarker).filter_by(kind="scene").one()
    assert edit_marker(db, scene.id, {"segment_id": "c1:abc"}) == "bad_segment"


def test_add_marker_rejects_malformed_segment_id(db):
    """M1: то же самое для add_marker."""
    from app.services.sound_store import add_marker
    apply(db)
    assert add_marker(db, chapter_id="c1", segment_id="c1:xyz", kind="transition", fields={}) == "bad_segment"


def test_queries_field_as_plain_string_becomes_one_item_list(db):
    """M2: строка в поле *queries — один запрос, а не список символов."""
    from app.models import SoundMarker, SoundPlace
    from app.services.sound_store import edit_marker, edit_place
    apply(db)
    sound = db.query(SoundMarker).filter_by(kind="sound").one()
    edited = edit_marker(db, sound.id, {"queries": "wind"})
    assert edited.payload["queries"] == ["wind"]
    place = db.query(SoundPlace).one()
    edited_place = edit_place(db, place.id, {"ambience_queries": "forest"})
    assert edited_place.ambience_queries == ["forest"]


# --- Final review fixes: I4, S1, S2 ---------------------------------------------


def test_place_for_follows_merge_when_model_repeats_old_name(db):
    """I4: место А поглотило Б; глава, где модель снова называет место именем Б (не зная о
    склейке), не заводит новую карточку — маркер уходит на А, живое место остаётся одно."""
    from app.models import SoundMarker, SoundPlace, SoundPlacePair
    from app.pipeline.sound_markers import Checked
    from app.services.sound_store import apply_chapter, decide_pair, record_pairs
    a, b = SoundPlace(book_id=BOOK, name="Таверна"), SoundPlace(book_id=BOOK, name="Кабак")
    db.add_all([a, b]); db.flush()
    record_pairs(db, BOOK, [{"a": a.id, "b": b.id, "reason": "одно место"}])
    db.commit()
    pair = db.query(SoundPlacePair).one()
    assert decide_pair(db, pair.id, merge=True) == "merged"
    db.commit()

    scene_named_b = Checked(scenes=[{"ordinal": 0, "place": {"id": "", "name": "Кабак",
                                                              "description": "шумно",
                                                              "ambience_queries": ["crowd"]},
                                     "time_of_day": "вечер", "weather": "", "ambience": "гам",
                                     "mood": "весело", "music_queries": ["tavern"]}])
    out = apply_chapter(db, book_id=BOOK, chapter_id="c1", text_sha256="h1", checked=scene_named_b,
                        run_id="r1", texts=TEXTS)
    db.commit()
    assert out["places_new"] == 0
    assert db.query(SoundPlace).filter_by(book_id=BOOK, merged_into=None).count() == 1
    marker = db.query(SoundMarker).filter_by(kind="scene").one()
    assert marker.place_id == a.id


def test_lost_marker_keeps_sha_so_second_reanchor_does_not_touch_it(db):
    """S1: маркер, ушедший в lost, сразу получает актуальный text_sha256 — второй вызов
    (например, второй GET главы подряд) его больше не трогает, пока текст не сменится снова."""
    from app.models import SoundMarker
    from app.services.sound_store import chapter_markers
    from app.v2.models import V2Segment
    apply(db)
    seg1 = db.query(V2Segment).filter_by(chapter_id="c1", ordinal=1).one()
    seg1.text = "Новый текст, где старого абзаца больше нет."
    db.commit()

    chapter_markers(db, "c1")
    db.commit()
    lost = db.query(SoundMarker).filter_by(kind="transition").one()
    assert lost.status == "lost"
    updated_first = lost.updated_at

    chapter_markers(db, "c1")
    db.commit()
    lost_again = db.query(SoundMarker).filter_by(kind="transition").one()
    assert lost_again.status == "lost" and lost_again.updated_at == updated_first


def test_sound_marker_without_quote_reanchors_by_anchor_text(db):
    """S2: звук без цитаты (добавлен вручную, до неё) ищет своё место по anchor_text, как
    сцена/переход, а не сразу уходит в потерянные из-за пустой цитаты."""
    from app.models import SoundMarker
    from app.services.sound_store import add_marker, reanchor_chapter
    from app.v2.models import V2Segment
    apply(db)
    row = add_marker(db, chapter_id="c1", segment_id="c1:00000", kind="sound", fields={})
    db.commit()
    assert row.quote == ""

    for seg in sorted(db.query(V2Segment).filter_by(chapter_id="c1").all(), key=lambda s: -s.ordinal):
        db.delete(seg)
    db.flush()
    for ordinal, text in enumerate(["Эпиграф."] + [TEXTS[i] for i in range(3)]):
        db.add(V2Segment(id=f"c1:{ordinal:05d}", book_id=BOOK, chapter_id="c1", ordinal=ordinal,
                         kind="paragraph", text=text))
    db.commit()

    assert reanchor_chapter(db, "c1") >= 1
    db.commit()
    moved = db.get(SoundMarker, row.id)
    assert moved.status == "active" and moved.segment_id == "c1:00001"


def test_active_chapter_markers_orders_same_paragraph_ties_deterministically(db):
    """Два звука на одном абзаце и виде — порядок держится на (created_at, id), а не на
    том, в каком порядке их вернула СУБД (без ORDER BY по этим полям не гарантирован)."""
    from datetime import timedelta

    from app.models import SoundMarker
    from app.services.sound_store import active_chapter_markers
    from app.time_utils import utcnow_naive

    now = utcnow_naive()
    later = SoundMarker(book_id=BOOK, chapter_id="c1", segment_id="c1:00002", kind="sound",
                        quote="ушли", payload={}, text_sha256="x", created_at=now)
    earlier = SoundMarker(book_id=BOOK, chapter_id="c1", segment_id="c1:00002", kind="sound",
                          quote="ушли", payload={}, text_sha256="x", created_at=now - timedelta(seconds=5))
    # Вставлены в порядке «позже, раньше» — без сортировки по created_at вывод держался
    # бы на порядке вставки/выдачи СУБД, а не на времени создания.
    db.add(later)
    db.add(earlier)
    db.commit()

    rows, _places = active_chapter_markers(db, "c1")

    assert [r["id"] for r in rows] == [earlier.id, later.id]


# --- Переразметка не отвязывает оплаченный эмбиент -------------------------------


def wide_chapter(db, count=8):
    """Глава c1 из `count` абзацев — чтобы проверять сдвиг сцены дальше трёх."""
    from app.v2.models import V2Segment
    for seg in db.query(V2Segment).filter_by(chapter_id="c1").all():
        db.delete(seg)
    db.flush()
    for ordinal in range(count):
        db.add(V2Segment(id=f"c1:{ordinal:05d}", book_id=BOOK, chapter_id="c1", ordinal=ordinal,
                         kind="paragraph", text=f"Абзац номер {ordinal}."))
    db.commit()


def one_scene(ordinal, place_name="Лес", **fields):
    return dict({"ordinal": ordinal,
                 "place": {"id": "", "name": place_name, "description": "чаща",
                           "ambience_queries": ["forest"]},
                 "time_of_day": "день", "weather": "", "ambience": "птицы", "mood": "покой",
                 "music_queries": ["calm"]}, **fields)


def apply_scenes(db, scenes, run="r1"):
    """Прогон из одних сцен по нынешнему тексту главы: `text_sha256` настоящий, чтобы
    перепривязка не вмешивалась в проверку сопоставления сцен."""
    from app.services.sound_store import _chapter_texts, apply_chapter
    texts, sha = _chapter_texts(db, "c1")
    out = apply_chapter(db, book_id=BOOK, chapter_id="c1", text_sha256=sha, checked=Checked(scenes=scenes),
                        run_id=run, texts=texts)
    db.commit()
    return out


def scenes_of(db):
    from app.models import SoundMarker
    return (db.query(SoundMarker).filter_by(chapter_id="c1", kind="scene")
            .order_by(SoundMarker.segment_id.asc()).all())


def ambient_track(db, marker_id):
    """Готовый трек эмбиента сцены: строка `ambient_tracks` и живой файл под ней."""
    from app.models import AmbientTrack, AudioFile
    audio = AudioFile(book_code="B1", original_filename="amb.mp3", stored_key="c1/amb.mp3",
                      canonical_filename="Г1_Эмбиент - Лес.mp3", mime_type="audio/mpeg", size_bytes=10,
                      chapter="1", role="Эмбиент", kind="ambient")
    db.add(audio)
    db.flush()
    track = AmbientTrack(book_id=BOOK, chapter_id="c1", marker_id=marker_id, audio_file_id=audio.id,
                         prompt="calm strings", status="done")
    db.add(track)
    db.commit()
    return track


def ready_scene_ids(db):
    """Сцены главы с готовым треком — глазами движка эмбиента (`done_tracks`)."""
    from app.models import AmbientTrack
    from app.services.ambient_engine import done_tracks
    return {mid for (mid,) in
            done_tracks(db, AmbientTrack.marker_id).filter(AmbientTrack.chapter_id == "c1")}


def test_rerun_keeps_a_scene_on_the_same_paragraph_with_its_track(db):
    """Тот же абзац — та же сцена: id сохраняется, оплаченный трек остаётся привязан."""
    from app.services.sound_store import scene_ambient
    wide_chapter(db)
    apply_scenes(db, [one_scene(2)])
    old_id = scenes_of(db)[0].id
    track_id = ambient_track(db, old_id).id

    out = apply_scenes(db, [one_scene(2, mood="тревога")], run="r2")

    again = scenes_of(db)[0]
    assert out == {"added": 0, "places_new": 0}      # переиспользованная сцена не новая
    assert again.id == old_id
    assert (again.source, again.status, again.run_id) == ("llm", "active", "r2")
    assert again.payload["mood"] == "тревога" and again.payload["anchor_text"] == "Абзац номер 2."
    assert ready_scene_ids(db) == {old_id}
    assert scene_ambient(db, "c1")[old_id]["id"] == track_id


def test_a_scene_shifted_within_three_paragraphs_keeps_its_id(db):
    """Место то же, абзац уехал не дальше трёх — это та же сцена, строка правится на месте."""
    wide_chapter(db)
    apply_scenes(db, [one_scene(1)])
    old_id = scenes_of(db)[0].id
    ambient_track(db, old_id)

    out = apply_scenes(db, [one_scene(4)], run="r2")

    again = scenes_of(db)[0]
    assert out["added"] == 0
    assert (again.id, again.segment_id) == (old_id, "c1:00004")
    assert again.payload["anchor_text"] == "Абзац номер 4."
    assert ready_scene_ids(db) == {old_id}


def test_a_scene_shifted_by_four_paragraphs_is_a_new_scene(db):
    """Четыре абзаца — уже не та же сцена: старая строка удаляется, новая добавляется."""
    from app.models import SoundMarker
    wide_chapter(db)
    apply_scenes(db, [one_scene(1)])
    old_id = scenes_of(db)[0].id

    out = apply_scenes(db, [one_scene(5)], run="r2")

    fresh = scenes_of(db)[0]
    assert out["added"] == 1
    assert fresh.id != old_id and fresh.segment_id == "c1:00005"
    assert db.get(SoundMarker, old_id) is None


def test_same_paragraph_beats_a_changed_place(db):
    """Правило абзаца сильнее правила места: сцена на том же абзаце остаётся собой, даже
    если прогон назвал другое место — трек снимать не за что, меняется только место."""
    from app.models import SoundPlace
    wide_chapter(db)
    apply_scenes(db, [one_scene(3, "Лес")])
    old_id = scenes_of(db)[0].id
    ambient_track(db, old_id)

    out = apply_scenes(db, [one_scene(3, "Таверна")], run="r2")

    again = scenes_of(db)[0]
    assert out["added"] == 0
    assert again.id == old_id
    assert db.get(SoundPlace, again.place_id).name == "Таверна"
    assert ready_scene_ids(db) == {old_id}


def test_a_scene_that_disappeared_is_deleted_and_its_track_orphaned(db):
    """Сцены в новом прогоне нет — строка удаляется, как и раньше; файл трека не трогаем."""
    from app.models import AmbientTrack, AudioFile, SoundMarker
    wide_chapter(db)
    apply_scenes(db, [one_scene(0), one_scene(6, "Таверна")])
    gone_id = scenes_of(db)[1].id
    track_id = ambient_track(db, gone_id).id

    apply_scenes(db, [one_scene(0)], run="r2")

    assert [r.segment_id for r in scenes_of(db)] == ["c1:00000"]
    assert db.get(SoundMarker, gone_id) is None
    orphan = db.get(AmbientTrack, track_id)
    assert orphan is not None and db.get(AudioFile, orphan.audio_file_id) is not None


def test_two_new_scenes_do_not_share_one_old_scene(db):
    """Одна старая строка достаётся не больше чем одной новой сцене — ближайшей."""
    wide_chapter(db)
    apply_scenes(db, [one_scene(2)])
    old_id = scenes_of(db)[0].id

    out = apply_scenes(db, [one_scene(3), one_scene(4)], run="r2")

    rows = scenes_of(db)
    assert out["added"] == 1
    assert [r.segment_id for r in rows] == ["c1:00003", "c1:00004"]
    assert rows[0].id == old_id and rows[1].id != old_id


def test_a_dismissed_scene_is_not_reused_or_resurrected(db):
    """Отклонённая сцена не кандидат на переиспользование: она остаётся отклонённой на
    своём абзаце, а новая сцена рядом заводится отдельной строкой."""
    from app.services.sound_store import dismiss_marker
    wide_chapter(db)
    apply_scenes(db, [one_scene(2)])
    old_id = scenes_of(db)[0].id
    assert dismiss_marker(db, old_id)
    db.commit()

    out = apply_scenes(db, [one_scene(4)], run="r2")

    rows = {r.id: r for r in scenes_of(db)}
    assert out["added"] == 1
    assert (rows[old_id].status, rows[old_id].segment_id) == ("dismissed", "c1:00002")
    fresh = [r for r in rows.values() if r.id != old_id]
    assert [(r.status, r.segment_id) for r in fresh] == [("active", "c1:00004")]


def test_a_human_scene_still_blocks_the_run_at_its_paragraph(db):
    """Правленое человеком прогон по-прежнему не трогает и не переиспользует."""
    from app.services.sound_store import add_marker
    from app.models import SoundPlace
    wide_chapter(db)
    apply_scenes(db, [one_scene(0)])
    llm_id = scenes_of(db)[0].id
    place = db.query(SoundPlace).filter_by(book_id=BOOK).one()
    human_id = add_marker(db, chapter_id="c1", segment_id="c1:00005", kind="scene",
                          fields={"place_id": place.id, "mood": "своё"}).id
    db.commit()

    out = apply_scenes(db, [one_scene(0), one_scene(5)], run="r2")

    rows = scenes_of(db)
    assert out["added"] == 0                                   # сцена 0 переиспользована, 5 закрыта
    assert [(r.id, r.source) for r in rows] == [(llm_id, "llm"), (human_id, "human")]
    assert rows[1].payload["mood"] == "своё"


def test_the_nearest_scene_takes_the_old_row_not_the_first_in_the_run(db):
    """Остаток разбирается по всем парам сразу: старая сцена с ¶4 достаётся сцене на ¶5
    (расстояние 1), а не первой по порядку прогона сцене на ¶1 (расстояние 3)."""
    wide_chapter(db)
    apply_scenes(db, [one_scene(4)])
    old_id = scenes_of(db)[0].id
    ambient_track(db, old_id)

    out = apply_scenes(db, [one_scene(1), one_scene(5)], run="r2")

    rows = scenes_of(db)
    assert out["added"] == 1
    assert [r.segment_id for r in rows] == ["c1:00001", "c1:00005"]
    assert rows[1].id == old_id and rows[0].id != old_id
    assert ready_scene_ids(db) == {old_id}


def test_both_old_rows_are_kept_when_a_valid_pairing_exists(db):
    """Ближняя пара не съедает единственного кандидата соседки.

    Старые сцены на ¶3 и ¶6, новые — на ¶1 и ¶4, место одно. Ближайшая пара (¶4 ← ¶3,
    расстояние 1) оставила бы сцену на ¶1 ни с чем: до ¶6 ей пять абзацев. Раскладка на
    две пары есть (¶1 ← ¶3, ¶4 ← ¶6), и берётся именно она — оба оплаченных трека целы.
    """
    wide_chapter(db)
    apply_scenes(db, [one_scene(3), one_scene(6)])
    third_id, sixth_id = (r.id for r in scenes_of(db))
    ambient_track(db, third_id)
    ambient_track(db, sixth_id)

    out = apply_scenes(db, [one_scene(1), one_scene(4)], run="r2")

    rows = scenes_of(db)
    assert out["added"] == 0                                   # новых сцен нет — обе узнаны
    assert [(r.id, r.segment_id) for r in rows] == [(third_id, "c1:00001"), (sixth_id, "c1:00004")]
    assert ready_scene_ids(db) == {third_id, sixth_id}


def test_the_pairing_does_not_depend_on_the_order_rows_were_inserted(db):
    """Кандидаты симметричны (старые сцены на ¶2 и ¶4, новая на ¶3) — пара одна и та же
    в обеих базах: строку забирает сцена с меньшим абзацем, а не та, что вставлена раньше.
    """
    def survivor(session, order):
        wide_chapter(session)
        apply_scenes(session, [one_scene(ordinal) for ordinal in order])
        was = {r.id: r.segment_id for r in scenes_of(session)}
        apply_scenes(session, [one_scene(3)], run="r2")
        rows = scenes_of(session)
        assert [r.segment_id for r in rows] == ["c1:00003"]
        return was.get(rows[0].id)

    assert survivor(db, [2, 4]) == "c1:00002"
    with memory_session() as other:            # обратный порядок вставки, другие id
        assert survivor(other, [4, 2]) == "c1:00002"


def test_two_old_scenes_on_one_paragraph_give_up_the_one_of_the_same_place(db):
    """На абзаце две старые сцены — новая забирает ту, чьё место совпало, а не первую,
    какую вернула СУБД."""
    from app.models import SoundMarker, SoundPlace
    wide_chapter(db)
    apply_scenes(db, [one_scene(2, "Лес"), one_scene(5, "Таверна")])
    tavern = db.query(SoundPlace).filter_by(name="Таверна").one()
    # Вторая сцена ИИ на том же абзаце: так бывает после перепривязки, когда две сцены
    # сошлись на одном абзаце.
    twin = SoundMarker(book_id=BOOK, chapter_id="c1", segment_id="c1:00002", kind="scene",
                       place_id=tavern.id, payload={"anchor_text": "Абзац номер 2."},
                       text_sha256=db.query(SoundMarker).first().text_sha256, run_id="r1")
    db.add(twin)
    db.commit()
    twin_id = twin.id

    apply_scenes(db, [one_scene(2, "Таверна")], run="r2")

    rows = scenes_of(db)
    assert [(r.id, r.segment_id) for r in rows] == [(twin_id, "c1:00002")]
    assert db.get(SoundPlace, rows[0].place_id).name == "Таверна"


def test_a_scene_with_a_broken_segment_id_does_not_break_the_chapter(db, caplog):
    """Битый `segment_id` у старой сцены не валит переразметку всей главы: строка
    пропускается с предупреждением и удаляется, как удалялась раньше."""
    import logging

    from app.models import SoundMarker
    wide_chapter(db)
    apply_scenes(db, [one_scene(2)])
    broken = scenes_of(db)[0]
    broken.segment_id = "c1:abc"      # битый ключ, sha актуальный — перепривязка его не тронет
    db.commit()
    broken_id = broken.id

    with caplog.at_level(logging.WARNING):
        out = apply_scenes(db, [one_scene(2)], run="r2")

    assert out["added"] == 1
    assert db.get(SoundMarker, broken_id) is None
    assert [r.segment_id for r in scenes_of(db)] == ["c1:00002"]
    assert "битым segment_id" in caplog.text


def test_a_lost_scene_comes_back_active_with_its_id_and_track(db):
    """Сцена, потерявшая абзац (`lost`), — тоже кандидат: новый прогон возвращает её в
    строй той же строкой, вместе с её треком."""
    from app.models import SoundMarker
    wide_chapter(db)
    apply_scenes(db, [one_scene(2)])
    row = scenes_of(db)[0]
    row.status = "lost"
    db.commit()
    old_id = row.id
    ambient_track(db, old_id)

    out = apply_scenes(db, [one_scene(2)], run="r2")

    again = db.get(SoundMarker, old_id)
    assert out["added"] == 0
    assert (again.status, again.segment_id, again.source) == ("active", "c1:00002", "llm")
    assert ready_scene_ids(db) == {old_id}

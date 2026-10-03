"""Лор: отбор разделов энциклопедии, запись, ручки для диктора.

Шпаргалка по миру — единственный слой студии, сделанный прежде всего для диктора,
поэтому доступ проверяется его ролью, а не ролью редактора.
"""
import base64

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import session_serializer
from app.db import Base
from app.main import app
from app.models import Author, AuthorCharacter, LoreArticle, LoreImage, ScriptBook
from app.services.lore_import import import_lore, parse_images, parse_lore_fb2, topic_filter, topic_wanted

#: миры книги и общие разделы — так студия отбирает энциклопедию целой вселенной
BOOK_WORLD = topic_filter(("Полумрак.", "Парифат.", "Парифат,"), ("Нежить.",))

PIXEL = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

FB2 = """<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0" xmlns:l="http://www.w3.org/1999/xlink">
<body>
<section><title><p>Полумрак. Бароны.</p></title>
<p><strong>Бимькмолепус</strong>. Он же Темный Балаганщик. Барон-хвьадзукилай.</p>
<p>Баронов в Полумраке девять.</p>
</section>
<section><title><p>Полумрак. Карта мира.</p></title>
<p>Полумрак имеет форму чаши.</p>
<image l:href="#map.png"/>
</section>
<section><title><p>Нежить.</p></title>
<p>Нежить бывает высшая и низшая.</p>
</section>
<section><title><p>Каабар. Мироустройство.</p></title>
<p>Мир, которого нет в этих книгах.</p>
</section>
<section><title><p>Парифат. Пустой раздел.</p></title>
</section>
</body>
<binary content-type="image/png" id="map.png">{image}</binary>
</FictionBook>
""".replace("{image}", base64.b64encode(PIXEL).decode())


@pytest.fixture()
def factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    make = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    with make() as db:
        db.add(Author(id="a1", name="Александр Белозёров", slug="belozerov"))
        db.add(ScriptBook(id="b1", title="Крылья Полумрака", display_title='Белозёровы - "Крылья Полумрака"',
                          source_filename="k.docx", source_format="docx", author_id="a1", pipeline_mode="v2"))
        db.add(ScriptBook(id="b2", title="Чужая книга", source_filename="x.txt", source_format="txt"))
        db.add(AuthorCharacter(id="c1", author_id="a1", canonical_name="Бимькмолепус",
                               aliases="Темный Балаганщик", description="Барон-хвьадзукилай.",
                               source_topic="Полумрак. Бароны."))
        db.add(AuthorCharacter(id="c2", author_id="a1", canonical_name="Погтда", description="",
                               source_topic=""))
        db.add(AuthorCharacter(id="c3", author_id="a1", canonical_name="Житель Каабара",
                               description="Из мира, которого тут нет.",
                               source_topic="Каабар. Мироустройство."))
        db.commit()
    return make


def _client(factory, roles, monkeypatch):
    monkeypatch.setattr("app.v2.lore_api.SessionLocal", factory)
    client = TestClient(app)
    client.cookies.set("session", session_serializer.dumps(
        {"uid": "u1", "sub": "u1", "roles": roles, "display_name": "Кто-то"}))
    return client


def _loaded(factory, tmp_path):
    sections = parse_lore_fb2(FB2.encode("utf-8"), wanted=BOOK_WORLD)
    images = parse_images(FB2.encode("utf-8"))
    with factory() as db:
        summary = import_lore(db, "a1", sections, images, author_slug="belozerov", root=str(tmp_path))
        db.commit()
    return summary


# --- отбор и разбор -------------------------------------------------------


def test_beryotsya_mir_knigi_i_obshchie_razdely():
    assert BOOK_WORLD("Полумрак. Бароны.")
    assert BOOK_WORLD("Парифат, Мистерия. Заклинания.")
    assert BOOK_WORLD("Нежить.")


def test_chuzhoy_mir_ne_beryotsya():
    """Энциклопедия описывает всю вселенную; дикторам этих книг чужие миры ни к чему."""
    assert not BOOK_WORLD("Каабар. Мироустройство.")
    assert not BOOK_WORLD("Земля, Древний Шумер. Магия.")
    assert not BOOK_WORLD("")


def test_bez_otbora_beryotsya_vsyo_s_nazvaniem():
    """У другого автора свои миры: молча выбросить их энциклопедию хуже, чем показать всё."""
    assert topic_wanted("Каабар. Мироустройство.")
    assert not topic_wanted("   ")


def test_razdel_bez_teksta_ne_stanovitsya_statyoy():
    topics = [s.topic for s in parse_lore_fb2(FB2.encode("utf-8"), wanted=BOOK_WORLD)]
    assert "Парифат. Пустой раздел." not in topics
    assert topics == ["Полумрак. Бароны.", "Полумрак. Карта мира.", "Нежить."]


def test_kartinka_razdela_nahoditsya_i_po_ssylke_i_v_faile():
    sections = {s.topic: s for s in parse_lore_fb2(FB2.encode("utf-8"), wanted=BOOK_WORLD)}
    assert sections["Полумрак. Карта мира."].images == ["map.png"]
    assert parse_images(FB2.encode("utf-8"))["map.png"][1] == PIXEL


# --- запись ---------------------------------------------------------------


def test_zapis_kladyot_stati_i_kartu(factory, tmp_path):
    summary = _loaded(factory, tmp_path)
    assert (summary["created"], summary["images"]) == (3, 1)
    with factory() as db:
        assert db.query(LoreArticle).count() == 3
        image = db.query(LoreImage).one()
        assert open(image.stored_path, "rb").read() == PIXEL


def test_povtornyy_zapusk_obnovlyaet_a_ne_plodit(factory, tmp_path):
    _loaded(factory, tmp_path)
    summary = _loaded(factory, tmp_path)
    assert (summary["created"], summary["updated"]) == (0, 3)
    with factory() as db:
        assert db.query(LoreArticle).count() == 3


# --- ручки ----------------------------------------------------------------


def test_diktor_vidit_lor(factory, tmp_path, monkeypatch):
    """Шпаргалка для актёра: он её и открывает."""
    _loaded(factory, tmp_path)
    answer = _client(factory, ["dictor"], monkeypatch).get("/api/v2/books/b1/lore")
    assert answer.status_code == 200
    body = answer.json()
    assert body["author"]["name"] == "Александр Белозёров"
    assert [a["title"] for a in body["articles"]] == ["Полумрак. Бароны", "Полумрак. Карта мира", "Нежить"]


def test_kartochki_tolko_svoih_tem_i_tolko_s_opisaniem(factory, tmp_path, monkeypatch):
    _loaded(factory, tmp_path)
    body = _client(factory, ["dictor"], monkeypatch).get("/api/v2/books/b1/lore").json()
    names = [e["name"] for e in body["entities"]]
    assert names == ["Бимькмолепус"]  # Погтда без описания, житель Каабара — чужой мир
    assert body["entities"][0]["aliases"] == ["Темный Балаганщик"]


def test_kniga_bez_avtora_lora_ne_imeet(factory, tmp_path, monkeypatch):
    """Кнопку интерфейс показывает по непустому ответу — значит пустой должен быть честным."""
    _loaded(factory, tmp_path)
    body = _client(factory, ["dictor"], monkeypatch).get("/api/v2/books/b2/lore").json()
    assert body == {"author": None, "articles": [], "entities": []}


def test_telo_stati_prihodit_otdelno(factory, tmp_path, monkeypatch):
    _loaded(factory, tmp_path)
    client = _client(factory, ["dictor"], monkeypatch)
    listing = client.get("/api/v2/books/b1/lore").json()
    article = next(a for a in listing["articles"] if a["title"] == "Полумрак. Карта мира")
    assert "body" not in article
    full = client.get(f"/api/v2/lore/articles/{article['id']}").json()
    assert full["body"].startswith("Полумрак имеет форму чаши")
    assert full["images"] == [f"/api/v2/lore/images/a1/map.png"]


def test_poisk_vozvrashchaet_fragment(factory, tmp_path, monkeypatch):
    _loaded(factory, tmp_path)
    body = _client(factory, ["dictor"], monkeypatch).get("/api/v2/books/b1/lore/search?q=Балаганщик").json()
    assert len(body["hits"]) == 1
    assert "Балаганщик" in body["hits"][0]["snippet"]


def test_korotkiy_zapros_nichego_ne_ishchet(factory, tmp_path, monkeypatch):
    """Одна буква совпадёт со всем подряд — искать по ней бессмысленно."""
    _loaded(factory, tmp_path)
    assert _client(factory, ["dictor"], monkeypatch).get("/api/v2/books/b1/lore/search?q=а").json()["hits"] == []


def test_karta_otdayotsya_svoim_tipom(factory, tmp_path, monkeypatch):
    _loaded(factory, tmp_path)
    answer = _client(factory, ["dictor"], monkeypatch).get("/api/v2/lore/images/a1/map.png")
    assert answer.status_code == 200
    assert answer.headers["content-type"] == "image/png"


def test_neizvestnaya_statya_i_karta_eto_404(factory, tmp_path, monkeypatch):
    _loaded(factory, tmp_path)
    client = _client(factory, ["dictor"], monkeypatch)
    assert client.get("/api/v2/lore/articles/net").status_code == 404
    assert client.get("/api/v2/lore/images/a1/net.png").status_code == 404


def test_bez_vhoda_lor_ne_otdayotsya(factory, tmp_path, monkeypatch):
    _loaded(factory, tmp_path)
    monkeypatch.setattr("app.v2.lore_api.SessionLocal", factory)
    assert TestClient(app).get("/api/v2/books/b1/lore").status_code == 401


# --- статья за ролью ------------------------------------------------------


def _book_with_role(canon_id: str = "c1"):
    from app.models import Character, ScriptChapter
    from app.v2.models import V2Attribution, V2Segment

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    make = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    with make() as db:
        db.add(Author(id="a1", name="Александр Белозёров", slug="belozerov"))
        db.add(ScriptBook(id="b1", title="Крылья Полумрака", source_filename="k.docx",
                          source_format="docx", author_id="a1", pipeline_mode="v2"))
        db.add(ScriptChapter(id="ch1", book_id="b1", chapter_index=1, chapter_title="Глава 1",
                             source_text="текст", status="published_to_dictor"))
        db.add(AuthorCharacter(id="c1", author_id="a1", canonical_name="Бимькмолепус",
                               aliases="Темный Балаганщик", description="Барон-хвьадзукилай.",
                               source_topic="Полумрак. Бароны."))
        db.add(AuthorCharacter(id="c2", author_id="a1", canonical_name="Погтда", description=""))
        db.add(Character(id="ch-role", book_id="b1", name="Бимькмолепус",
                         author_character_id=canon_id))
        db.add(V2Segment(id="ch1:00001", book_id="b1", chapter_id="ch1", ordinal=1,
                         text="— Слова, — сказал он.", char_start=0, char_end=20))
        db.add(V2Attribution(id="at1", segment_id="ch1:00001", span_start=0, span_end=8,
                             speaker="Бимькмолепус", version=1))
        db.commit()
    return make


def test_rol_pokazyvaet_chto_o_ney_govorit_kanon():
    from app.v2.role_view import role_script

    with _book_with_role()() as db:
        payload = role_script(db, "b1", "Бимькмолепус")
        assert payload["canon"]["name"] == "Бимькмолепус"
        assert payload["canon"]["aliases"] == ["Темный Балаганщик"]
        assert payload["canon"]["description"].startswith("Барон")


def test_rol_bez_svyazi_s_kanonom_nichego_ne_vydumyvaet():
    from app.v2.role_view import role_script

    with _book_with_role(canon_id="")() as db:
        assert role_script(db, "b1", "Бимькмолепус")["canon"] is None


def test_kartochka_bez_opisaniya_ne_schitaetsya_statyoy():
    """Карточка из каста знает актёра, но о персонаже сказать ей нечего."""
    from app.v2.role_view import role_script

    with _book_with_role(canon_id="c2")() as db:
        assert role_script(db, "b1", "Бимькмолепус")["canon"] is None


def test_karta_chuzhogo_tipa_ne_stanovitsya_stranicey(factory, tmp_path, monkeypatch):
    """SVG и HTML из fb2 исполнились бы на нашем домене — отдаются как байты."""
    _loaded(factory, tmp_path)
    with factory() as db:
        row = db.query(LoreImage).filter_by(image_key="map.png").one()
        row.content_type = "image/svg+xml"
        open(row.stored_path, "wb").write(b'<svg xmlns="http://www.w3.org/2000/svg"><script/></svg>')
        db.commit()
    answer = _client(factory, ["dictor"], monkeypatch).get("/api/v2/lore/images/a1/map.png")
    assert answer.headers["content-type"] == "application/octet-stream"

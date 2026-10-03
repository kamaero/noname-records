"""Иллюстрации книги: разбор fb2, подсказка кандидатов, привязка, портрет у роли.

Картинки в книге не подписаны — привязку делает человек, а код отвечает за то, чтобы
подсказка не врала и чтобы привязанный портрет доехал до актёра.
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
from app.models import Author, AuthorCharacter, BookIllustration, Character, ScriptBook
from app.services.book_images import (
    candidates,
    cast_index,
    import_book_images,
    parse_book_images,
    sniff_image_type,
)
from app.v2.illustrations_api import portraits_for
from app.v2.role_view import role_canon

PIXEL = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
DATA = base64.b64encode(PIXEL).decode()

FB2 = f"""<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0" xmlns:l="http://www.w3.org/1999/xlink">
<description><title-info><coverpage><image l:href="#cover.jpg"/></coverpage>
<annotation><p>author.today 126111</p></annotation></title-info></description>
<body>
<section><title><p>Глава 1</p></title>
<p>Дгарнин вышел на балкон и посмотрел вниз.</p>
<image l:href="#img1.png" id="img1.png"/>
<p>Внизу ждала Погтда.</p>
</section>
<section><title><p>Глава 2</p></title>
<p>Пустая сцена без героев.</p>
<image l:href="#img2.png"/>
<image l:href="#missing.png"/>
</section>
</body>
<binary content-type="image/jpeg" id="cover.jpg">{DATA}</binary>
<binary content-type="image/png" id="img1.png">{DATA}</binary>
<binary id="img2.png" content-type="image/png">{DATA}</binary>
</FictionBook>
""".encode("utf-8")


# --- разбор файла ---------------------------------------------------------


def test_oblozhka_i_kartinka_bez_dannyh_ne_berutsya():
    assert [i.key for i in parse_book_images(FB2)] == ["img1.png", "img2.png"]


def test_poryadok_atributov_binary_ne_vazhen():
    """У img2 id стоит раньше content-type — картинка всё равно найдена."""
    image = parse_book_images(FB2)[1]
    assert image.content_type == "image/png"
    assert image.payload == PIXEL


def test_kontekst_vokrug_kartinki_i_glava():
    first = parse_book_images(FB2)[0]
    before, after = first.context.split("⟦здесь картинка⟧")
    assert "Дгарнин вышел на балкон" in before
    assert "Внизу ждала Погтда" in after
    assert first.chapter_hint == "Глава 1"


def test_obrezannyy_teg_na_krayu_okna_ne_popadaet_v_kontekst(monkeypatch):
    monkeypatch.setattr("app.services.book_images.CONTEXT_BEFORE", 45)
    context = parse_book_images(FB2)[0].context
    assert ">" not in context.split("⟦здесь картинка⟧")[0]


def test_hvost_tega_kartinki_ne_popadaet_v_kontekst():
    """После ссылки у тега бывают ещё атрибуты — они не текст книги."""
    after = parse_book_images(FB2)[0].context.split("⟦здесь картинка⟧")[1]
    assert "id=" not in after and "/>" not in after


def test_sluzhebnyy_blok_ne_popadaet_v_kontekst():
    assert "author.today" not in parse_book_images(FB2)[0].context


# --- подсказка кандидатов -------------------------------------------------

INDEX = [
    ("дгарнин", "Дгарнин", "c1"),
    ("дгар", "Дгарнин", "c1"),
    ("погтда", "Погтда", "c2"),
    ("мать", "Мать", "c3"),
]


def test_alias_vnutri_imeni_ne_schitaetsya_dvazhdy():
    """«Дгар» — начало «Дгарнина»: одно упоминание, а не два."""
    assert candidates("Дгарнин вошёл.", INDEX) == [{"character_id": "c1", "name": "Дгарнин", "hits": 1}]


def test_imya_v_kosvennom_padezhe_nahoditsya():
    """Русский склоняет имена — совпадение по началу слова, конец свободен."""
    assert [c["name"] for c in candidates("Все ждали Дгарнина и Погтду.", INDEX)] == ["Дгарнин"]


def test_imya_vnutri_chuzhogo_slova_ne_schitaetsya():
    """«Мать» не должна находиться в «Кормать» или «Обнимать»."""
    assert candidates("Она стала обнимать сына.", INDEX) == []


def test_chashche_nazvannyy_vyshe():
    text = "Погтда смеялась. Дгарнин молчал. Погтда ушла."
    assert [c["name"] for c in candidates(text, INDEX)] == ["Погтда", "Дгарнин"]


# --- база: загрузка, привязка, портрет -------------------------------------


@pytest.fixture()
def factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    make = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    with make() as db:
        db.add(Author(id="a1", name="Александр Белозёров", slug="belozerov"))
        db.add(ScriptBook(id="b1", title="Крылья Полумрака", source_filename="k.fb2", source_format="fb2",
                          author_id="a1", pipeline_mode="v2"))
        db.add(AuthorCharacter(id="c1", author_id="a1", canonical_name="Дгарнин",
                               description="Сын Бимькмолепуса.", source_topic="Полумрак."))
        # Карточка из каста, без статьи энциклопедии — портрет ей всё равно можно дать.
        db.add(AuthorCharacter(id="c2", author_id="a1", canonical_name="Погтда", description=""))
        db.add(Character(book_id="b1", name="Дгарнин", aliases="Дгар, Дгарнин", author_character_id="c1"))
        db.add(Character(book_id="b1", name="Погтда", aliases="", author_character_id="c2"))
        db.add(Character(book_id="b1", name="Стражник", aliases=""))
        db.commit()
    return make


def _load(factory, tmp_path):
    with factory() as db:
        summary = import_book_images(db, book_id="b1", author_id="a1",
                                     images=parse_book_images(FB2), root=str(tmp_path))
        db.commit()
    return summary


def _client(factory, roles, monkeypatch):
    monkeypatch.setattr("app.v2.illustrations_api.SessionLocal", factory)
    client = TestClient(app)
    client.cookies.set("session", session_serializer.dumps(
        {"uid": "u1", "sub": "u1", "roles": roles, "display_name": "Редактор"}))
    return client


def _ids(factory):
    with factory() as db:
        return [r.id for r in db.query(BookIllustration).order_by(BookIllustration.ordinal).all()]


def test_rol_bez_kanona_v_podskazki_ne_idyot(factory):
    with factory() as db:
        names = {name for _key, name, _cid in cast_index(db, "b1")}
    assert names == {"Дгарнин", "Погтда"}


def test_perezalivka_ne_trogaet_privyazku(factory, tmp_path):
    _load(factory, tmp_path)
    with factory() as db:
        row = db.query(BookIllustration).filter_by(image_key="img1.png").one()
        row.character_id, row.status = "c1", "bound"
        db.commit()
    summary = _load(factory, tmp_path)
    assert (summary["created"], summary["updated"]) == (0, 2)
    with factory() as db:
        row = db.query(BookIllustration).filter_by(image_key="img1.png").one()
        assert (row.character_id, row.status) == ("c1", "bound")


def test_spisok_s_podskazkoy_i_kastom_bez_dubley(factory, tmp_path, monkeypatch):
    _load(factory, tmp_path)
    body = _client(factory, ["author"], monkeypatch).get("/api/v2/books/b1/illustrations").json()
    assert body["counts"] == {"total": 2, "bound": 0, "skipped": 0}
    # У Дгарнина три написания (имя, «Дгар», повтор имени) — в поиске он один.
    assert [c["name"] for c in body["cast"]] == ["Дгарнин", "Погтда"]
    assert [c["name"] for c in body["items"][0]["candidates"]] == ["Дгарнин", "Погтда"]


def test_diktor_ne_privyazyvaet_no_vidit_kartinku(factory, tmp_path, monkeypatch):
    _load(factory, tmp_path)
    first = _ids(factory)[0]
    client = _client(factory, ["dictor"], monkeypatch)
    assert client.get("/api/v2/books/b1/illustrations").status_code == 403
    assert client.post(f"/api/v2/illustrations/{first}", json={"character_id": "c1"}).status_code == 403
    image = client.get(f"/api/v2/illustrations/{first}/image")
    assert image.status_code == 200
    assert image.headers["content-type"] == "image/png"


def test_privyazka_otlozhit_i_snyat(factory, tmp_path, monkeypatch):
    _load(factory, tmp_path)
    first = _ids(factory)[0]
    client = _client(factory, ["author"], monkeypatch)
    assert client.post(f"/api/v2/illustrations/{first}", json={"character_id": "c1"}).json()["status"] == "bound"
    assert client.post(f"/api/v2/illustrations/{first}", json={"skip": True}).json()["status"] == "skipped"
    answer = client.post(f"/api/v2/illustrations/{first}", json={"character_id": ""}).json()
    assert (answer["status"], answer["character_id"]) == ("new", "")


def test_neizvestnyy_personazh_eto_oshibka(factory, tmp_path, monkeypatch):
    _load(factory, tmp_path)
    first = _ids(factory)[0]
    answer = _client(factory, ["author"], monkeypatch).post(
        f"/api/v2/illustrations/{first}", json={"character_id": "net"})
    assert answer.status_code == 400


def test_portret_samaya_rannyaya_privyazannaya(factory, tmp_path):
    _load(factory, tmp_path)
    first, second = _ids(factory)
    with factory() as db:
        for row in db.query(BookIllustration).all():
            row.character_id, row.status = "c1", "bound"
        db.commit()
        assert portraits_for(db, ["c1"]) == {"c1": f"/api/v2/illustrations/{first}/image"}
        db.get(BookIllustration, first).status = "skipped"
        db.commit()
        assert portraits_for(db, ["c1"]) == {"c1": f"/api/v2/illustrations/{second}/image"}


def test_portret_doezzhaet_do_roli(factory, tmp_path):
    _load(factory, tmp_path)
    first = _ids(factory)[0]
    with factory() as db:
        row = db.get(BookIllustration, first)
        row.character_id, row.status = "c1", "bound"
        db.commit()
        assert role_canon(db, "b1", "Дгар")["portrait"] == f"/api/v2/illustrations/{first}/image"


def test_portret_bez_stati_vsyo_ravno_pokazyvaetsya(factory, tmp_path):
    """У Нами нет статьи энциклопедии, но человек дал ей портрет — актёр его увидит."""
    _load(factory, tmp_path)
    first = _ids(factory)[0]
    with factory() as db:
        assert role_canon(db, "b1", "Погтда") is None  # ни статьи, ни портрета — молчим
        row = db.get(BookIllustration, first)
        row.character_id, row.status = "c2", "bound"
        db.commit()
        canon = role_canon(db, "b1", "Погтда")
        assert canon["description"] == ""
        assert canon["portrait"] == f"/api/v2/illustrations/{first}/image"


def test_tip_beryotsya_iz_baytov_a_ne_iz_podpisi(factory, tmp_path, monkeypatch):
    """У Белозёрова все картинки подписаны `image/jpg`, а внутри PNG/WebP/JPEG."""
    _load(factory, tmp_path)
    first = _ids(factory)[0]
    with factory() as db:
        db.get(BookIllustration, first).content_type = "image/jpg"
        db.commit()
    answer = _client(factory, ["dictor"], monkeypatch).get(f"/api/v2/illustrations/{first}/image")
    assert answer.headers["content-type"] == "image/png"


def test_ne_kartinka_ne_otdayotsya_kak_stranica(factory, tmp_path, monkeypatch):
    """HTML, подписанный картинкой, исполнился бы на нашем домене — отдаётся байтами."""
    _load(factory, tmp_path)
    first = _ids(factory)[0]
    with factory() as db:
        row = db.get(BookIllustration, first)
        row.content_type = "text/html"
        open(row.stored_path, "wb").write(b"<script>alert(1)</script>")
        db.commit()
    answer = _client(factory, ["dictor"], monkeypatch).get(f"/api/v2/illustrations/{first}/image")
    assert answer.headers["content-type"] == "application/octet-stream"
    assert answer.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize("head, kind", [
    (b"\xff\xd8\xff\xe0\x00\x10JFIF", "image/jpeg"),
    (b"RIFF\x00\x00\x00\x00WEBPVP8 ", "image/webp"),
    (b"GIF89a\x01\x00", "image/gif"),
    (b"<svg xmlns=", ""),
])
def test_format_po_pervym_baytam(head, kind):
    assert sniff_image_type(head) == kind


def test_hab_poluchaet_tolko_cifry(factory, tmp_path, monkeypatch):
    """Строку «Портреты» на хабе незачем кормить 190 контекстами."""
    _load(factory, tmp_path)
    body = _client(factory, ["author"], monkeypatch).get("/api/v2/books/b1/illustrations?summary=1").json()
    assert body == {"counts": {"total": 2, "bound": 0, "skipped": 0}}

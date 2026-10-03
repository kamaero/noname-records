"""Книга подписана «Автор - "Название"», а код книги остаётся при названии.

Автор и название держатся порознь по одной причине: из `title` выводится код,
которым актёры подписывают файлы. Пока автор жил в названии, «Примеродины - "Семья
волшебников. книга 2"» давала код `RVK2` вместо `SVK2` — и присланная запись
искала книгу, которой нет.
"""
from app.services.audio_naming import book_token
from app.services.audio_uploads import derive_book_code
from app.services.book_title import apply_book_title, compose_display_title, split_display_title
from app.pipeline.book_parser import parse_filename_title


class _Book:
    def __init__(self, title: str = "", author_label: str = "", display_title: str = ""):
        self.title, self.author_label, self.display_title = title, author_label, display_title


def test_witrina_sobiraetsya_iz_avtora_i_nazvaniya():
    assert compose_display_title("Примеродины", "Карта Переправы") == 'Примеродины - "Карта Переправы"'


def test_bez_avtora_ostayotsya_odno_nazvanie():
    assert compose_display_title("", "Карта Переправы") == "Карта Переправы"


def test_kavychki_ne_udvaivayutsya():
    """Человек вписывает название уже в кавычках — витрина не должна их удваивать."""
    assert compose_display_title("Примеродины", '«Карта Переправы»') == 'Примеродины - "Карта Переправы"'


def test_sborka_idempotentna():
    once = compose_display_title("Примеродины", "Карта Переправы")
    author, title = split_display_title(once)
    assert compose_display_title(author, title) == once


def test_staroe_nazvanie_razbiraetsya_obratno():
    """Книги, залитые до разделения полей, носят автора внутри `title`."""
    assert split_display_title('Примеродины - "Сад великанов. книга 2"') == ("Примеродины", "Сад великанов. книга 2")


def test_obychnoe_nazvanie_s_defisom_ne_schitaetsya_avtorom():
    assert split_display_title("Стажёры - хроника полёта") == ("", "Стажёры - хроника полёта")


def test_avtor_ne_popadaet_v_kod_knigi():
    book = _Book()
    apply_book_title(book, author="Примеродины", title="Сад великанов. Книга 2")
    assert book.title == "Сад великанов. Книга 2"
    assert book.display_title == 'Примеродины - "Сад великанов. Книга 2"'
    assert book_token(derive_book_code(book.title)) == "SVK2"


def test_kod_zapisannoy_knigi_perezhivaet_pereimenovanie():
    """У «Карты» 200+ принятых файлов с кодом КП — правка регистра его не трогает."""
    book = _Book(title="Карта переправы")
    before = book_token(derive_book_code(book.title))
    apply_book_title(book, author="Примеродины", title="Карта Переправы")
    assert book_token(derive_book_code(book.title)) == before == "KP"


def test_pustoe_nazvanie_ne_stiraet_knigu():
    """Название — ключ к записям актёров; пустое поле не должно его обнулять."""
    book = _Book(title="Карта Переправы", author_label="Примеродины")
    apply_book_title(book, author="Примеродины", title="   ")
    assert book.title == "Карта Переправы"


def test_imya_fayla_daet_avtora_i_nazvanie_porozn():
    assert parse_filename_title("primerodiny_sad_velikanov_2.fb2") == ("Примеродины", "Сад великанов 2")
    assert parse_filename_title("Карта Переправы.docx") == ("", "Карта переправы")


def test_zagruzka_delit_imya_fayla_na_avtora_i_nazvanie(tmp_path, monkeypatch):
    """Файл «Примеродины Сад великанов…» даёт витрину с автором, но чистый `title`."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db import Base
    from app.services.book_import import enqueue_book

    monkeypatch.setattr("app.services.book_import._persist_book_source_snapshot", lambda *a, **k: None)
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        book = enqueue_book(db=db, filename="primerodiny_sad_velikanov_2.txt",
                            payload="Глава 1\nТекст.\n".encode("utf-8"))
        assert book.title == "Сад великанов 2"
        assert book.author_label == "Примеродины"
        assert book.display_title == 'Примеродины - "Сад великанов 2"'
        assert book_token(derive_book_code(book.title)) == "SV2"


def test_zagruzka_beret_vpisannoe_chelovekom(tmp_path, monkeypatch):
    """Поля формы главнее имени файла — регистр и том человек ставит сам."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db import Base
    from app.services.book_import import enqueue_book

    monkeypatch.setattr("app.services.book_import._persist_book_source_snapshot", lambda *a, **k: None)
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        book = enqueue_book(db=db, filename="kniga.txt", payload="Глава 1\nТекст.\n".encode("utf-8"),
                            title="Карта Переправы", author="Примеродины")
        assert book.title == "Карта Переправы"
        assert book.display_title == 'Примеродины - "Карта Переправы"'


def _factory():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import Base
    from app.models import ScriptBook

    # StaticPool: одно соединение на всю базу в памяти — иначе ручка открывает
    # свою сессию и видит пустую базу.
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    with factory() as db:
        db.add(ScriptBook(id="b1", title="Карта переправы", display_title="Карта переправы",
                          source_filename="Карта Переправы.docx", source_format="docx", pipeline_mode="v2"))
        db.commit()
    return factory


def _client(roles, monkeypatch):
    from fastapi.testclient import TestClient

    from app.auth import session_serializer
    from app.main import app

    factory = _factory()
    monkeypatch.setattr("app.v2.api.SessionLocal", factory)
    client = TestClient(app)
    client.cookies.set("session", session_serializer.dumps(
        {"uid": "u1", "sub": "u1", "roles": roles, "display_name": "Редактор"}))
    return client, factory


def test_pereimenovanie_pishet_tri_polya_i_vozvrashchaet_kod(monkeypatch):
    from app.models import ScriptBook

    client, factory = _client(["author"], monkeypatch)
    answer = client.post("/api/v2/books/b1/title", json={"author": "Примеродины", "title": "Карта Переправы"})

    assert answer.status_code == 200
    body = answer.json()
    assert body["display_title"] == 'Примеродины - "Карта Переправы"'
    # Код остался прежним — 200+ принятых файлов «КП» не осиротели.
    assert body["book_code"] == "KP"
    with factory() as db:
        book = db.get(ScriptBook, "b1")
        assert (book.title, book.author_label) == ("Карта Переправы", "Примеродины")


def test_pereimenovanie_bez_nazvaniya_otvergaetsya(monkeypatch):
    client, _ = _client(["author"], monkeypatch)
    answer = client.post("/api/v2/books/b1/title", json={"author": "Примеродины", "title": "  "})
    assert answer.status_code == 400


def test_diktor_knigu_ne_pereimenuet(monkeypatch):
    client, _ = _client(["dictor"], monkeypatch)
    answer = client.post("/api/v2/books/b1/title", json={"author": "Кто-то", "title": "Своё"})
    assert answer.status_code == 403


def test_smena_koda_pod_zapisyami_trebuet_podtverzhdeniya(monkeypatch):
    """У записей нет ссылки на книгу — только код из названия. Отрывать их молча нельзя."""
    from app.models import AudioFile, ScriptBook

    client, factory = _client(["author"], monkeypatch)
    with factory() as db:
        db.add(AudioFile(id="a1", book_code="КП", original_filename="КП_Ch01_Роль_Актёр.wav",
                         stored_key="k", mime_type="audio/wav", size_bytes=1, chapter="1", role="Роль"))
        db.commit()

    answer = client.post("/api/v2/books/b1/title", json={"author": "Примеродины", "title": "Другая книга"})
    assert answer.status_code == 409
    body = answer.json()
    assert (body["error"], body["was_code"], body["files"]) == ("code_changes", "KP", 1)
    with factory() as db:
        assert db.get(ScriptBook, "b1").title == "Карта переправы"

    # С открытыми глазами — переименование проходит.
    answer = client.post("/api/v2/books/b1/title",
                         json={"author": "Примеродины", "title": "Другая книга", "confirm": True})
    assert answer.status_code == 200
    with factory() as db:
        assert db.get(ScriptBook, "b1").title == "Другая книга"


def test_pravka_registra_koda_ne_menyaet_i_ni_o_chem_ne_sprashivaet(monkeypatch):
    from app.models import AudioFile

    client, factory = _client(["author"], monkeypatch)
    with factory() as db:
        db.add(AudioFile(id="a1", book_code="КП", original_filename="КП_Ch01_Роль_Актёр.wav",
                         stored_key="k", mime_type="audio/wav", size_bytes=1, chapter="1", role="Роль"))
        db.commit()

    answer = client.post("/api/v2/books/b1/title", json={"author": "Примеродины", "title": "Карта Переправы"})
    assert answer.status_code == 200

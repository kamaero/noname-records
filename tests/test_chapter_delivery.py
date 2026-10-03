"""Глава записана — и её пора отдавать в монтаж.

Готовность до сих пор считалась через распознавание речи: сколько реплик нашлось в
аудио. Распознавание не запускалось ни разу, поэтому готовность всегда показывала ноль,
и вопрос «эту главу уже можно сводить?» оставался без ответа.

Ответ дешевле, чем казалось: у каждой говорящей роли главы либо есть дубль, либо нет.
Это не то же, что «все реплики произнесены» — но это то, что можно узнать сегодня, не
потратив ни секунды машинного времени.

Пробы в счёт не идут: проба — заявка на роль, а не запись главы.
"""
import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.services.audio_uploads as au
from app.db import Base
from app.models import AudioFile, Character, ScriptBook, ScriptChapter
from app.services import nas_health
from app.services.chapter_delivery import chapter_recording_status
from app.time_utils import utcnow_naive


class _FakeStorage:
    """Хранилище знает и куда положило, и что именно: разбор файла спрашивает путь,
    сверка — сумму."""

    def write_file(self, key, payload, location="nas"):
        import hashlib
        return hashlib.md5(payload).hexdigest()

    def write_stream(self, key, source, chunk_size=1024 * 1024, location="nas"):
        import hashlib
        digest = hashlib.md5()
        written = 0
        while True:
            chunk = source.read(65536)
            if not chunk:
                break
            digest.update(chunk)
            written += len(chunk)
        return written, digest.hexdigest()

    def resolve_path(self, key, location="nas"):
        return f"/nowhere/{key}"

    def local_root(self):
        return "/nowhere"

    def free_bytes(self, path):
        # Сторож места (задача 5) спрашивает диск при каждом приёме — этому
        # фейку интересна только сама запись, поэтому места всегда с запасом.
        return 10**12


@pytest.fixture()
def studio(monkeypatch):
    monkeypatch.setattr(au, "audio_storage", _FakeStorage())
    monkeypatch.setattr(au, "probe_audio_file", lambda path: {"duration_seconds": 12.5})
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        book = ScriptBook(title="Крылья полумрака", source_filename="k.txt", source_format="txt", created_at=utcnow_naive())
        db.add(book)
        db.flush()
        chapter = ScriptChapter(book_id=book.id, chapter_index=4, chapter_title="Глава 4. Проба пера", status="published")
        db.add(chapter)
        db.add(Character(book_id=book.id, name="Дгарнин", actor_name="Сергей Зотов"))
        db.add(Character(book_id=book.id, name="Сатухух", actor_name="Роман Сомов"))
        db.flush()
        yield db, book, chapter


def _roles(monkeypatch, counts):
    monkeypatch.setattr("app.services.chapter_delivery.chapter_role_counts", lambda db, chapter_id: counts)


def _take(db, chapter, *, role, actor, kind="take", mirrored_at=None):
    item = au.store_audio_file(
        db, payload=b"x" * 100, mime_type="audio/wav", original_filename=f"{role}.wav",
        book_code="КП", chapter=chapter.chapter_title, role=role, actor_name=actor,
        kind=kind, safe_name=lambda s: s,
    )
    if mirrored_at is not None:
        # Зеркало ставит отметку времени и состояние вместе: без mirror_state="ok"
        # запись выглядела бы наполовину зеркалированной.
        item.mirrored_at = mirrored_at
        item.mirror_state = "ok"
    return item


class TestWhatIsMissing:
    def test_a_chapter_nobody_recorded_is_not_ready(self, studio, monkeypatch):
        db, _, chapter = studio
        _roles(monkeypatch, {"Дгарнин": 40, "Сатухух": 12})

        status = chapter_recording_status(db, chapter.id)

        assert status["ready"] is False
        assert [row["role"] for row in status["roles"] if not row["files"]] == ["Дгарнин", "Сатухух"]

    def test_half_a_chapter_is_still_not_ready(self, studio, monkeypatch):
        db, _, chapter = studio
        _roles(monkeypatch, {"Дгарнин": 40, "Сатухух": 12})
        _take(db, chapter, role="Дгарнин", actor="Сергей Зотов")
        db.flush()

        status = chapter_recording_status(db, chapter.id)

        assert status["ready"] is False
        assert status["recorded_roles"] == 1 and status["total_roles"] == 2

    def test_every_role_recorded_makes_it_ready(self, studio, monkeypatch):
        db, _, chapter = studio
        _roles(monkeypatch, {"Дгарнин": 40, "Сатухух": 12})
        _take(db, chapter, role="Дгарнин", actor="Сергей Зотов")
        _take(db, chapter, role="Сатухух", actor="Роман Сомов")
        db.flush()

        assert chapter_recording_status(db, chapter.id)["ready"] is True


class TestWhatDoesNotCount:
    def test_an_audition_is_not_a_recording_of_the_chapter(self, studio, monkeypatch):
        db, _, chapter = studio
        _roles(monkeypatch, {"Дгарнин": 40})
        _take(db, chapter, role="Дгарнин", actor="Кто-То Ещё", kind="audition")
        db.flush()

        assert chapter_recording_status(db, chapter.id)["ready"] is False

    def test_a_take_of_another_chapter_does_not_count_either(self, studio, monkeypatch):
        db, book, chapter = studio
        _roles(monkeypatch, {"Дгарнин": 40})
        au.store_audio_file(
            db, payload=b"x", mime_type="audio/wav", original_filename="d.wav", book_code="КП",
            chapter="Глава 9. Другая", role="Дгарнин", actor_name="Сергей Зотов", safe_name=lambda s: s,
        )
        db.flush()

        assert chapter_recording_status(db, chapter.id)["ready"] is False

    def test_a_chapter_with_no_speaking_roles_is_not_called_ready(self, studio, monkeypatch):
        """Пустая глава — не готовая глава: готовить в ней нечего."""
        db, _, chapter = studio
        _roles(monkeypatch, {})

        status = chapter_recording_status(db, chapter.id)

        assert status["ready"] is False and status["total_roles"] == 0


class TestWhatTheRowSays:
    def test_it_names_the_actor_the_lines_and_the_minutes(self, studio, monkeypatch):
        db, _, chapter = studio
        _roles(monkeypatch, {"Дгарнин": 40})
        _take(db, chapter, role="Дгарнин", actor="Сергей Зотов")
        db.flush()

        row = chapter_recording_status(db, chapter.id)["roles"][0]

        assert row["role"] == "Дгарнин"
        assert row["actor_name"] == "Сергей Зотов"
        assert row["lines"] == 40
        assert row["files"] == 1
        assert row["duration_seconds"] == pytest.approx(12.5)


class TestTheFilesThemselves:
    """Опись главы: не «сколько файлов», а какие именно.

    Число отвечало на вопрос готовности — «у роли есть дубль или нет». Опись отвечает
    на другой: что лежит на диске, можно ли это послушать и тем ли оно оказалось.
    """

    def test_the_row_carries_each_take_and_not_only_how_many(self, studio, monkeypatch):
        db, _, chapter = studio
        _roles(monkeypatch, {"Дгарнин": 40})
        stored = _take(db, chapter, role="Дгарнин", actor="Сергей Зотов")
        db.flush()

        take = chapter_recording_status(db, chapter.id)["roles"][0]["takes"][0]

        assert take["id"] == stored.id
        assert take["canonical_filename"] == stored.canonical_filename
        assert take["size_bytes"] == 100
        assert take["duration_seconds"] == pytest.approx(12.5)
        assert take["stored_key"] == stored.stored_key
        assert take["uploaded_at"]

    def test_two_takes_of_one_role_are_both_listed_oldest_first(self, studio, monkeypatch):
        """У роли бывает несколько дублей, и порядок — это порядок перезаписи."""
        db, _, chapter = studio
        _roles(monkeypatch, {"Дгарнин": 40})
        first = _take(db, chapter, role="Дгарнин", actor="Сергей Зотов")
        db.flush()
        first.uploaded_at = first.uploaded_at.replace(year=first.uploaded_at.year - 1)
        second = _take(db, chapter, role="Дгарнин", actor="Сергей Зотов")
        db.flush()

        row = chapter_recording_status(db, chapter.id)["roles"][0]

        assert row["files"] == 2
        assert [take["id"] for take in row["takes"]] == [first.id, second.id]

    def test_a_role_nobody_recorded_carries_an_empty_list(self, studio, monkeypatch):
        """Пусто, а не отсутствует: экрану нечего разворачивать, но и падать не на чем."""
        db, _, chapter = studio
        _roles(monkeypatch, {"Дгарнин": 40})

        assert chapter_recording_status(db, chapter.id)["roles"][0]["takes"] == []


class TestNothingOnDiskStaysInvisible:
    """Дубль, чья роль ушла из сценария, — переименовали её в карте персонажей или
    слили с другой. Файл лежит на диске, в свою роль уже не попадёт, и без отдельной
    корзины опись главы молчала бы о нём: ровно та слепота, ради которой экран и
    затевался.
    """

    def test_a_take_whose_role_left_the_script_is_not_lost(self, studio, monkeypatch):
        db, _, chapter = studio
        _roles(monkeypatch, {"Дгарнин": 40})
        stray = _take(db, chapter, role="Сатухух", actor="Роман Сомов")
        db.flush()

        status = chapter_recording_status(db, chapter.id)

        assert [take["id"] for take in status["orphan_takes"]] == [stray.id]
        assert status["orphan_takes"][0]["role"] == "Сатухух"

    def test_a_recorded_role_never_lands_in_the_bucket(self, studio, monkeypatch):
        db, _, chapter = studio
        _roles(monkeypatch, {"Дгарнин": 40})
        _take(db, chapter, role="Дгарнин", actor="Сергей Зотов")
        db.flush()

        assert chapter_recording_status(db, chapter.id)["orphan_takes"] == []

    def test_an_audition_is_not_a_lost_take(self, studio, monkeypatch):
        """Проба и не должна быть в главе — она заявка на роль, а не запись."""
        db, _, chapter = studio
        _roles(monkeypatch, {"Дгарнин": 40})
        _take(db, chapter, role="Сатухух", actor="Кто-То Ещё", kind="audition")
        db.flush()

        assert chapter_recording_status(db, chapter.id)["orphan_takes"] == []

    def test_roles_come_in_the_order_of_who_speaks_most(self, studio, monkeypatch):
        db, _, chapter = studio
        _roles(monkeypatch, {"Сатухух": 12, "Дгарнин": 40})

        assert [row["role"] for row in chapter_recording_status(db, chapter.id)["roles"]] == ["Дгарнин", "Сатухух"]


class TestTheArchive:
    """Собирается без сжатия: WAV ему почти не поддаётся, а пачка на полгигабайта,
    сложенная как есть, идёт со скоростью диска вместо минут работы процессора."""

    def _real_storage(self, monkeypatch, tmp_path):
        from app.services import audio_storage

        # Оба корня в один каталог: приём с этой выкатки пишет на локальный диск,
        # а сборка архива читает по `item.location` — подменив только NAS, тест
        # проверял бы, что запись и чтение случайно разошлись.
        monkeypatch.setattr(audio_storage, "local_root", lambda: str(tmp_path))
        monkeypatch.setattr(audio_storage, "nas_root", lambda: str(tmp_path))
        monkeypatch.setattr(au, "audio_storage", audio_storage)

    def test_it_holds_the_takes_and_an_inventory(self, studio, monkeypatch, tmp_path):
        import zipfile

        from app.services.chapter_delivery import build_chapter_archive

        db, _, chapter = studio
        self._real_storage(monkeypatch, tmp_path)
        _roles(monkeypatch, {"Дгарнин": 40, "Сатухух": 12})
        _take(db, chapter, role="Дгарнин", actor="Сергей Зотов")
        _take(db, chapter, role="Сатухух", actor="Роман Сомов")
        db.flush()

        target = tmp_path / "chapter.zip"
        result = build_chapter_archive(db, chapter.id, str(target))

        assert result["files_written"] == 2 and result["files_missing"] == []
        with zipfile.ZipFile(target) as archive:
            names = archive.namelist()
            assert "опись.txt" in names
            assert sorted(n for n in names if n.endswith(".wav")) == [
                "KP_Ch04_Dgarnin_SergeyZotov.wav",
                "KP_Ch04_Satuhuh_RomanSomov.wav",
            ]
            inventory = archive.read("опись.txt").decode("utf-8")
        assert "Крылья полумрака" in inventory
        assert "Сергей Зотов" in inventory

    def test_the_title_is_not_said_twice(self, studio, monkeypatch, tmp_path):
        """Название главы уже начинается с «Глава 4» — приписывать номер ещё раз незачем."""
        import zipfile

        from app.services.chapter_delivery import build_chapter_archive

        db, _, chapter = studio
        self._real_storage(monkeypatch, tmp_path)
        _roles(monkeypatch, {"Дгарнин": 40})
        _take(db, chapter, role="Дгарнин", actor="Сергей Зотов")
        db.flush()

        build_chapter_archive(db, chapter.id, str(tmp_path / "c.zip"))

        with zipfile.ZipFile(tmp_path / "c.zip") as archive:
            inventory = archive.read("опись.txt").decode("utf-8")
        assert "Глава 4. Проба пера" in inventory
        assert "Глава 4. Глава 4" not in inventory

    def test_the_inventory_names_who_is_still_missing(self, studio, monkeypatch, tmp_path):
        import zipfile

        from app.services.chapter_delivery import build_chapter_archive

        db, _, chapter = studio
        self._real_storage(monkeypatch, tmp_path)
        _roles(monkeypatch, {"Дгарнин": 40, "Сатухух": 12})
        _take(db, chapter, role="Дгарнин", actor="Сергей Зотов")
        db.flush()

        target = tmp_path / "chapter.zip"
        build_chapter_archive(db, chapter.id, str(target))

        with zipfile.ZipFile(target) as archive:
            assert "НЕ ЗАПИСАНЫ: Сатухух" in archive.read("опись.txt").decode("utf-8")

    def test_a_file_that_vanished_from_storage_is_reported_not_hidden(self, studio, monkeypatch, tmp_path):
        from app.services.chapter_delivery import build_chapter_archive

        db, _, chapter = studio
        self._real_storage(monkeypatch, tmp_path)
        _roles(monkeypatch, {"Дгарнин": 40})
        item = _take(db, chapter, role="Дгарнин", actor="Сергей Зотов")
        db.flush()
        os.remove(tmp_path / item.stored_key)

        result = build_chapter_archive(db, chapter.id, str(tmp_path / "chapter.zip"))

        assert result["files_written"] == 0
        assert result["files_missing"] == ["KP_Ch04_Dgarnin_SergeyZotov.wav"]

    def test_a_legacy_take_that_still_lives_on_the_nas_is_packed_too(self, studio, monkeypatch, tmp_path):
        """Шестьдесят записей до переезда физически лежат на NAS, и `location` у них
        так и говорит. Сборка архива обязана идти за файлом по его месту, а не по
        нынешнему умолчанию.

        Стережёт тихую потерю: если ветвление по `item.location` однажды сочтут
        мёртвым кодом и уберут, архив главы отдастся без этих файлов — молча, без
        единого исключения, и недостачу заметит только диктор в студии."""
        import zipfile

        from app.services import audio_storage
        from app.services.chapter_delivery import build_chapter_archive

        db, _, chapter = studio
        # Корни РАЗНЫЕ: только так видно, что за легаси-файлом сходили именно на NAS.
        local, nas = tmp_path / "rec", tmp_path / "nas"
        monkeypatch.setattr(audio_storage, "local_root", lambda: str(local))
        monkeypatch.setattr(audio_storage, "nas_root", lambda: str(nas))
        monkeypatch.setattr(au, "audio_storage", audio_storage)
        _roles(monkeypatch, {"Дгарнин": 40})

        item = _take(db, chapter, role="Дгарнин", actor="Сергей Зотов")
        # Приём положил файл локально; переносим его туда, где лежат старые записи.
        moved = nas / str(item.stored_key)
        moved.parent.mkdir(parents=True, exist_ok=True)
        moved.write_bytes((local / str(item.stored_key)).read_bytes())
        (local / str(item.stored_key)).unlink()
        item.location = "nas"
        db.flush()
        nas_health.record_probe(True)

        target = tmp_path / "chapter.zip"
        result = build_chapter_archive(db, chapter.id, str(target))

        assert result["files_written"] == 1 and result["files_missing"] == []
        with zipfile.ZipFile(target) as archive:
            assert item.canonical_filename in " ".join(archive.namelist())

    @pytest.mark.parametrize("nas_state", [False, None])
    def test_a_legacy_nas_take_is_not_touched_unless_the_nas_is_confirmed_alive(
        self, studio, monkeypatch, tmp_path, nas_state
    ):
        """Архив главы читал NAS-файлы без сторожа: при мёртвом монтировании запрос
        вис, держа поток и сессию базы. Теперь — быстрый отказ целиком, а не архив
        без части дублей: неполный архив звукорежиссёр принял бы за полный."""
        from app.services import audio_storage
        from app.services.chapter_delivery import (
            StorageUnavailable,
            build_chapter_archive,
        )

        db, _, chapter = studio
        monkeypatch.setattr(audio_storage, "local_root", lambda: str(tmp_path / "rec"))
        monkeypatch.setattr(audio_storage, "nas_root", lambda: str(tmp_path / "nas"))
        monkeypatch.setattr(au, "audio_storage", audio_storage)
        _roles(monkeypatch, {"Дгарнин": 40})
        item = _take(db, chapter, role="Дгарнин", actor="Сергей Зотов")
        item.location = "nas"
        db.flush()
        if nas_state is not None:
            nas_health.record_probe(nas_state)
        touched = []
        monkeypatch.setattr("app.services.chapter_delivery.os.path.isfile", lambda p: touched.append(p) or True)

        target = tmp_path / "chapter.zip"
        with pytest.raises(StorageUnavailable):
            build_chapter_archive(db, chapter.id, str(target))

        assert touched == [], "до NAS не должно было дойти ни одно обращение"
        assert not target.exists(), "полуготовый архив не должен оставаться на диске"
        assert chapter.delivered_at is None, "несобранная глава не считается сданной"

    def test_a_chapter_that_does_not_exist(self, studio, tmp_path):
        from app.services.chapter_delivery import build_chapter_archive

        db, _, _ = studio

        assert build_chapter_archive(db, "no-such-chapter", str(tmp_path / "x.zip")) is None


class TestTheWholeBook:
    """Шестьдесят глав — один проход по атрибуциям, а не шестьдесят запросов подряд."""

    def test_it_counts_each_chapter(self, studio, monkeypatch):
        from app.services.chapter_delivery import book_recording_status

        db, book, chapter = studio
        second = ScriptChapter(book_id=book.id, chapter_index=5, chapter_title="Глава 5. Ещё", status="published")
        db.add(second)
        db.flush()
        monkeypatch.setattr(
            "app.services.chapter_delivery.book_role_counts_by_chapter",
            lambda db, book_id: {chapter.id: {"Дгарнин": 40}, second.id: {"Сатухух": 12}},
        )
        _take(db, chapter, role="Дгарнин", actor="Сергей Зотов")
        db.flush()

        status = book_recording_status(db, book.id)

        assert [row["chapter_index"] for row in status["chapters"]] == [4, 5]
        assert status["chapters"][0]["ready"] is True
        assert status["chapters"][1]["ready"] is False
        assert status["ready_chapters"] == 1 and status["total_chapters"] == 2

    def test_a_book_that_does_not_exist(self, studio):
        from app.services.chapter_delivery import book_recording_status

        db, _, _ = studio

        assert book_recording_status(db, "no-such-book") is None

    def test_the_chapter_row_carries_its_integrity(self, studio, monkeypatch):
        """«Файл есть» и «файл цел» — разные вещи, и на экране это должно быть видно."""
        from app.services.chapter_delivery import book_recording_status

        db, book, chapter = studio
        monkeypatch.setattr(
            "app.services.chapter_delivery.book_role_counts_by_chapter",
            lambda db, book_id: {chapter.id: {"Дгарнин": 5}},
        )
        ok_item = _take(db, chapter, role="Дгарнин", actor="Сергей Зотов")
        bad_item = _take(db, chapter, role="Дгарнин", actor="Сергей Зотов")
        ok_item.verify_state = "ok"
        bad_item.verify_state = "mismatch"
        db.commit()

        row = book_recording_status(db, book.id)["chapters"][0]
        assert row["integrity_ok"] == 1
        assert row["integrity_bad"] == 1
        assert row["integrity_checked"] is True

    def test_files_from_before_the_migration_are_counted_as_unverifiable(self, studio, monkeypatch):
        """У дублей, принятых до миграции 0020, суммы приёма нет — сверять не с чем.
        Молча считать их непроверенными значило рисовать кнопку «сверить» вечно: она
        запускала пустую сверку за миллисекунды и ничего не меняла."""
        from app.services.chapter_delivery import book_recording_status

        db, book, chapter = studio
        monkeypatch.setattr(
            "app.services.chapter_delivery.book_role_counts_by_chapter",
            lambda db, book_id: {chapter.id: {"Дгарнин": 5}},
        )
        old_one = _take(db, chapter, role="Дгарнин", actor="Сергей Зотов")
        old_two = _take(db, chapter, role="Дгарнин", actor="Сергей Зотов")
        old_one.md5 = ""
        old_two.md5 = ""
        db.commit()

        row = book_recording_status(db, book.id)["chapters"][0]
        assert row["integrity_skipped"] == 2
        assert row["integrity_files"] == 2
        assert row["integrity_checked"] is False

    def test_one_file_with_a_receipt_sum_is_still_worth_checking(self, studio, monkeypatch):
        """Смешанная глава — не «сверять не с чем»: дозаписанный дубль сумму приёма
        имеет, и кнопка обязана остаться."""
        from app.services.chapter_delivery import book_recording_status

        db, book, chapter = studio
        monkeypatch.setattr(
            "app.services.chapter_delivery.book_role_counts_by_chapter",
            lambda db, book_id: {chapter.id: {"Дгарнин": 5}},
        )
        old = _take(db, chapter, role="Дгарнин", actor="Сергей Зотов")
        _take(db, chapter, role="Дгарнин", actor="Сергей Зотов")
        old.md5 = ""
        db.commit()

        row = book_recording_status(db, book.id)["chapters"][0]
        assert row["integrity_skipped"] == 1
        assert row["integrity_files"] == 2

    def test_the_chapter_row_shows_how_many_copies_reached_the_nas(self, studio, monkeypatch):
        """Окно одной копии должно быть видно: файл, не уехавший сутки, значит
        что домашний канал владельца лежит давно, а не пять минут."""
        from datetime import timedelta

        from app.services.chapter_delivery import book_recording_status

        db, book, chapter = studio
        monkeypatch.setattr(
            "app.services.chapter_delivery.book_role_counts_by_chapter",
            lambda db, book_id: {chapter.id: {"Дгарнин": 5}},
        )
        _take(db, chapter, role="Дгарнин", actor="Сергей Зотов", mirrored_at=utcnow_naive())
        _take(db, chapter, role="Дгарнин", actor="Сергей Зотов")
        old = _take(db, chapter, role="Дгарнин", actor="Сергей Зотов")
        old.uploaded_at = utcnow_naive() - timedelta(days=2)
        db.commit()

        row = book_recording_status(db, book.id)["chapters"][0]
        assert row["mirrored_files"] == 1
        assert row["unmirrored_files"] == 2
        assert row["stale_unmirrored"] == 1
        # Протухло по возрасту, а не по расхождению: копия ещё может приехать.
        assert row["mirror_broken"] == 0
        assert row["session_archived"] is False

    def test_one_hopeless_file_keeps_the_whole_chapter_out_of_the_safe_state(self, studio, monkeypatch):
        """Самая дорогая ошибка этого экрана — зелёная галочка над главой, у которой
        одна копия не сойдётся уже никогда. Владелец прочтёт её как «можно чистить
        сервер», и запись останется в одном экземпляре.

        Числа считаются независимо, поэтому сегодня это верно по построению. Тест
        закрепляет само свойство: пока в главе есть безнадёжный файл, строка не
        может выглядеть полностью скопированной."""
        from app.services.audio_mirror import MISMATCH
        from app.services.chapter_delivery import book_recording_status

        db, book, chapter = studio
        monkeypatch.setattr(
            "app.services.chapter_delivery.book_role_counts_by_chapter",
            lambda db, book_id: {chapter.id: {"Дгарнин": 5}},
        )
        good = _take(db, chapter, role="Дгарнин", actor="Сергей Зотов")
        good.mirrored_at = utcnow_naive()
        good.mirror_state = "ok"
        broken = _take(db, chapter, role="Дгарнин", actor="Роман Сомов")
        broken.mirror_state = MISMATCH
        db.commit()

        row = book_recording_status(db, book.id)["chapters"][0]
        assert row["mirrored_files"] == 1
        assert row["mirror_broken"] == 1
        # Условие «всё скопировано» на экране — это ноль незеркалированных.
        # Безнадёжный файл обязан держать его ненулевым.
        assert row["unmirrored_files"] == 1

    def test_a_fresh_mismatch_is_stale_immediately(self, studio, monkeypatch):
        """Расхождение суммы — приговор без срока давности: `mirror_once` исключает
        такой файл из кругов навсегда, и `mirrored_at` у него не появится никогда.
        Ждать сутки, чтобы показать предупреждение, здесь бессмысленно — оно обязано
        загореться в ту же секунду, что и mismatch, а не через день ожидания."""
        from app.services.audio_mirror import MISMATCH
        from app.services.chapter_delivery import book_recording_status

        db, book, chapter = studio
        monkeypatch.setattr(
            "app.services.chapter_delivery.book_role_counts_by_chapter",
            lambda db, book_id: {chapter.id: {"Дгарнин": 5}},
        )
        broken = _take(db, chapter, role="Дгарнин", actor="Сергей Зотов")
        broken.mirror_state = MISMATCH
        db.commit()

        row = book_recording_status(db, book.id)["chapters"][0]
        assert row["unmirrored_files"] == 1
        assert row["stale_unmirrored"] == 1
        # И отдельно от возраста: экран обязан сказать «не сойдётся само», а не
        # «подождите» — действия человека в этих двух случаях разные.
        assert row["mirror_broken"] == 1


class TestWhatAsrAdds:
    """Второй счёт рядом с первым: «роль записана» и «роль записана целиком» — разное."""

    def _job(self, db, audio_id, *, missing, total=10):
        """Доля выводится из самой разметки, а не задаётся отдельно: два числа об одном
        и том же расходятся ровно тогда, когда на них смотрят."""
        import json

        from app.models import AsrJob

        db.add(AsrJob(audio_file_id=audio_id, status="done", coverage=(total - len(missing)) / total,
                      alignment_json=json.dumps({"missing": missing, "total": total, "lines": []})))
        db.flush()

    def test_a_role_without_recognition_says_so_rather_than_zero(self, studio, monkeypatch):
        db, _, chapter = studio
        _roles(monkeypatch, {"Дгарнин": 40})
        _take(db, chapter, role="Дгарнин", actor="Сергей Зотов")
        db.flush()

        row = chapter_recording_status(db, chapter.id)["roles"][0]

        assert row["asr_coverage"] is None, "ноль означал бы «ничего не произнесено», а мы просто не считали"
        assert row["missing_lines"] == 0

    def test_recognition_fills_in_the_second_count(self, studio, monkeypatch):
        db, _, chapter = studio
        _roles(monkeypatch, {"Дгарнин": 40})
        item = _take(db, chapter, role="Дгарнин", actor="Сергей Зотов")
        db.flush()
        self._job(db, item.id, missing=[3, 7])

        row = chapter_recording_status(db, chapter.id)["roles"][0]

        assert row["asr_coverage"] == pytest.approx(0.8)
        assert row["missing_lines"] == 2

    def test_the_best_of_several_takes_counts(self, studio, monkeypatch):
        """Актёр перезаписал роль: считается лучший дубль, а не последний."""
        db, _, chapter = studio
        _roles(monkeypatch, {"Дгарнин": 40})
        first = _take(db, chapter, role="Дгарнин", actor="Сергей Зотов")
        second = _take(db, chapter, role="Дгарнин", actor="Сергей Зотов")
        db.flush()
        self._job(db, first.id, missing=[1, 2, 3])
        self._job(db, second.id, missing=[9])

        row = chapter_recording_status(db, chapter.id)["roles"][0]

        assert row["asr_coverage"] == pytest.approx(0.9)
        assert row["missing_lines"] == 1


class TestTheTracksTheEditorWouldAddByHand:
    """Монтажёр каждый раз заводит руками две дорожки под саундтрек и две под
    интершумы. Сессия заводит их пустыми — это ничего не стоит и снимает кусок
    настройки перед работой."""

    def test_the_session_brings_music_and_ambience_tracks(self, studio, monkeypatch):
        import xml.etree.ElementTree as ET

        from app.services.chapter_delivery import build_chapter_session

        db, _, chapter = studio
        _roles(monkeypatch, {"Гамук": 1})
        monkeypatch.setattr("app.services.chapter_delivery.chapter_replicas", lambda db, chapter_id: [])

        root = ET.fromstring(build_chapter_session(db, chapter.id).lstrip("﻿"))
        names = [t.find("trackParameters/name").text for t in root.findall("session/tracks/audioTrack")]

        assert names[-4:] == ["Саундтрек 1", "Саундтрек 2", "Интершум 1", "Интершум 2"]

    def test_those_tracks_come_empty(self, studio, monkeypatch):
        import xml.etree.ElementTree as ET

        from app.services.chapter_delivery import build_chapter_session

        db, _, chapter = studio
        _roles(monkeypatch, {"Гамук": 1})
        monkeypatch.setattr("app.services.chapter_delivery.chapter_replicas", lambda db, chapter_id: [])

        root = ET.fromstring(build_chapter_session(db, chapter.id).lstrip("﻿"))
        extra = root.findall("session/tracks/audioTrack")[-4:]

        assert all(track.findall("audioClip") == [] for track in extra)


class TestTheAuditionSession:
    """Сессия строится из того, что известно: с распознаванием — по репликам, без него —
    целыми дублями. Второе полезно уже сегодня: файлы главы разложены по трекам с
    именами ролей, и остаётся только резать, а не искать и импортировать."""

    def _session(self, db, chapter):
        import xml.etree.ElementTree as ET

        from app.services.chapter_delivery import build_chapter_session

        xml = build_chapter_session(db, chapter.id)
        return xml, ET.fromstring(xml.lstrip("﻿"))

    def test_without_recognition_each_role_is_one_whole_clip(self, studio, monkeypatch):
        from app.services.chapter_delivery import EXTRA_TRACKS

        db, _, chapter = studio
        _roles(monkeypatch, {"Дгарнин": 40, "Сатухух": 12})
        _take(db, chapter, role="Дгарнин", actor="Сергей Зотов")
        _take(db, chapter, role="Сатухух", actor="Роман Сомов")
        db.flush()

        _xml, root = self._session(db, chapter)
        names = [node.text for node in root.findall("session/tracks/audioTrack/trackParameters/name")]
        clips = root.findall("session/tracks/audioTrack/audioClip")

        assert names == ["Дгарнин", "Сатухух", *EXTRA_TRACKS]
        assert len(clips) == 2, "по одному целому дублю на роль"

    def test_a_role_nobody_recorded_gets_an_empty_track(self, studio, monkeypatch):
        """Пустой трек — это видимая дыра: инженер сразу знает, кого ждать."""
        from app.services.chapter_delivery import EXTRA_TRACKS

        db, _, chapter = studio
        _roles(monkeypatch, {"Дгарнин": 40, "Сатухух": 12})
        _take(db, chapter, role="Дгарнин", actor="Сергей Зотов")
        db.flush()

        _xml, root = self._session(db, chapter)
        tracks = root.findall("session/tracks/audioTrack")

        # Дорожки ролей идут первыми, пустые служебные — следом.
        assert len(tracks) == 2 + len(EXTRA_TRACKS)
        assert len(tracks[1].findall("audioClip")) == 0

    def test_with_recognition_the_clips_are_the_replicas(self, studio, monkeypatch):
        import json

        from app.models import AsrJob

        db, _, chapter = studio
        _roles(monkeypatch, {"Дгарнин": 40})
        monkeypatch.setattr("app.services.chapter_delivery.chapter_replicas", lambda db, chapter_id: [
            {"order": 0, "role": "Дгарнин", "role_index": 0, "text": "Я не пойду туда"},
            {"order": 1, "role": "Дгарнин", "role_index": 1, "text": "Там холодно"},
        ])
        item = _take(db, chapter, role="Дгарнин", actor="Сергей Зотов")
        db.flush()
        db.add(AsrJob(audio_file_id=item.id, status="done", coverage=1.0, alignment_json=json.dumps({
            "missing": [], "total": 2,
            "lines": [
                {"index": 0, "text": "Я не пойду туда", "matched": True, "start": 1.0, "end": 3.0, "score": 1.0},
                {"index": 1, "text": "Там холодно", "matched": True, "start": 5.0, "end": 6.5, "score": 1.0},
            ],
        })))
        db.flush()

        _xml, root = self._session(db, chapter)
        clips = root.findall("session/tracks/audioTrack/audioClip")

        assert len(clips) == 2
        assert [clip.get("name") for clip in clips] == ["Я не пойду туда", "Там холодно"]

    def test_a_missing_replica_leaves_a_hole_rather_than_a_wrong_clip(self, studio, monkeypatch):
        import json

        from app.models import AsrJob

        db, _, chapter = studio
        _roles(monkeypatch, {"Дгарнин": 40})
        monkeypatch.setattr("app.services.chapter_delivery.chapter_replicas", lambda db, chapter_id: [
            {"order": 0, "role": "Дгарнин", "role_index": 0, "text": "есть"},
            {"order": 1, "role": "Дгарнин", "role_index": 1, "text": "нет"},
        ])
        item = _take(db, chapter, role="Дгарнин", actor="Сергей Зотов")
        db.flush()
        db.add(AsrJob(audio_file_id=item.id, status="done", coverage=0.5, alignment_json=json.dumps({
            "missing": [1], "total": 2,
            "lines": [
                {"index": 0, "text": "есть", "matched": True, "start": 1.0, "end": 2.0, "score": 1.0},
                {"index": 1, "text": "нет", "matched": False, "start": None, "end": None, "score": 0.1},
            ],
        })))
        db.flush()

        _xml, root = self._session(db, chapter)

        assert len(root.findall("session/tracks/audioTrack/audioClip")) == 1

    def test_a_chapter_that_does_not_exist(self, studio):
        from app.services.chapter_delivery import build_chapter_session

        db, _, _ = studio

        assert build_chapter_session(db, "нет-такой-главы") is None


class TestTheSessionInsideTheArchive:
    def test_the_archive_carries_a_session_that_finds_its_audio(self, studio, monkeypatch, tmp_path):
        """Скачал, распаковал, открыл сессию — файлы на месте, без единой настройки путей."""
        import xml.etree.ElementTree as ET
        import zipfile

        from app.services import audio_storage
        from app.services.chapter_delivery import build_chapter_archive

        db, _, chapter = studio
        monkeypatch.setattr(audio_storage, "nas_root", lambda: str(tmp_path))
        # Своё локальное хранилище: общий `/tmp/noname_test_audio` копит файлы прошлых
        # прогонов, а приём не затирает занятый ключ и дал бы файлу номер `_2`, `_3`…
        monkeypatch.setattr(audio_storage.settings, "audio_storage_path", str(tmp_path / "local"))
        monkeypatch.setattr(au, "audio_storage", audio_storage)
        _roles(monkeypatch, {"Дгарнин": 40})
        _take(db, chapter, role="Дгарнин", actor="Сергей Зотов")
        db.flush()

        target = tmp_path / "chapter.zip"
        build_chapter_archive(db, chapter.id, str(target))

        with zipfile.ZipFile(target) as archive:
            names = archive.namelist()
            assert "сессия.sesx" in names
            root = ET.fromstring(archive.read("сессия.sesx").decode("utf-8").lstrip("﻿"))

        relative = root.find("files/file").get("relativePath")
        assert relative == "KP_Ch04_Dgarnin_SergeyZotov.wav"
        assert relative in names, "сессия зовёт файл ровно тем именем, под которым он лежит рядом"


class TestTheSessionReadsTopToBottom:
    """Клипы стоят в порядке главы, а не в порядке файла своего актёра.

    Каждый диктор писал отдельно и начинал с нуля, поэтому позиции внутри его файла ни
    с чем не согласованы: два трека, разложенные так, звучали бы одновременно. Порядок
    знает сценарий — и по нему глава открывается собранным черновиком.
    """

    def _alignment(self, *lines):
        import json

        return json.dumps({"missing": [], "total": len(lines), "lines": [
            {"index": i, "text": t, "matched": True, "start": s, "end": e, "score": 1.0}
            for i, (t, s, e) in enumerate(lines)
        ]})

    def _session(self, db, chapter):
        import xml.etree.ElementTree as ET

        from app.services.chapter_delivery import build_chapter_session

        return ET.fromstring(build_chapter_session(db, chapter.id).lstrip("﻿"))

    def _clips(self, root):
        out = []
        for track in root.findall("session/tracks/audioTrack"):
            name = track.find("trackParameters/name").text
            for clip in track.findall("audioClip"):
                out.append((int(clip.get("startPoint")), name, clip.get("name")))
        return [(role, text) for _start, role, text in sorted(out)]

    def test_two_actors_alternate_the_way_the_chapter_does(self, studio, monkeypatch):
        from app.models import AsrJob

        db, _, chapter = studio
        _roles(monkeypatch, {"Дгарнин": 2, "Сатухух": 1})
        monkeypatch.setattr("app.services.chapter_delivery.chapter_replicas", lambda db, chapter_id: [
            {"order": 0, "role": "Дгарнин", "role_index": 0, "text": "первая"},
            {"order": 1, "role": "Сатухух", "role_index": 0, "text": "вторая"},
            {"order": 2, "role": "Дгарнин", "role_index": 1, "text": "третья"},
        ])
        one = _take(db, chapter, role="Дгарнин", actor="Зотов")
        two = _take(db, chapter, role="Сатухух", actor="Сомов")
        db.flush()
        # У каждого своя запись, и каждый в ней начинает с нуля.
        db.add(AsrJob(audio_file_id=one.id, status="done", coverage=1.0,
                      alignment_json=self._alignment(("первая", 0.0, 2.0), ("третья", 4.0, 6.0))))
        db.add(AsrJob(audio_file_id=two.id, status="done", coverage=1.0,
                      alignment_json=self._alignment(("вторая", 0.0, 1.5))))
        db.flush()

        assert self._clips(self._session(db, chapter)) == [
            ("Дгарнин", "первая"), ("Сатухух", "вторая"), ("Дгарнин", "третья"),
        ]

    def test_nothing_overlaps(self, studio, monkeypatch):
        from app.models import AsrJob

        db, _, chapter = studio
        _roles(monkeypatch, {"Дгарнин": 1, "Сатухух": 1})
        monkeypatch.setattr("app.services.chapter_delivery.chapter_replicas", lambda db, chapter_id: [
            {"order": 0, "role": "Дгарнин", "role_index": 0, "text": "первая"},
            {"order": 1, "role": "Сатухух", "role_index": 0, "text": "вторая"},
        ])
        one = _take(db, chapter, role="Дгарнин", actor="Зотов")
        two = _take(db, chapter, role="Сатухух", actor="Сомов")
        db.flush()
        db.add(AsrJob(audio_file_id=one.id, status="done", coverage=1.0, alignment_json=self._alignment(("первая", 0.0, 3.0))))
        db.add(AsrJob(audio_file_id=two.id, status="done", coverage=1.0, alignment_json=self._alignment(("вторая", 0.0, 2.0))))
        db.flush()

        root = self._session(db, chapter)
        spans = sorted(
            (int(c.get("startPoint")), int(c.get("endPoint")))
            for c in root.findall("session/tracks/audioTrack/audioClip")
        )

        assert spans[0][1] <= spans[1][0], "второй клип начинается не раньше, чем кончился первый"

    def test_the_clip_still_cuts_the_right_piece_of_the_source(self, studio, monkeypatch):
        """На таймлайне место новое, а вырезается по-прежнему то, что было сказано."""
        from app.models import AsrJob

        db, _, chapter = studio
        _roles(monkeypatch, {"Дгарнин": 1})
        monkeypatch.setattr("app.services.chapter_delivery.chapter_replicas", lambda db, chapter_id: [
            {"order": 0, "role": "Дгарнин", "role_index": 0, "text": "реплика"},
        ])
        item = _take(db, chapter, role="Дгарнин", actor="Зотов")
        db.flush()
        db.add(AsrJob(audio_file_id=item.id, status="done", coverage=1.0, alignment_json=self._alignment(("реплика", 42.0, 45.0))))
        db.flush()

        clip = self._session(db, chapter).find("session/tracks/audioTrack/audioClip")

        assert clip.get("sourceInPoint") == str(int(42.0 * 44100))
        assert clip.get("sourceOutPoint") == str(int(45.0 * 44100))
        assert clip.get("startPoint") == "0", "первая реплика главы стоит в начале"

    def test_a_missing_replica_leaves_room_where_it_belongs(self, studio, monkeypatch):
        """Дыра на своём месте: монтажёр видит, чего не хватает и куда это встанет."""
        from app.models import AsrJob

        db, _, chapter = studio
        _roles(monkeypatch, {"Дгарнин": 2})
        monkeypatch.setattr("app.services.chapter_delivery.chapter_replicas", lambda db, chapter_id: [
            {"order": 0, "role": "Дгарнин", "role_index": 0, "text": "есть"},
            {"order": 1, "role": "Дгарнин", "role_index": 1, "text": "не записана"},
            {"order": 2, "role": "Дгарнин", "role_index": 2, "text": "тоже есть"},
        ])
        import json

        item = _take(db, chapter, role="Дгарнин", actor="Зотов")
        db.flush()
        db.add(AsrJob(audio_file_id=item.id, status="done", coverage=0.67, alignment_json=json.dumps({
            "missing": [1], "total": 3, "lines": [
                {"index": 0, "text": "есть", "matched": True, "start": 0.0, "end": 2.0, "score": 1.0},
                {"index": 1, "text": "не записана", "matched": False, "start": None, "end": None, "score": 0.1},
                {"index": 2, "text": "тоже есть", "matched": True, "start": 5.0, "end": 7.0, "score": 1.0},
            ],
        })))
        db.flush()

        root = self._session(db, chapter)
        clips = sorted(
            (int(c.get("startPoint")), int(c.get("endPoint")), c.get("name"))
            for c in root.findall("session/tracks/audioTrack/audioClip")
        )

        assert [name for _s, _e, name in clips] == ["есть", "тоже есть"]
        assert clips[1][0] > clips[0][1], "между ними осталось место под пропущенную"


class TestWhenTheRoleWasReadSeveralTimes:
    """Все подходы попадают на дорожку роли, а глава от этого не разъезжается.

    Монтажёр выбирает подход сам — значит видеть он должен все. Но следующая реплика
    обязана стоять на своём месте независимо от его выбора, иначе выбор превращается
    в перекладывание всей главы.
    """

    def _alignment(self, *lines):
        import json

        return json.dumps({"missing": [], "total": len(lines), "lines": [
            {"index": i, "text": t, "matched": True, "start": takes[0][0], "end": takes[0][1],
             "score": 1.0,
             "takes": [{"start": s, "end": e, "score": 1.0} for s, e in takes]}
            for i, (t, takes) in enumerate(lines)
        ]})

    def _clips(self, db, chapter):
        import xml.etree.ElementTree as ET

        from app.services.chapter_delivery import build_chapter_session

        root = ET.fromstring(build_chapter_session(db, chapter.id).lstrip("﻿"))
        out = []
        for track in root.findall("session/tracks/audioTrack"):
            name = track.find("trackParameters/name").text
            for clip in track.findall("audioClip"):
                out.append((int(clip.get("startPoint")), name, clip.get("name"),
                            int(clip.get("sourceInPoint")), int(clip.get("sourceOutPoint"))))
        return sorted(out)

    def _one_role_chapter(self, db, chapter, monkeypatch, takes):
        from app.models import AsrJob

        _roles(monkeypatch, {"Гамук": 1, "Дгарнин": 1})
        monkeypatch.setattr("app.services.chapter_delivery.chapter_replicas", lambda db, chapter_id: [
            {"order": 0, "role": "Гамук", "role_index": 0, "text": "первая"},
            {"order": 1, "role": "Дгарнин", "role_index": 0, "text": "вторая"},
        ])
        one = _take(db, chapter, role="Гамук", actor="Гончаров")
        two = _take(db, chapter, role="Дгарнин", actor="Зотов")
        db.flush()
        db.add(AsrJob(audio_file_id=one.id, status="done", coverage=1.0,
                      alignment_json=self._alignment(("первая", takes))))
        db.add(AsrJob(audio_file_id=two.id, status="done", coverage=1.0,
                      alignment_json=self._alignment(("вторая", [(0.0, 1.0)]))))
        db.flush()

    def test_every_take_lands_on_the_timeline(self, studio, monkeypatch):
        db, _, chapter = studio
        self._one_role_chapter(db, chapter, monkeypatch, [(0.0, 2.0), (3.0, 5.5), (6.0, 7.0)])

        gamuk = [c for c in self._clips(db, chapter) if c[1] == "Гамук"]
        assert len(gamuk) == 3

    def test_takes_sit_end_to_end_starting_at_the_replica(self, studio, monkeypatch):
        db, _, chapter = studio
        self._one_role_chapter(db, chapter, monkeypatch, [(0.0, 2.0), (3.0, 5.5)])

        gamuk = [c for c in self._clips(db, chapter) if c[1] == "Гамук"]
        first, second = gamuk
        assert first[0] == 0
        assert second[0] == pytest.approx(first[0] + 2.0 * 44100, abs=2)

    def test_the_next_role_waits_for_the_longest_take_not_their_sum(self, studio, monkeypatch):
        """Зазор по сумме оставил бы тишину во весь хвост неиспользованных подходов;
        по первому — пустил бы длинный подход на следующую реплику."""
        db, _, chapter = studio
        self._one_role_chapter(db, chapter, monkeypatch, [(0.0, 2.0), (3.0, 5.5), (6.0, 7.0)])

        dgarnin = [c for c in self._clips(db, chapter) if c[1] == "Дгарнин"][0]
        longest = 2.5  # (3.0, 5.5)
        assert dgarnin[0] == pytest.approx((longest + 0.35) * 44100, abs=2)

    def test_a_single_take_is_placed_exactly_as_before(self, studio, monkeypatch):
        db, _, chapter = studio
        self._one_role_chapter(db, chapter, monkeypatch, [(0.0, 2.0)])

        gamuk = [c for c in self._clips(db, chapter) if c[1] == "Гамук"]
        assert len(gamuk) == 1 and gamuk[0][0] == 0

    def test_the_clip_name_says_which_take_it_is(self, studio, monkeypatch):
        db, _, chapter = studio
        self._one_role_chapter(db, chapter, monkeypatch, [(0.0, 2.0), (3.0, 5.5)])

        names = [c[2] for c in self._clips(db, chapter) if c[1] == "Гамук"]
        assert names == ["первая · дубль 1/2", "первая · дубль 2/2"]

    def test_a_lone_take_is_not_numbered(self, studio, monkeypatch):
        """«дубль 1/1» — шум: нумеровать нечего."""
        db, _, chapter = studio
        self._one_role_chapter(db, chapter, monkeypatch, [(0.0, 2.0)])

        names = [c[2] for c in self._clips(db, chapter) if c[1] == "Гамук"]
        assert names == ["первая"]

    def test_two_replicas_of_the_same_role_in_a_row_do_not_overlap(self, studio, monkeypatch):
        """Все существующие тесты раскладки берут две РАЗНЫЕ роли подряд — там общих
        часов достаточно, потому что слот следующей реплики начинается там, где его
        оставила предыдущая роль. Но рассказчик (или персонаж, чья реплика разбита на
        части сценарием) нередко говорит два раза подряд. Общие часы двигаются на
        САМЫЙ ДЛИННЫЙ подход реплики, а её же подходы на ЕЁ ЖЕ дорожке лежат встык и
        занимают дорожку на их СУММУ — и если сумма больше самого длинного (она почти
        всегда больше, когда подходов больше одного), второй клип по одним только
        общим часам лёг бы поверх хвоста первой реплики на этой же дорожке.
        """
        from app.models import AsrJob

        db, _, chapter = studio
        _roles(monkeypatch, {"Гамук": 1})
        monkeypatch.setattr("app.services.chapter_delivery.chapter_replicas", lambda db, chapter_id: [
            {"order": 0, "role": "Гамук", "role_index": 0, "text": "первая"},
            {"order": 1, "role": "Гамук", "role_index": 1, "text": "вторая"},
        ])
        item = _take(db, chapter, role="Гамук", actor="Гончаров")
        db.flush()
        db.add(AsrJob(audio_file_id=item.id, status="done", coverage=1.0,
                      alignment_json=self._alignment(
                          ("первая", [(0.0, 2.0), (3.0, 5.5), (6.0, 7.0)]),
                          ("вторая", [(10.0, 11.0)]),
                      )))
        db.flush()

        gamuk = [c for c in self._clips(db, chapter) if c[1] == "Гамук"]
        assert len(gamuk) == 4, "три подхода первой реплики плюс один второй"
        # `_clips` сортирует по startPoint через все клипы дорожки разом, а не
        # реплика за репликой — при нахлёсте клип второй реплики окажется
        # где-то ПОСЕРЕДИНЕ подходов первой, а не в конце списка. Поэтому берём
        # клипы по имени, а не по позиции в отсортированном списке.
        # (startPoint, role, name, sourceInPoint, sourceOutPoint); конец клипа на
        # таймлайне — startPoint + длина вырезанного куска.
        first_replica_clips = [c for c in gamuk if c[2].startswith("первая")]
        second_replica_clip = next(c for c in gamuk if c[2] == "вторая")
        assert len(first_replica_clips) == 3
        ends_of_first_replica = [start + (out - in_) for start, _r, _n, in_, out in first_replica_clips]
        assert second_replica_clip[0] >= max(ends_of_first_replica), (
            "первый клип второй реплики начинается не раньше конца последнего клипа первой"
        )


class TestSessionsBuiltBeforeTakesExisted:
    """Главы, распознанные до появления подходов, хранят одно вхождение и ключа `takes`
    не имеют вовсе. Перераспознавать их ради сессии — минута машинного времени на файл,
    поэтому сборка обязана понимать старую запись."""

    def test_an_old_alignment_without_takes_still_assembles(self, studio, monkeypatch):
        import json
        import xml.etree.ElementTree as ET

        from app.models import AsrJob
        from app.services.chapter_delivery import build_chapter_session

        db, _, chapter = studio
        _roles(monkeypatch, {"Гамук": 1})
        monkeypatch.setattr("app.services.chapter_delivery.chapter_replicas", lambda db, chapter_id: [
            {"order": 0, "role": "Гамук", "role_index": 0, "text": "первая"},
        ])
        item = _take(db, chapter, role="Гамук", actor="Гончаров")
        db.flush()
        db.add(AsrJob(audio_file_id=item.id, status="done", coverage=1.0, alignment_json=json.dumps({
            "missing": [], "total": 1,
            "lines": [{"index": 0, "text": "первая", "matched": True,
                       "start": 0.0, "end": 2.0, "score": 1.0}],
        })))
        db.flush()

        root = ET.fromstring(build_chapter_session(db, chapter.id).lstrip("﻿"))
        clips = root.findall("session/tracks/audioTrack/audioClip")

        assert len(clips) == 1
        assert clips[0].get("name") == "первая"

    def test_a_line_whose_every_take_lost_its_timestamps_falls_back_too(self, studio, monkeypatch):
        """`takes` непуст, но ни у одного подхода нет `start`/`end` — тот же откат на
        `start`/`end` строки, что и при отсутствии ключа `takes` вовсе, а не молчаливая
        дыра там, где должна быть реплика."""
        import json
        import xml.etree.ElementTree as ET

        from app.models import AsrJob
        from app.services.chapter_delivery import build_chapter_session

        db, _, chapter = studio
        _roles(monkeypatch, {"Гамук": 1})
        monkeypatch.setattr("app.services.chapter_delivery.chapter_replicas", lambda db, chapter_id: [
            {"order": 0, "role": "Гамук", "role_index": 0, "text": "первая"},
        ])
        item = _take(db, chapter, role="Гамук", actor="Гончаров")
        db.flush()
        db.add(AsrJob(audio_file_id=item.id, status="done", coverage=1.0, alignment_json=json.dumps({
            "missing": [], "total": 1,
            "lines": [{"index": 0, "text": "первая", "matched": True,
                       "start": 0.0, "end": 2.0, "score": 1.0,
                       "takes": [{"start": None, "end": None, "score": 1.0}]}],
        })))
        db.flush()

        root = ET.fromstring(build_chapter_session(db, chapter.id).lstrip("﻿"))
        clips = root.findall("session/tracks/audioTrack/audioClip")

        assert len(clips) == 1, "реплика не должна молча стать дырой"
        assert clips[0].get("sourceInPoint") == str(int(0.0 * 44100))
        assert clips[0].get("sourceOutPoint") == str(int(2.0 * 44100))


class TestHandingTheChapterOver:
    def test_taking_the_archive_marks_the_chapter_delivered(self, studio, monkeypatch, tmp_path):
        from app.services import audio_storage
        from app.services.chapter_delivery import book_recording_status, build_chapter_archive

        db, book, chapter = studio
        monkeypatch.setattr(audio_storage, "nas_root", lambda: str(tmp_path))
        monkeypatch.setattr(au, "audio_storage", audio_storage)
        monkeypatch.setattr("app.services.chapter_delivery.book_role_counts_by_chapter",
                            lambda db, book_id: {chapter.id: {"Дгарнин": 40}})
        _roles(monkeypatch, {"Дгарнин": 40})
        _take(db, chapter, role="Дгарнин", actor="Сергей Зотов")
        db.flush()

        assert book_recording_status(db, book.id)["delivered_chapters"] == 0

        build_chapter_archive(db, chapter.id, str(tmp_path / "c.zip"))
        db.flush()

        assert chapter.delivered_at is not None
        assert book_recording_status(db, book.id)["delivered_chapters"] == 1

    def test_a_half_recorded_chapter_downloads_but_is_not_delivered(self, studio, monkeypatch, tmp_path):
        """Архив недописанной главы берут посмотреть — это ещё не сдача в сведение."""
        from app.services.chapter_delivery import build_chapter_archive

        db, _, chapter = studio
        _roles(monkeypatch, {"Дгарнин": 40, "Сатухух": 12})
        _take(db, chapter, role="Дгарнин", actor="Сергей Зотов")
        db.flush()

        result = build_chapter_archive(db, chapter.id, str(tmp_path / "c.zip"))

        assert result["files_written"] == 1
        assert (tmp_path / "c.zip").exists()
        assert result["delivered"] is False
        assert chapter.delivered_at is None

    def test_a_file_missing_on_disk_keeps_the_chapter_undelivered(self, studio, monkeypatch, tmp_path):
        """Все роли записаны, но дубль пропал с диска: архив неполон — и не сдан."""
        from app.services.chapter_delivery import build_chapter_archive

        db, _, chapter = studio
        _roles(monkeypatch, {"Дгарнин": 40})
        _take(db, chapter, role="Дгарнин", actor="Сергей Зотов")
        db.flush()
        monkeypatch.setattr("app.services.chapter_delivery.os.path.isfile", lambda path: False)

        result = build_chapter_archive(db, chapter.id, str(tmp_path / "c.zip"))

        assert result["files_missing"]
        assert result["delivered"] is False
        assert chapter.delivered_at is None


class TestCoverageCountsTheWholeChapter:
    """Знаменатель — все реплики главы, а не реплики проверенных файлов.

    Глава 15 показывала 100% при одной записанной роли из двадцати шести: единственный
    файл был прочитан целиком, и среднее по проверенным файлам вышло единицей. На деле
    из 355 реплик главы записаны были 10 — два процента. У Рассказчика одного 75.

    Такое число не просто неверно: оно говорит «глава готова» ровно тогда, когда её
    почти не начинали.
    """

    def _job(self, db, audio_id, *, matched, total):
        import json

        from app.models import AsrJob

        lines = [{"index": i, "text": f"реплика {i}", "matched": i < matched,
                  "start": float(i), "end": float(i) + 0.5, "score": 1.0} for i in range(total)]
        db.add(AsrJob(audio_file_id=audio_id, status="done", coverage=matched / total,
                      alignment_json=json.dumps({"missing": [i for i in range(matched, total)],
                                                 "total": total, "lines": lines})))
        db.flush()

    def test_one_role_read_whole_is_not_a_whole_chapter(self, studio, monkeypatch):
        from app.services.chapter_delivery import book_recording_status

        db, book, chapter = studio
        monkeypatch.setattr("app.services.chapter_delivery.book_role_counts_by_chapter",
                            lambda db, book_id: {chapter.id: {"Рассказчик": 75, "Бдеукс": 10, "Хубол": 15}})
        _roles(monkeypatch, {"Рассказчик": 75, "Бдеукс": 10, "Хубол": 15})
        item = _take(db, chapter, role="Бдеукс", actor="Сергей Зотов")
        db.flush()
        self._job(db, item.id, matched=10, total=10)

        row = book_recording_status(db, book.id)["chapters"][0]

        assert row["asr_coverage"] == pytest.approx(10 / 100), "десять реплик из ста, а не одна роль из одной"

    def test_the_narrator_weighs_as_much_as_he_speaks(self, studio, monkeypatch):
        """У Рассказчика больше всех текста; без него глава не может быть почти готова."""
        from app.services.chapter_delivery import book_recording_status

        db, book, chapter = studio
        monkeypatch.setattr("app.services.chapter_delivery.book_role_counts_by_chapter",
                            lambda db, book_id: {chapter.id: {"Рассказчик": 75, "Бдеукс": 25}})
        _roles(monkeypatch, {"Рассказчик": 75, "Бдеукс": 25})
        item = _take(db, chapter, role="Бдеукс", actor="Сергей Зотов")
        db.flush()
        self._job(db, item.id, matched=25, total=25)

        assert book_recording_status(db, book.id)["chapters"][0]["asr_coverage"] == pytest.approx(0.25)

    def test_two_roles_read_add_up(self, studio, monkeypatch):
        from app.services.chapter_delivery import book_recording_status

        db, book, chapter = studio
        monkeypatch.setattr("app.services.chapter_delivery.book_role_counts_by_chapter",
                            lambda db, book_id: {chapter.id: {"Рассказчик": 60, "Бдеукс": 40}})
        _roles(monkeypatch, {"Рассказчик": 60, "Бдеукс": 40})
        one = _take(db, chapter, role="Рассказчик", actor="Кто-то")
        two = _take(db, chapter, role="Бдеукс", actor="Сергей Зотов")
        db.flush()
        self._job(db, one.id, matched=30, total=60)
        self._job(db, two.id, matched=40, total=40)

        assert book_recording_status(db, book.id)["chapters"][0]["asr_coverage"] == pytest.approx(0.7)

    def test_the_same_denominator_on_the_chapter_screen(self, studio, monkeypatch):
        db, _, chapter = studio
        _roles(monkeypatch, {"Рассказчик": 75, "Бдеукс": 25})
        item = _take(db, chapter, role="Бдеукс", actor="Сергей Зотов")
        db.flush()
        self._job(db, item.id, matched=25, total=25)

        status = chapter_recording_status(db, chapter.id)

        assert status["asr_coverage"] == pytest.approx(0.25)
        assert status["total_lines"] == 100
        assert status["matched_lines"] == 25

    def test_a_role_still_reports_its_own_share(self, studio, monkeypatch):
        """У роли знаменатель свой: диктор отвечает за свои реплики, а не за главу."""
        db, _, chapter = studio
        _roles(monkeypatch, {"Рассказчик": 75, "Бдеукс": 25})
        item = _take(db, chapter, role="Бдеукс", actor="Сергей Зотов")
        db.flush()
        self._job(db, item.id, matched=20, total=25)

        row = next(r for r in chapter_recording_status(db, chapter.id)["roles"] if r["role"] == "Бдеукс")

        assert row["asr_coverage"] == pytest.approx(0.8)


class TestABorrowedLineIsCutFromTheDonorFile:
    def test_the_clip_comes_from_the_donor_and_lies_on_the_line_role_track(self, monkeypatch, tmp_path):
        from types import SimpleNamespace

        from app.services import audio_storage
        from app.services.chapter_delivery import _place_by_script

        monkeypatch.setattr(audio_storage.settings, "audio_storage_path", str(tmp_path))
        tupug = SimpleNamespace(id="a-tupug", stored_key="k/tupug.wav", location="local", canonical_filename="R.wav")
        gamuk = SimpleNamespace(id="a-gamuk", stored_key="k/gamuk.wav", location="local", canonical_filename="T.wav")
        replicas = [{"role": "Гамук", "role_index": 0, "text": "Там ждут."},
                    {"role": "Гамук", "role_index": 1, "text": "Я знаю дорогу."}]
        heard = {
            "a-gamuk": [
                {"index": 0, "matched": True, "takes": [{"start": 0.0, "end": 1.0}]},
                {"index": 1, "matched": True, "takes": [{"start": 5.0, "end": 6.5}], "source_audio_file_id": "a-tupug"},
            ],
        }

        placed = _place_by_script(replicas, {"Гамук": [gamuk]}, heard, relative=True,
                                  files_by_id={"a-tupug": tupug, "a-gamuk": gamuk})

        assert [role for role, _clip in placed] == ["Гамук", "Гамук"]
        assert placed[1][1].path.endswith("k/tupug.wav")
        assert placed[1][1].relative == "R.wav"
        assert (placed[1][1].source_in, placed[1][1].source_out) == (5.0, 6.5)

    def test_a_vanished_donor_leaves_a_gap(self, monkeypatch, tmp_path):
        from types import SimpleNamespace

        from app.services import audio_storage
        from app.services.chapter_delivery import _place_by_script

        monkeypatch.setattr(audio_storage.settings, "audio_storage_path", str(tmp_path))
        gamuk = SimpleNamespace(id="a-gamuk", stored_key="k/gamuk.wav", location="local", canonical_filename="T.wav")
        heard = {"a-gamuk": [{"index": 0, "matched": True, "takes": [{"start": 5.0, "end": 6.5}],
                              "source_audio_file_id": "a-gone"}]}

        placed = _place_by_script([{"role": "Гамук", "role_index": 0, "text": "Я знаю дорогу."}],
                                  {"Гамук": [gamuk]}, heard, relative=True, files_by_id={"a-gamuk": gamuk})

        assert placed == []


class TestTheLatestUploadedFileWins:
    """Реплика бывает в нескольких файлах роли: общий файл и фикс, старый и присланный
    заново. Звучит последний присланный — его прислали, чтобы поправить именно эту
    реплику (решение владельца 2026-09-15)."""

    def test_a_line_found_in_two_files_comes_from_the_one_uploaded_last(self, monkeypatch, tmp_path):
        from datetime import datetime
        from types import SimpleNamespace

        from app.services import audio_storage
        from app.services.chapter_delivery import _place_by_script

        monkeypatch.setattr(audio_storage.settings, "audio_storage_path", str(tmp_path))
        old = SimpleNamespace(id="a-old", stored_key="k/old.wav", location="local",
                              canonical_filename="OLD.wav", uploaded_at=datetime(2026, 9, 15, 10, 0))
        fix = SimpleNamespace(id="a-fix", stored_key="k/fix.wav", location="local",
                              canonical_filename="FIX.wav", uploaded_at=datetime(2026, 9, 15, 11, 0))
        replicas = [{"role": "Гамук", "role_index": 0, "text": "Там ждут."},
                    {"role": "Гамук", "role_index": 1, "text": "Я знаю дорогу."}]
        heard = {
            # Строка 0 звучит в обоих файлах напрямую — берётся из позже присланного (fix).
            # Строка 1 звучит только в старом файле; в fix она помечена скопированной
            # (`_copy_from_siblings`) с `source_audio_file_id=old` — источник остаётся old.
            "a-old": [
                {"index": 0, "matched": True, "takes": [{"start": 0.0, "end": 1.0}]},
                {"index": 1, "matched": True, "takes": [{"start": 2.0, "end": 3.0}]},
            ],
            "a-fix": [
                {"index": 0, "matched": True, "takes": [{"start": 5.0, "end": 6.0}]},
                {"index": 1, "matched": True, "takes": [{"start": 2.0, "end": 3.0}], "source_audio_file_id": "a-old"},
            ],
        }

        placed = _place_by_script(replicas, {"Гамук": [old, fix]}, heard, relative=True,
                                  files_by_id={"a-old": old, "a-fix": fix})

        assert placed[0][1].path.endswith("k/fix.wav"), "строка есть в обоих файлах — звучит последний присланный"
        assert placed[0][1].relative == "FIX.wav"
        assert (placed[0][1].source_in, placed[0][1].source_out) == (5.0, 6.0)
        assert placed[1][1].path.endswith("k/old.wav"), "строка есть только в старом файле"
        assert placed[1][1].relative == "OLD.wav"
        assert (placed[1][1].source_in, placed[1][1].source_out) == (2.0, 3.0)


class TestStartsRemembersTheEarliestClipOfAParagraph:
    """`starts[segment_id]` — начало САМОГО РАННЕГО клипа абзаца, а не первого обработанного:
    когда одна роль уже занята подходами предыдущей реплики, её офсет обгоняет общие часы,
    и следующая реплика того же абзаца — свободной роли — на самом деле встаёт раньше."""

    def test_a_later_processed_replica_can_still_start_earlier(self, monkeypatch, tmp_path):
        from types import SimpleNamespace

        from app.services import audio_storage
        from app.services.chapter_delivery import _place_by_script

        monkeypatch.setattr(audio_storage.settings, "audio_storage_path", str(tmp_path))
        role_a = SimpleNamespace(id="a-A", stored_key="k/a.wav", location="local", canonical_filename="A.wav")
        role_b = SimpleNamespace(id="a-B", stored_key="k/b.wav", location="local", canonical_filename="B.wav")
        replicas = [
            # Предыдущий абзац: роль A читает три подхода подряд — дорожка A убегает
            # вперёд общих часов (сумма подходов больше самого длинного из них).
            {"role": "A", "role_index": 0, "text": "первая", "segment_id": "s_prev"},
            # Абзац под проверкой: роль A продолжает (дорожка ещё занята — офсет большой),
            # затем роль B — общие часы к этому моменту меньше, чем дорожка A.
            {"role": "A", "role_index": 1, "text": "вторая", "segment_id": "s0"},
            {"role": "B", "role_index": 0, "text": "третья", "segment_id": "s0"},
        ]
        heard = {
            "a-A": [
                {"index": 0, "matched": True, "takes": [
                    {"start": 0.0, "end": 2.0}, {"start": 2.0, "end": 4.0}, {"start": 4.0, "end": 6.0},
                ]},
                {"index": 1, "matched": True, "takes": [{"start": 6.0, "end": 7.0}]},
            ],
            "a-B": [{"index": 0, "matched": True, "takes": [{"start": 0.0, "end": 1.0}]}],
        }

        starts: dict = {}
        _place_by_script(replicas, {"A": [role_a], "B": [role_b]}, heard, relative=True,
                         files_by_id={"a-A": role_a, "a-B": role_b}, starts=starts)

        assert starts["s0"] == pytest.approx(3.7), "роль B встала раньше, чем продолжение роли A"

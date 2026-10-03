import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import AudioFile, Base
from app.services.audio_mirror import LocalDiskFull, ensure_room, storage_key


def test_a_file_that_would_eat_the_reserve_is_refused():
    """Забитый под ноль системный диск роняет и сайт, и воркер. Честный отказ
    диктору дешевле — и теперь это единственное хранилище приёма."""
    with pytest.raises(LocalDiskFull):
        ensure_room(2 * 1024**3, free=6 * 1024**3, reserve_bytes=5 * 1024**3)


def test_a_file_that_leaves_the_reserve_intact_is_accepted():
    ensure_room(1024**3, free=10 * 1024**3, reserve_bytes=5 * 1024**3)


def _sessions():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def test_a_fresh_file_has_no_mirror_yet():
    """Пустая отметка — честное «на NAS ещё не копировали». Ноль или «ok»
    соврали бы о наличии второй копии, а на ней держится вся защита от потери."""
    sessions = _sessions()
    with sessions() as db:
        item = AudioFile(
            book_code="KP", original_filename="a.wav", stored_key="KP/Chapter01/a.wav",
            mime_type="audio/wav", size_bytes=1, chapter="Глава1", role="Роль",
        )
        db.add(item)
        db.commit()
        db.refresh(item)
        assert item.mirrored_at is None
        assert item.mirror_state == ""


def test_a_take_goes_into_its_chapter_folder():
    """Папку главы человек открывает руками — поэтому номер с нулём и без uuid."""
    assert storage_key("KP", "Глава 5. Господа мухоморы", "KP_Ch05_Gamuk_GoncharovIvan.wav", "take") == (
        "KP/Chapter05/KP_Ch05_Gamuk_GoncharovIvan.wav"
    )


def test_a_two_digit_chapter_keeps_two_digits():
    assert storage_key("KP", "Глава 30. Болота не любят шоу", "KP_Ch30_Bdeuks_Zotov.wav", "take") == (
        "KP/Chapter30/KP_Ch30_Bdeuks_Zotov.wav"
    )


def test_an_audition_has_no_chapter_and_lives_apart():
    """У пробы главы нет вовсе — складывать её в Chapter00 значило бы соврать."""
    assert storage_key("KP", "", "KP_Osniva_NataliGolubeva_proba.wav", "audition") == (
        "KP/Auditions/KP_Osniva_NataliGolubeva_proba.wav"
    )


def test_a_chapter_without_a_number_falls_back_to_a_named_folder():
    """Глава без номера в заголовке не должна ронять приём — пусть ляжет
    в понятную папку, которую видно глазами и можно разложить потом."""
    assert storage_key("KP", "Пролог", "KP_Prolog_Rol_Aktyor.wav", "take") == (
        "KP/Chapter00/KP_Prolog_Rol_Aktyor.wav"
    )


def test_an_upload_lands_in_the_chapter_folder_on_the_local_disk(tmp_path, monkeypatch):
    """Приём не касается NFS: повиснуть не на чем, провалиться из-за домашнего
    интернета владельца тоже нельзя."""
    import io

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import app.services.audio_uploads as au
    from app.models import Base
    from app.services import audio_storage

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    monkeypatch.setattr(audio_storage.settings, "audio_storage_path", str(tmp_path / "rec"))
    monkeypatch.setattr(au, "probe_audio_file", lambda path: {})

    with sessions() as db:
        item = au.store_audio_file(
            db, source=io.BytesIO(b"take"), mime_type="audio/wav", original_filename="a.wav",
            book_code="KP", chapter="Глава 5. Господа мухоморы", role="Гамук",
            actor_name="Гончаров Иван", safe_name=lambda name: name,
        )
        db.commit()
        assert item.stored_key.startswith("KP/Chapter05/")
        assert (tmp_path / "rec" / item.stored_key).exists()
        assert item.mirrored_at is None


def test_a_book_code_from_the_form_cannot_open_a_path_of_its_own():
    """`book_code` приезжает полем формы из браузера, а этой выкаткой становится
    верхним сегментом пути. Пропускаем его через тот же токен, что и имя файла:
    на выходе только латиница и цифры — разделителю взяться неоткуда, и ни
    вложенной папки, ни выхода из корня форма заказать не может."""
    assert storage_key("../../etc", "Глава 1. Раз", "a.wav", "take") == "ETC/Chapter01/a.wav"
    assert storage_key("KP/sub", "Глава 1. Раз", "a.wav", "take") == "KPSUB/Chapter01/a.wav"
    assert storage_key("", "Глава 1. Раз", "a.wav", "take") == "BOOK/Chapter01/a.wav"


def test_the_book_folder_is_named_like_the_file_prefix():
    """Папка `KP/` и имя `KP_Ch01_…` собираются из одного кода, но разными
    руками: имя — через `book_token`, папка — раньше напрямую. Кириллическое
    «КП» из формы развело бы одну книгу по двум деревьям, и на сервере, и на NAS."""
    assert storage_key("КП", "Глава 1. Раз", "KP_Ch01_Rol_Aktyor.wav", "take") == (
        "KP/Chapter01/KP_Ch01_Rol_Aktyor.wav"
    )


def _mirror_env(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.models import Base
    from app.services import audio_storage, nas_health

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    monkeypatch.setattr(audio_storage.settings, "audio_storage_path", str(tmp_path / "rec"))
    monkeypatch.setattr(audio_storage.settings, "audio_nas_path", str(tmp_path / "nas"))
    nas_health.record_probe(True)
    return sessionmaker(bind=engine)


def test_a_file_is_copied_to_the_nas_and_the_local_copy_stays(tmp_path, monkeypatch):
    """Локальная копия не удаляется никогда: смысл зеркала в том, что копий две."""
    import hashlib

    from app.models import AudioFile
    from app.services.audio_mirror import mirror_once

    sessions = _mirror_env(tmp_path, monkeypatch)
    payload = b"take bytes"
    key = "KP/Chapter05/KP_Ch05_Gamuk_GoncharovIvan.wav"
    (tmp_path / "rec" / "KP" / "Chapter05").mkdir(parents=True)
    (tmp_path / "rec" / key).write_bytes(payload)

    with sessions() as db:
        item = AudioFile(
            book_code="KP", original_filename="a.wav", stored_key=key, mime_type="audio/wav",
            size_bytes=len(payload), chapter="Глава5", role="Гамук", md5=hashlib.md5(payload).hexdigest(),
        )
        db.add(item)
        db.commit()

        stats = mirror_once(db)
        db.commit()

        assert stats["mirrored"] == 1
        assert item.mirror_state == "ok"
        assert item.mirrored_at is not None
        assert (tmp_path / "nas" / key).exists()
        assert (tmp_path / "rec" / key).exists()


def test_a_bad_copy_is_not_marked_and_is_not_retried(tmp_path, monkeypatch):
    """Расхождение суммы означает человека, а не следующий круг: иначе цикл
    будет вечно переписывать один и тот же битый файл."""
    from app.models import AudioFile
    from app.services.audio_mirror import mirror_once

    sessions = _mirror_env(tmp_path, monkeypatch)
    key = "KP/Chapter05/bad.wav"
    (tmp_path / "rec" / "KP" / "Chapter05").mkdir(parents=True)
    (tmp_path / "rec" / key).write_bytes(b"bytes on disk")

    with sessions() as db:
        item = AudioFile(
            book_code="KP", original_filename="bad.wav", stored_key=key, mime_type="audio/wav",
            size_bytes=13, chapter="Глава5", role="Гамук", md5="00" * 16,
        )
        db.add(item)
        db.commit()

        first = mirror_once(db)
        db.commit()
        assert first["failed"] == 1
        assert item.mirror_state == "mismatch"
        assert item.mirrored_at is None

        second = mirror_once(db)
        assert second == {"mirrored": 0, "failed": 0, "left": 0}


def test_an_audition_is_mirrored_like_any_take(tmp_path, monkeypatch):
    """Пробы зеркалируются наравне: владелец сначала сказал «на NAS не едут»,
    но то было про архив готового продукта. Здесь смысл другой — вторая копия,
    и проба теряется так же безвозвратно, как дубль (спека §5)."""
    import hashlib

    from app.models import AudioFile
    from app.services.audio_mirror import mirror_once

    sessions = _mirror_env(tmp_path, monkeypatch)
    payload = b"proba"
    key = "KP/Auditions/KP_Osniva_NataliGolubeva_proba.wav"
    (tmp_path / "rec" / "KP" / "Auditions").mkdir(parents=True)
    (tmp_path / "rec" / key).write_bytes(payload)

    with sessions() as db:
        item = AudioFile(
            book_code="KP", original_filename="p.wav", stored_key=key, mime_type="audio/wav",
            size_bytes=len(payload), chapter="", role="Оснива", kind="audition",
            md5=hashlib.md5(payload).hexdigest(),
        )
        db.add(item)
        db.commit()

        assert mirror_once(db)["mirrored"] == 1
        db.commit()
        assert item.mirror_state == "ok"
        assert (tmp_path / "nas" / key).exists()


def test_mirroring_waits_while_the_nas_is_silent(tmp_path, monkeypatch):
    from app.services import nas_health
    from app.services.audio_mirror import mirror_once

    sessions = _mirror_env(tmp_path, monkeypatch)
    nas_health.record_probe(False)
    with sessions() as db:
        assert mirror_once(db) == {"mirrored": 0, "failed": 0, "left": 0}


def test_a_mismatch_is_reported_to_telegram_and_not_reported_twice(tmp_path, monkeypatch):
    """Расхождение обязано реально уйти в Телеграм — не подменой
    `notify_mirror_problem` целиком (это спрятало бы как раз тот рассинхрон полей,
    ради которого функция заведена), а настоящей отправкой через `send_telegram_message`.
    Второй круг не должен слать письмо повторно: строка уже ждёт человека."""
    from app.models import AudioFile, ScriptBook, ScriptChapter
    from app.services import telegram
    from app.services.audio_mirror import mirror_once

    sessions = _mirror_env(tmp_path, monkeypatch)
    sent: list[str] = []
    monkeypatch.setattr(telegram, "send_telegram_message", lambda db, text, *a, **k: sent.append(text) or 1)

    key = "KP/Chapter05/bad.wav"
    (tmp_path / "rec" / "KP" / "Chapter05").mkdir(parents=True)
    (tmp_path / "rec" / key).write_bytes(b"bytes on disk")

    with sessions() as db:
        book = ScriptBook(title="Крылья Полумрака", source_filename="k.txt", source_format="txt")
        db.add(book)
        db.commit()
        chapter = ScriptChapter(book_id=book.id, chapter_index=5, chapter_title="Глава5")
        db.add(chapter)
        db.commit()

        item = AudioFile(
            book_code="KP", original_filename="bad.wav", stored_key=key, mime_type="audio/wav",
            size_bytes=13, chapter="Глава5", role="Гамук", md5="00" * 16,
        )
        db.add(item)
        db.commit()

        first = mirror_once(db)
        db.commit()
        assert first["failed"] == 1
        assert len(sent) == 1
        assert sent[0].strip()

        second = mirror_once(db)
        assert second == {"mirrored": 0, "failed": 0, "left": 0}
        assert len(sent) == 1


def test_mirroring_keeps_earlier_commits_when_a_later_file_blows_up(tmp_path, monkeypatch):
    """Коммит на каждый файл, а не один на весь проход: обрыв на втором файле НЕ
    OSError-ом (тем классом сбоя, который старый код спула не ловил) не должен
    откатывать уже перенесённый первый файл и терять его строку."""
    import hashlib

    import pytest

    from app.models import AudioFile
    from app.services import audio_storage
    from app.services.audio_mirror import mirror_once

    sessions = _mirror_env(tmp_path, monkeypatch)
    payload1 = b"first take"
    payload2 = b"second take"
    key1 = "KP/Chapter05/a.wav"
    key2 = "KP/Chapter05/b.wav"
    (tmp_path / "rec" / "KP" / "Chapter05").mkdir(parents=True)
    (tmp_path / "rec" / key1).write_bytes(payload1)
    (tmp_path / "rec" / key2).write_bytes(payload2)

    real_write_stream = audio_storage.write_stream

    def flaky_write_stream(key, source, *args, **kwargs):
        if key == key2:
            raise RuntimeError("boom mid-transfer")
        return real_write_stream(key, source, *args, **kwargs)

    monkeypatch.setattr(audio_storage, "write_stream", flaky_write_stream)

    with sessions() as db:
        item1 = AudioFile(
            book_code="KP", original_filename="a.wav", stored_key=key1, mime_type="audio/wav",
            size_bytes=len(payload1), chapter="Глава5", role="Роль1", md5=hashlib.md5(payload1).hexdigest(),
        )
        item2 = AudioFile(
            book_code="KP", original_filename="b.wav", stored_key=key2, mime_type="audio/wav",
            size_bytes=len(payload2), chapter="Глава5", role="Роль2", md5=hashlib.md5(payload2).hexdigest(),
        )
        db.add_all([item1, item2])
        db.commit()

        with pytest.raises(RuntimeError):
            mirror_once(db)

        # Первый файл уже отмечен и закоммичен, несмотря на обрыв на втором.
        db.refresh(item1)
        assert item1.mirror_state == "ok"
        assert item1.mirrored_at is not None

        # Второй остался нетронутым — его подхватит следующий круг.
        db.refresh(item2)
        assert item2.mirror_state == ""
        assert item2.mirrored_at is None


def test_mirroring_does_not_explode_when_the_nas_path_is_empty(tmp_path, monkeypatch):
    """Пустая настройка `audio_nas_path` — законное состояние, зеркала может не быть
    вовсе, а не повод падать.

    Сторожит наличие самой проверки `nas_root()`, а не порядок операндов в `or`:
    оба операнда — чистые геттеры и не ходят на диск, так что перестановка ничего
    не меняет. А вот если проверку убрать, цикл дойдёт до
    `resolve_path(location="nas")`, который на пустом корне бросает `RuntimeError`,
    и зеркалирование будет падать вместо тихого простоя."""
    from app.models import AudioFile
    from app.services import audio_storage, nas_health
    from app.services.audio_mirror import mirror_once

    sessions = _mirror_env(tmp_path, monkeypatch)
    monkeypatch.setattr(audio_storage.settings, "audio_nas_path", "")
    nas_health.record_probe(True)

    key = "KP/Chapter05/a.wav"
    (tmp_path / "rec" / "KP" / "Chapter05").mkdir(parents=True)
    (tmp_path / "rec" / key).write_bytes(b"take")

    with sessions() as db:
        item = AudioFile(
            book_code="KP", original_filename="a.wav", stored_key=key, mime_type="audio/wav",
            size_bytes=4, chapter="Глава5", role="Роль",
        )
        db.add(item)
        db.commit()

        assert mirror_once(db) == {"mirrored": 0, "failed": 0, "left": 0}


def test_a_file_without_an_intake_checksum_is_verified_by_size(tmp_path, monkeypatch):
    """Записи старше миграции 0020 суммы приёма не имеют — сверять нечем, а считать
    их битыми на этом основании значило бы врать (тот же довод, что в
    `audio_integrity.verify_file`). После переезда такие записи станут локальными и
    попадут в этот цикл, поэтому сверка по размеру обязана отмечать их `ok`,
    а не `mismatch` навечно."""
    from app.models import AudioFile
    from app.services.audio_mirror import mirror_once

    sessions = _mirror_env(tmp_path, monkeypatch)
    payload = b"no checksum on intake"
    key = "KP/Chapter05/old.wav"
    (tmp_path / "rec" / "KP" / "Chapter05").mkdir(parents=True)
    (tmp_path / "rec" / key).write_bytes(payload)

    with sessions() as db:
        item = AudioFile(
            book_code="KP", original_filename="old.wav", stored_key=key, mime_type="audio/wav",
            size_bytes=len(payload), chapter="Глава5", role="Гамук", md5="",
        )
        db.add(item)
        db.commit()

        stats = mirror_once(db)
        db.commit()

        assert stats == {"mirrored": 1, "failed": 0, "left": 0}
        assert item.mirror_state == "ok"
        assert item.mirrored_at is not None


def test_left_does_not_count_rows_whose_local_file_is_gone(tmp_path, monkeypatch):
    """«Осталось» обязано значить «ещё предстоит скопировать» — а не включать строки,
    которых цикл не тронул вовсе, потому что файла нет на локальном диске. Иначе
    после переезда журнал будет вечно завышен на потерянные файлы и никогда не
    дойдёт до нуля."""
    from app.models import AudioFile
    from app.services.audio_mirror import mirror_once

    sessions = _mirror_env(tmp_path, monkeypatch)
    key = "KP/Chapter05/ghost.wav"
    # Каталог главы не создаём и файл не пишем — запись есть, файла на диске нет.

    with sessions() as db:
        item = AudioFile(
            book_code="KP", original_filename="ghost.wav", stored_key=key, mime_type="audio/wav",
            size_bytes=4, chapter="Глава5", role="Гамук", md5="ab" * 16,
        )
        db.add(item)
        db.commit()

        assert mirror_once(db) == {"mirrored": 0, "failed": 0, "left": 0}


def test_a_real_upload_is_refused_when_the_local_disk_is_full(tmp_path, monkeypatch):
    """Сторож места проверяется в изоляции, а на приёме он живёт проводкой:
    `free_bytes(local_root())` и резерв из настроек. Перепутанные местами аргументы
    или потерянный вызов не уронят ни один тест изоляции — а на проде уронят сервер,
    потому что забитый под ноль диск кладёт и сайт, и воркер. Здесь проверяется
    именно проводка: настоящий `store_audio_file` на настоящем корне."""
    import io

    import pytest
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import app.services.audio_uploads as au
    from app.models import AudioFile, Base
    from app.services import audio_storage
    from app.services.audio_mirror import LocalDiskFull

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    monkeypatch.setattr(audio_storage.settings, "audio_storage_path", str(tmp_path / "rec"))
    monkeypatch.setattr(au.settings, "local_reserve_gb", 5)
    monkeypatch.setattr(au, "probe_audio_file", lambda path: {})
    # Свободно шесть гигабайт при резерве в пять: файл на два не влезает.
    monkeypatch.setattr(audio_storage, "free_bytes", lambda path: 6 * 1024**3)

    with sessions() as db:
        with pytest.raises(LocalDiskFull):
            au.store_audio_file(
                db, source=io.BytesIO(b"take"), mime_type="audio/wav", original_filename="a.wav",
                book_code="KP", chapter="Глава 5. Господа мухоморы", role="Гамук",
                actor_name="Гончаров Иван", safe_name=lambda name: name,
                size_hint=2 * 1024**3,
            )
        # Отказ до записи, а не после: ни файла на диске, ни строки в базе.
        assert db.query(AudioFile).count() == 0
        assert not (tmp_path / "rec").exists()

        # Тот же файл при свободном месте проходит — сторож отказывает по месту,
        # а не всегда.
        monkeypatch.setattr(audio_storage, "free_bytes", lambda path: 100 * 1024**3)
        item = au.store_audio_file(
            db, source=io.BytesIO(b"take"), mime_type="audio/wav", original_filename="a.wav",
            book_code="KP", chapter="Глава 5. Господа мухоморы", role="Гамук",
            actor_name="Гончаров Иван", safe_name=lambda name: name,
            size_hint=2 * 1024**3,
        )
        db.commit()
        assert (tmp_path / "rec" / item.stored_key).exists()


def test_mirroring_refuses_to_run_when_both_roots_are_the_same_place(tmp_path, monkeypatch):
    """Если корень зеркала лежит внутри локального — а именно так выйдет, если
    выкатить ветку и забыть поправить конфиг, — то обе «копии» окажутся на одном и
    том же NAS.

    Зеркало при этом отрапортует, что всё скопировано, и на экране загорится зелёная
    галочка: владелец прочитает её как «две копии есть, сервер можно чистить», хотя
    копия ровно одна. Врущая галочка опаснее, чем стоящий цикл, поэтому цикл встаёт
    и говорит об этом."""
    from app.services import audio_storage
    from app.services.audio_mirror import roots_overlap

    # Не настоящее монтирование: realpath() делает stat, и при лежащем NAS
    # (hard-mount) тест висел бы вечно — а с ним pre-push и деплой.
    nas_mount = tmp_path / "nas_noname"
    monkeypatch.setattr(audio_storage.settings, "audio_storage_path", str(nas_mount))
    monkeypatch.setattr(audio_storage.settings, "audio_nas_path", str(nas_mount / "rec"))
    assert roots_overlap() is True

    monkeypatch.setattr(audio_storage.settings, "audio_nas_path", str(nas_mount))
    assert roots_overlap() is True

    # Разные места — работаем как обычно.
    monkeypatch.setattr(audio_storage.settings, "audio_storage_path", str(tmp_path / "rec"))
    monkeypatch.setattr(audio_storage.settings, "audio_nas_path", str(tmp_path / "nas"))
    assert roots_overlap() is False

    # Зеркала нет вовсе — это законное состояние, а не перекрытие.
    monkeypatch.setattr(audio_storage.settings, "audio_nas_path", "")
    assert roots_overlap() is False


def test_a_pass_does_nothing_while_the_roots_overlap(tmp_path, monkeypatch):
    """Круг не должен ставить отметки «копия подтверждена», когда копия одна."""
    import hashlib

    from app.models import AudioFile
    from app.services import audio_storage
    from app.services.audio_mirror import mirror_once

    sessions = _mirror_env(tmp_path, monkeypatch)
    root = tmp_path / "both"
    monkeypatch.setattr(audio_storage.settings, "audio_storage_path", str(root))
    monkeypatch.setattr(audio_storage.settings, "audio_nas_path", str(root / "rec"))

    with sessions() as db:
        key = "KP/Chapter01/a.wav"
        path = root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"take")
        item = AudioFile(
            book_code="KP", original_filename="a.wav", stored_key=key, mime_type="audio/wav",
            size_bytes=4, chapter="Глава 1", role="Роль", md5=hashlib.md5(b"take").hexdigest(),
        )
        db.add(item)
        db.commit()

        assert mirror_once(db) == {"mirrored": 0, "failed": 0, "left": 0}
        db.refresh(item)
        assert item.mirrored_at is None


def test_a_second_take_of_the_same_role_does_not_overwrite_the_first(tmp_path, monkeypatch):
    """Уникальность ключа держал uuid в начале имени. Раскладка по книге и главе его
    убрала, и для проб порядковый номер это заменил, а для дублей — нет: второй дубль
    той же роли получал тот же ключ и затирал первый файл на диске.

    Это не окно, а событие: диктор дописывает реплику патчем, и целая глава заменяется
    патчем в несколько секунд. Первая строка в базе продолжает показывать «копия
    подтверждена», зеркало на следующем круге переписывает и копию на NAS, а экран
    рисует зелёную галочку над тем, чего уже нет."""
    import io

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import app.services.audio_uploads as au
    from app.models import Base
    from app.services import audio_storage

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    monkeypatch.setattr(audio_storage.settings, "audio_storage_path", str(tmp_path / "rec"))
    monkeypatch.setattr(au, "probe_audio_file", lambda path: {})

    def _upload(payload: bytes):
        return au.store_audio_file(
            db, source=io.BytesIO(payload), mime_type="audio/wav", original_filename="a.wav",
            book_code="KP", chapter="Глава 5. Господа мухоморы", role="Гамук",
            actor_name="Гончаров Иван", safe_name=lambda name: name,
        )

    with sessions() as db:
        first = _upload(b"take one")
        second = _upload(b"take two")
        db.commit()

        assert first.stored_key != second.stored_key
        assert second.canonical_filename.endswith("_2.wav")
        # Оба файла на диске, и первый — тот же, каким его записали.
        assert (tmp_path / "rec" / first.stored_key).read_bytes() == b"take one"
        assert (tmp_path / "rec" / second.stored_key).read_bytes() == b"take two"


def test_поручение_стирает_копию_на_зеркале(tmp_path, monkeypatch):
    """Поручение снимается, а копия на NAS реально исчезает с диска."""
    from app.models import PendingMirrorDeletion
    from app.services.audio_mirror import purge_deleted_once

    sessions = _mirror_env(tmp_path, monkeypatch)
    key = "KP/Chapter01/take.wav"
    (tmp_path / "nas" / "KP" / "Chapter01").mkdir(parents=True)
    (tmp_path / "nas" / key).write_bytes("звук".encode())

    with sessions() as db:
        db.add(PendingMirrorDeletion(stored_key=key))
        db.commit()

        result = purge_deleted_once(db)

        assert result == {"purged": 1, "missing": 0, "reused": 0, "left": 0}
        assert not (tmp_path / "nas" / key).exists()
        assert db.query(PendingMirrorDeletion).count() == 0


def test_поручение_без_файла_на_зеркале_снимается(tmp_path, monkeypatch):
    """Цель — чтобы копии не было; её и нет. Поручение всё равно снимается, а не
    висит вечно, ожидая файла, который не появится."""
    from app.models import PendingMirrorDeletion
    from app.services.audio_mirror import purge_deleted_once

    sessions = _mirror_env(tmp_path, monkeypatch)

    with sessions() as db:
        db.add(PendingMirrorDeletion(stored_key="KP/Chapter01/net.wav"))
        db.commit()

        result = purge_deleted_once(db)

        assert result == {"purged": 0, "missing": 1, "reused": 0, "left": 0}
        assert db.query(PendingMirrorDeletion).count() == 0


def test_мёртвый_нас_поручения_не_трогает(tmp_path, monkeypatch):
    """Не отдать ошибку, а повиснуть — вот чего нельзя допустить: мёртвый NAS
    не должен даже пытаться, поручение остаётся на следующий круг."""
    from app.models import PendingMirrorDeletion
    from app.services import nas_health
    from app.services.audio_mirror import purge_deleted_once

    sessions = _mirror_env(tmp_path, monkeypatch)
    nas_health.record_probe(False)

    with sessions() as db:
        db.add(PendingMirrorDeletion(stored_key="KP/Chapter01/take.wav"))
        db.commit()

        result = purge_deleted_once(db)

        assert result == {"purged": 0, "missing": 0, "reused": 0, "left": 1}
        assert db.query(PendingMirrorDeletion).count() == 1


def test_проход_не_снимает_поручения_при_перекрытии_корней(tmp_path, monkeypatch):
    """Тот же сторож, что и у копирования: если корни совпали, стирание — это
    стирание единственной копии, а не мусора зеркала."""
    from app.models import PendingMirrorDeletion
    from app.services import audio_storage
    from app.services.audio_mirror import purge_deleted_once

    sessions = _mirror_env(tmp_path, monkeypatch)
    root = tmp_path / "both"
    monkeypatch.setattr(audio_storage.settings, "audio_storage_path", str(root))
    monkeypatch.setattr(audio_storage.settings, "audio_nas_path", str(root / "rec"))

    with sessions() as db:
        db.add(PendingMirrorDeletion(stored_key="KP/Chapter01/take.wav"))
        db.commit()

        result = purge_deleted_once(db)

        assert result == {"purged": 0, "missing": 0, "reused": 0, "left": 1}
        assert db.query(PendingMirrorDeletion).count() == 1


def test_поручение_не_стирает_копию_перезалитой_записи(tmp_path, monkeypatch):
    """«Удалил не тот дубль, залил правильный» не должно стирать правильный.

    `next_ordinal` считает живые строки той же роли, главы и актёра: удаление
    строки освобождает номер, и следующая загрузка получает то же каноническое
    имя, а значит и тот же `stored_key`. Поручение зеркалу к этому моменту ещё
    висит и адресовано ровно этим ключом — то есть указывает уже не на копию
    удалённой записи, а на копию новой.

    Само не залечится: `mirror_once` берёт только строки с `mirrored_at IS NULL`,
    второй раз файл не скопируется никогда, а копию на NAS не сверяет никто —
    `verify_file` проверяет только локальную. Галочка «копий две» осталась бы
    вечной при одной копии, а зеркало здесь единственная избыточность.

    На боевой базе ни одна пара роль-глава-актёр не имеет больше одного дубля,
    так что этот путь — не край, а основной сценарий ветки.
    """
    import io

    import app.services.audio_uploads as au
    from app.models import AudioFile, PendingMirrorDeletion
    from app.services.audio_deletion import delete_audio_file
    from app.services.audio_mirror import mirror_once, purge_deleted_once

    sessions = _mirror_env(tmp_path, monkeypatch)
    monkeypatch.setattr(au, "probe_audio_file", lambda path: {})

    def _upload(db, payload: bytes):
        return au.store_audio_file(
            db, source=io.BytesIO(payload), mime_type="audio/wav", original_filename="a.wav",
            book_code="KP", chapter="Глава 9. Хонгномазаль", role="Хонгномазаль",
            actor_name="Дорохов Архип", safe_name=lambda name: name,
        )

    with sessions() as db:
        first = _upload(db, b"dubl pervyj")
        db.commit()
        key = first.stored_key
        assert mirror_once(db)["mirrored"] == 1
        assert (tmp_path / "nas" / key).exists()

        delete_audio_file(db, first.id, actor_uid="owner", actor_name="Владелец", privileged=True)
        assert [item.stored_key for item in db.query(PendingMirrorDeletion).all()] == [key]

        second = _upload(db, b"dubl vtoroj")
        db.commit()
        # Ключ переиспользован — иначе ловушки бы не было и тест ничего не стерёг бы.
        assert second.stored_key == key
        assert mirror_once(db)["mirrored"] == 1
        assert (tmp_path / "nas" / key).read_bytes() == b"dubl vtoroj"

        result = purge_deleted_once(db)

        # Главное: копия НОВОЙ записи на зеркале цела.
        assert (tmp_path / "nas" / key).read_bytes() == b"dubl vtoroj"
        # Поручение при этом снято, а не оставлено висеть на следующий круг:
        # исполнять его нечего, ключ занят живой строкой.
        assert db.query(PendingMirrorDeletion).count() == 0
        assert result == {"purged": 0, "missing": 0, "reused": 1, "left": 0}
        row = db.get(AudioFile, second.id)
        assert row.mirror_state == "ok" and row.mirrored_at is not None

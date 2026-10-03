"""От актёра ждут его речь, а не кусок разметки целиком.

Кусок роли бывает составным: «— Речь, — ремарка автора. — Речь». Ремарку внутри
куска читает рассказчик, а не актёр этой роли, поэтому сверка не должна требовать
её от него — иначе на месте настоящей заслуги встаёт ложный пропуск.

Рассказчик — исключение: авторские слова и есть его работа, поэтому для него
разбор не применяется вовсе.
"""
import re
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import AsrJob, AudioFile, ScriptChapter
from app.services.asr_align import align_transcript
from app.services.asr_notice import missing_lines_notice
from app.services.asr_run import chapter_take_fingerprint, expected_lines, narration_only_lines, run_asr_for_chapter
from app.services.audio_uploads import TAKE
from app.v2.models import V2Attribution, V2Segment

BOOK = "book-asr-run"


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    with sessionmaker(bind=engine)() as session:
        yield session


def _say(db, chapter: str, ordinal: int, speaker: str, text: str | None = None) -> None:
    text = text if text is not None else f"Реплика {ordinal}."
    seg = f"{chapter}:{ordinal:05d}"
    db.add(V2Segment(id=seg, book_id=BOOK, chapter_id=chapter, ordinal=ordinal,
                     kind="paragraph", text=text, char_start=0, char_end=len(text)))
    db.add(V2Attribution(id=str(uuid.uuid4()), segment_id=seg, span_start=0,
                         span_end=len(text), speaker=speaker, confidence=0.9,
                         source="llm", version=1))


def _chapter_id_for_title(title: str) -> str:
    match = re.search(r"\d+", title)
    return f"ch-{match.group(0)}" if match else f"ch-{title}"


def _add_take(db, chapter: str, role: str, file_id: str) -> None:
    """Дубль в составе главы — так, как его сегодня ищет `run_asr_for_chapter`:
    по совпадению `AudioFile.chapter` с `chapter_title` строки `ScriptChapter`."""
    chapter_id = _chapter_id_for_title(chapter)
    if db.get(ScriptChapter, chapter_id) is None:
        db.add(ScriptChapter(id=chapter_id, book_id=BOOK, chapter_index=1, chapter_title=chapter))
    db.add(AudioFile(
        id=file_id,
        book_code=BOOK,
        original_filename=f"{file_id}.wav",
        stored_key=f"{file_id}.wav",
        mime_type="audio/wav",
        size_bytes=1,
        chapter=chapter,
        role=role,
        kind=TAKE,
    ))


def _recognizable_take(db, chapter: str, role: str, file_id: str) -> None:
    """Дубль, который распознавание действительно возьмёт в работу.

    `_add_take` этого не даёт: `run_asr_for_take` ищет главу дубля через код
    книги (`find_chapter_for_take`), а книги в базе нет вовсе — распознавание
    падает с `chapter_not_found`, не дойдя до расшифровки. Здесь книга заведена,
    а «Т» — код, который `derive_book_code` вывела бы из её заголовка «Тест»
    (первая буква слова), а не сам заголовок целиком.
    """
    from app.models import ScriptBook

    if db.get(ScriptBook, "book-1") is None:
        db.add(ScriptBook(id="book-1", title="Тест", source_filename="t.txt", source_format="txt"))
    chapter_id = _chapter_id_for_title(chapter)
    row = db.get(ScriptChapter, chapter_id)
    if row is None:
        db.add(ScriptChapter(id=chapter_id, book_id="book-1", chapter_index=1, chapter_title=chapter))
    else:
        row.book_id = "book-1"
    db.add(AudioFile(
        id=file_id,
        book_code="Т",
        original_filename=f"{file_id}.wav",
        stored_key=f"{file_id}.wav",
        mime_type="audio/wav",
        size_bytes=1,
        chapter=chapter,
        role=role,
        kind=TAKE,
    ))


def _chapter_with_roles(db, chapter_id: str, roles: list[str]) -> None:
    """Глава, в которой каждая из ролей говорит хотя бы раз — иначе `chapter_is_ready`
    не увидит, кого закрывать дублем, и отчёт не решится, что глава собралась."""
    match = re.search(r"\d+", chapter_id)
    title = f"Глава {match.group(0)}" if match else chapter_id
    if db.get(ScriptChapter, chapter_id) is None:
        db.add(ScriptChapter(id=chapter_id, book_id=BOOK, chapter_index=1, chapter_title=title))
    for ordinal, role in enumerate(roles):
        _say(db, chapter_id, ordinal, role)


def _fake_transcribe(source) -> dict:
    """Расшифровка, которая слышит всё, что могло понадобиться: слов с запасом больше,
    чем ролей в тесте, — реальное распознавание сюда не ходит, проверяется склейка."""
    text = " ".join(f"Реплика {n}." for n in range(8))
    return {"text": text, "segments": [{"text": text, "start": 0.0, "end": 16.0, "words": []}]}


@pytest.fixture()
def sent(monkeypatch):
    """Перехватывает отправку в Телеграм и складывает тексты в список — вместо
    настоящего похода в сеть. В проекте есть заслон на живые отправки из тестов
    (`tests/conftest.py`), это — его использование, не обход."""
    from app.services import telegram

    box: list[str] = []
    monkeypatch.setattr(telegram, "send_telegram_message", lambda db, text, *a, **k: box.append(text) or 1)
    return box


def test_отпечаток_меняется_с_приходом_нового_дубля(db):
    """Отчёт уходит один раз на состав главы; новый дубль — новый состав."""
    _add_take(db, chapter="Глава 1", role="Гамук", file_id="take-1")
    db.commit()
    first = chapter_take_fingerprint(db, "ch-1")

    assert first
    assert chapter_take_fingerprint(db, "ch-1") == first, "тот же состав — тот же отпечаток"

    _add_take(db, chapter="Глава 1", role="Тупуг", file_id="take-2")
    db.commit()

    assert chapter_take_fingerprint(db, "ch-1") != first


def test_ожидаемая_реплика_не_содержит_ремарки(db):
    """Актёр читает свою речь; авторские слова внутри куска — не его работа."""
    _say(db, "ch-1", 0, "Зеленщик",
         "- …Хотя тот уехал куда-то, - поправил сам себя трактирщик. – Сгинул, наверное")
    db.commit()

    lines = expected_lines(db, "ch-1", "Зеленщик")

    assert len(lines) == 1
    assert "поправил сам себя трактирщик" not in lines[0]
    assert "Хотя тот уехал куда-то" in lines[0]


def test_повествование_на_роли_от_актёра_не_ожидается(db):
    """Кусок без речи вообще не попадает в ожидаемое — и уходит отдельным списком."""
    _say(db, "ch-1", 0, "Гамук", "Гамук подплыл ближе и доверительно сказал:")
    _say(db, "ch-1", 1, "Гамук", "— Проводил твоих друзей.")
    db.commit()

    assert expected_lines(db, "ch-1", "Гамук") == ["Проводил твоих друзей."]
    assert narration_only_lines(db, "ch-1", "Гамук") == ["Гамук подплыл ближе и доверительно сказал:"]


def test_рассказчику_разбор_не_применяется(db):
    """Авторские слова — работа рассказчика; ожидать от него только речь означало
    бы перестать сверять большую часть того, что он читает."""
    text = "- …Хотя тот уехал куда-то, - поправил сам себя трактирщик. – Сгинул, наверное"
    _say(db, "ch-1", 0, "Рассказчик", text)
    db.commit()

    assert expected_lines(db, "ch-1", "Рассказчик") == [text]


def test_у_рассказчика_нет_кусков_без_речи(db):
    """Для рассказчика `narration_only_lines` пуст по той же причине: у него всё —
    его работа, и сигнализировать владельцу тут не о чём."""
    _say(db, "ch-1", 0, "Рассказчик", "Прошло много лет.")
    db.commit()

    assert narration_only_lines(db, "ch-1", "Рассказчик") == []


def test_письмо_диктору_не_содержит_куска_без_речи(db):
    """Ради этого всё и делается: письмо о пропусках не должно требовать голоса там,
    где в куске нет ни слова речи."""
    _say(db, "ch-1", 0, "Гамук", "Гамук подплыл ближе и доверительно сказал:")
    _say(db, "ch-1", 1, "Гамук", "— Проводил твоих друзей.")
    db.commit()

    expected = expected_lines(db, "ch-1", "Гамук")
    alignment = align_transcript(expected, [])  # диктор ничего не прочитал

    assert all("подплыл ближе" not in line["text"] for line in alignment["lines"])

    text = missing_lines_notice(role="Гамук", chapter="Глава 1", book_title="К",
                                lines=alignment["lines"])
    assert "подплыл ближе" not in text


class TestReportOnAssembly:
    """Глава собралась — у каждой говорящей роли есть хотя бы один дубль — и владелец
    получает один отчёт. Не собралась или состав не изменился — молчание."""

    def test_отчёт_уходит_когда_глава_собралась(self, db, sent):
        """Собралась — значит у каждой говорящей роли есть хотя бы один дубль."""
        _chapter_with_roles(db, "ch-1", ["Гамук", "Тупуг"])
        _add_take(db, chapter="Глава 1", role="Гамук", file_id="take-1")
        _add_take(db, chapter="Глава 1", role="Тупуг", file_id="take-2")
        db.commit()

        run_asr_for_chapter(db, "ch-1", transcribe=_fake_transcribe)

        assert len(sent) == 1
        assert "Глава 1" in sent[0]

    def test_повторный_прогон_по_тому_же_составу_молчит(self, db, sent):
        _chapter_with_roles(db, "ch-1", ["Гамук"])
        _add_take(db, chapter="Глава 1", role="Гамук", file_id="take-1")
        db.commit()

        run_asr_for_chapter(db, "ch-1", transcribe=_fake_transcribe)
        run_asr_for_chapter(db, "ch-1", transcribe=_fake_transcribe)

        assert len(sent) == 1, "владелец не должен получать один и тот же отчёт дважды"

    def test_новый_дубль_даёт_новый_отчёт(self, db, sent):
        _chapter_with_roles(db, "ch-1", ["Гамук"])
        _add_take(db, chapter="Глава 1", role="Гамук", file_id="take-1")
        db.commit()
        run_asr_for_chapter(db, "ch-1", transcribe=_fake_transcribe)

        _add_take(db, chapter="Глава 1", role="Гамук", file_id="take-2")
        db.commit()
        run_asr_for_chapter(db, "ch-1", transcribe=_fake_transcribe)

        assert len(sent) == 2

    def test_несобранная_глава_отчёта_не_даёт(self, db, sent):
        """У Тупуга дубля нет — глава не собрана, отчитываться рано."""
        _chapter_with_roles(db, "ch-1", ["Гамук", "Тупуг"])
        _add_take(db, chapter="Глава 1", role="Гамук", file_id="take-1")
        db.commit()

        run_asr_for_chapter(db, "ch-1", transcribe=_fake_transcribe)

        assert sent == []

    def test_глава_без_дублей_вовсе_отчёта_не_даёт(self, db, sent):
        """Дублей нет совсем: отпечаток состава пуст ещё до всякого распознавания.
        Раньше этот случай не проверялся ничем, хотя код его обрабатывает —
        `chapter_take_fingerprint` возвращает пустую строку, а пустой отпечаток
        отправку не запускает."""
        _chapter_with_roles(db, "ch-1", ["Гамук", "Тупуг"])
        db.commit()

        assert chapter_take_fingerprint(db, "ch-1") == ""

        result = run_asr_for_chapter(db, "ch-1", transcribe=_fake_transcribe)

        assert result == {"chapter_id": "ch-1", "takes": 0, "done": 0, "realigned": 0, "failed": 0}
        assert sent == []
        chapter = db.get(ScriptChapter, "ch-1")
        assert chapter.coverage_reported_takes == ""
        assert chapter.coverage_reported_at is None

    def test_упавшая_сборка_отчёта_не_топит_распознанные_дубли(self, db, sent, monkeypatch):
        """Отчёт — не единственный смысл прогона. Единственный `db.commit()` на
        весь прогон живёт выше, в `perform_asr_chapter_task`: если сборка отчёта
        бросает исключение и оно долетает до вызывающего, `with` откатит сессию
        и стёрты будут все уже распознанные дубли главы, а не только отчёт. Такое
        задание уйдёт в упавшие с ложной причиной — как будто не распозналось,
        хотя распозналось. Отпечаток при этом не должен записаться: иначе владелец
        не получит отчёт никогда и не узнает, что он не дошёл."""
        from app.models import ScriptBook
        from app.services import chapter_coverage_report

        def _boom(db, chapter_id):
            raise RuntimeError("сборка отчёта упала")

        monkeypatch.setattr(chapter_coverage_report, "build_chapter_report", _boom)

        # `run_asr_for_take` находит главу дубля через код книги
        # (`find_chapter_for_take`) — код должен совпасть с тем, что выводится из
        # заголовка книги, иначе распознавание не состоится вовсе, ещё до всякого
        # отчёта, и тест проверял бы не то падение. `_add_take` этого не обеспечивает
        # (остальные тесты файла берут `book_code` по умолчанию и не смотрят на
        # `AsrJob.status`), поэтому книга и совпадающий код здесь заведены отдельно.
        db.add(ScriptBook(id="book-1", title="Тест", source_filename="t.txt", source_format="txt"))
        _chapter_with_roles(db, "ch-1", ["Гамук"])
        db.get(ScriptChapter, "ch-1").book_id = "book-1"
        db.add(AudioFile(
            # «Т» — код, который `derive_book_code` вывела бы из заголовка «Тест»
            # (первая буква слова), а не сам заголовок целиком.
            id="take-1", book_code="Т", original_filename="take-1.wav", stored_key="take-1.wav",
            mime_type="audio/wav", size_bytes=1, chapter="Глава 1", role="Гамук", kind=TAKE,
        ))
        db.commit()

        result = run_asr_for_chapter(db, "ch-1", transcribe=_fake_transcribe)

        assert result == {"chapter_id": "ch-1", "takes": 1, "done": 1, "realigned": 0, "failed": 0}
        assert sent == []
        job = db.query(AsrJob).filter(AsrJob.chapter_id == "ch-1").one()
        assert job.status == "done", "распознавание удалось — упавший отчёт не должен переписать это на failed"
        chapter = db.get(ScriptChapter, "ch-1")
        assert chapter.coverage_reported_takes == "", "отправка не состоялась — отпечаток не пишется"

    def test_дубль_пришедший_во_время_прогона_не_объявляется_разобранным(self, db, sent):
        """Состав главы снимается в начале прогона, а прогон длится минуты.

        Дубль, загруженный посреди прогона, распознан не был — но раньше он всё
        равно попадал и в «глава собралась», и в отпечаток, считавшийся свежим
        запросом в самом конце. Отчёт уходил со словами «всё на месте», не назвав
        роль, которую никто не слушал, и по этому составу отчёта не было уже
        никогда: отпечаток совпадал, отправка не срабатывала.

        Отпечаток теперь считается по тому списку дублей, который прогон реально
        разобрал. Пришедший в середине даёт другой состав — и следующий прогон
        отчитывается честно.
        """
        _chapter_with_roles(db, "ch-1", ["Гамук", "Тупуг"])
        _recognizable_take(db, "Глава 1", "Гамук", "take-1")
        db.commit()

        def _upload_during_run(source):
            # Тупуг присылает дубль, пока прогон занят дублем Гамука
            if db.get(AudioFile, "take-2") is None:
                _recognizable_take(db, "Глава 1", "Тупуг", "take-2")
                db.flush()
            return _fake_transcribe(source)

        run_asr_for_chapter(db, "ch-1", transcribe=_upload_during_run)

        chapter = db.get(ScriptChapter, "ch-1")
        assert chapter.coverage_reported_takes != chapter_take_fingerprint(db, "ch-1"), (
            "состав, в котором дубль не был распознан, не должен считаться отчитанным"
        )

        run_asr_for_chapter(db, "ch-1", transcribe=_fake_transcribe)

        assert len(sent) == 2, "по полному составу владелец обязан получить отчёт"
        assert chapter.coverage_reported_takes == chapter_take_fingerprint(db, "ch-1")

    def test_упавший_до_создания_задания_дубль_назван_в_отчёте(self, db, sent):
        """`run_asr_for_take` бросает `AsrRunError` до того, как заведёт строку
        `AsrJob`: файла нет, это не дубль, главы не нашлось. Строки нет — значит
        отчёт, который читает только `AsrJob`, о такой роли не знает ничего, а
        число ролей с записью считает по файлам. Файл есть, роль «записана»,
        разделов нет — и владелец читает «всё на месте» про то, чего никто не
        слушал. Число упавших прогон знает; оно должно дойти до отчёта.
        """
        _chapter_with_roles(db, "ch-1", ["Гамук"])
        _add_take(db, chapter="Глава 1", role="Гамук", file_id="take-1")
        db.commit()

        result = run_asr_for_chapter(db, "ch-1", transcribe=_fake_transcribe)

        assert result["failed"] == 1
        assert not db.query(AsrJob).filter(AsrJob.chapter_id == "ch-1").all(), (
            "проверяется именно тот путь, где строки задания не появилось вовсе"
        )
        assert len(sent) == 1
        assert "всё на месте" not in sent[0].lower()
        assert "Гамук" in sent[0]
        assert "chapter_not_found" in sent[0]

    def test_неудачная_доставка_не_ставит_отметку(self, db, monkeypatch):
        """Отметка о составе — это обещание «отчёт по нему уже ушёл». Если
        отправка не удалась, обещание ложно, и второго шанса не будет: следующий
        прогон увидит совпавший отпечаток и промолчит навсегда."""
        from app.config import settings
        from app.services import telegram

        monkeypatch.setattr(settings, "telegram_notify_enabled", True)
        monkeypatch.setattr(telegram, "send_telegram_message", lambda db, text, *a, **k: 0)

        _chapter_with_roles(db, "ch-1", ["Гамук"])
        _add_take(db, chapter="Глава 1", role="Гамук", file_id="take-1")
        db.commit()

        run_asr_for_chapter(db, "ch-1", transcribe=_fake_transcribe)

        chapter = db.get(ScriptChapter, "ch-1")
        assert chapter.coverage_reported_takes == ""
        assert chapter.coverage_reported_at is None

    def test_выключенный_канал_уведомлений_отметку_ставит(self, db, monkeypatch):
        """Ноль отправленных чатов при выключенных уведомлениях — не отказ
        доставки, а сознательно закрытый канал: адресата нет вовсе. Пересобирать
        отчёт на каждую загрузку впустую незачем, отметка ставится."""
        from app.config import settings
        from app.services import telegram

        monkeypatch.setattr(settings, "telegram_notify_enabled", False)
        monkeypatch.setattr(telegram, "send_telegram_message", lambda db, text, *a, **k: 0)

        _chapter_with_roles(db, "ch-1", ["Гамук"])
        _add_take(db, chapter="Глава 1", role="Гамук", file_id="take-1")
        db.commit()

        run_asr_for_chapter(db, "ch-1", transcribe=_fake_transcribe)

        chapter = db.get(ScriptChapter, "ch-1")
        assert chapter.coverage_reported_takes == chapter_take_fingerprint(db, "ch-1")
        assert chapter.coverage_reported_at is not None


class TestChapterUploadDoesNotRepayRecognition:
    """Распознавание платное и невоспроизводимое: приём главы платит только за файл,
    которого раньше не слышали, а уже распознанные пересчитывает бесплатно
    (`asr_replay.realign_job`) — иначе фикс в одну реплику заново оплачивал бы
    распознавание всего хора уже сданной главы."""

    def test_a_recognised_file_is_realigned_a_new_one_is_transcribed(self, db, sent):
        from app.models import AsrJob

        _chapter_with_roles(db, "ch-1", ["Тупуг", "Тупуг", "Гамук"])
        _recognizable_take(db, "Глава 1", "Тупуг", "r")
        db.commit()

        calls: list[str] = []

        def _counting_transcribe(source):
            calls.append(source)
            return _fake_transcribe(source)

        run_asr_for_chapter(db, "ch-1", transcribe=_counting_transcribe)

        assert len(calls) == 1, "первый прогон слышит только один дубль — «r»"
        calls.clear()
        before = db.query(AsrJob).filter(AsrJob.audio_file_id == "r").one().alignment_json

        # Вторую реплику Тупуга переносят Гамуку — точно так же, как это делает
        # `reassign_segment` в жизни; у «r» состав ожидаемого меняется.
        from tests.asr_studio import move_line

        move_line(db, "ch-1", 1, "Гамук")
        _recognizable_take(db, "Глава 1", "Гамук", "t")
        db.commit()

        run_asr_for_chapter(db, "ch-1", transcribe=_counting_transcribe)

        assert len(calls) == 1, "второй прогон платит только за новый файл «t»"
        after = db.query(AsrJob).filter(AsrJob.audio_file_id == "r").one().alignment_json
        assert after != before, "сверка «r» пересчитана по текущей разметке — бесплатно"


class TestTheEditorButtonPaysForRecognitionAgain:
    """Кнопка «Распознать» в редакторе обещает услышать главу заново — и платит за это.
    Приём файлов (`force=False`) уже распознанное пересчитывает бесплатно."""

    def _counting(self, calls):
        def _transcribe(source):
            calls.append(source)
            return _fake_transcribe(source)
        return _transcribe

    def test_force_transcribes_an_already_recognised_take(self, db, sent):
        _chapter_with_roles(db, "ch-1", ["Тупуг"])
        _recognizable_take(db, "Глава 1", "Тупуг", "r")
        db.commit()
        calls: list[str] = []
        run_asr_for_chapter(db, "ch-1", transcribe=self._counting(calls))
        calls.clear()

        result = run_asr_for_chapter(db, "ch-1", transcribe=self._counting(calls), force=True)

        assert len(calls) == 1
        assert result["done"] == 1 and result["realigned"] == 0

    def test_the_upload_path_does_not_transcribe_it_again(self, db, sent):
        _chapter_with_roles(db, "ch-1", ["Тупуг"])
        _recognizable_take(db, "Глава 1", "Тупуг", "r")
        db.commit()
        calls: list[str] = []
        run_asr_for_chapter(db, "ch-1", transcribe=self._counting(calls))
        calls.clear()

        result = run_asr_for_chapter(db, "ch-1", transcribe=self._counting(calls))

        assert calls == []
        assert result["done"] == 0 and result["realigned"] == 1

    def test_broken_stored_json_of_one_job_does_not_abort_the_chapter(self, db, sent, monkeypatch):
        _chapter_with_roles(db, "ch-1", ["Тупуг", "Гамук"])
        _recognizable_take(db, "Глава 1", "Тупуг", "r")
        _recognizable_take(db, "Глава 1", "Гамук", "t")
        db.commit()
        calls: list[str] = []
        run_asr_for_chapter(db, "ch-1", transcribe=self._counting(calls))
        calls.clear()
        broken = db.query(AsrJob).filter(AsrJob.audio_file_id == "r").one().id

        from app.services import asr_replay
        real = asr_replay.realign_job

        def _realign(db_, job_id, **kwargs):
            if job_id == broken:
                raise ValueError("испорченный heard_json")
            return real(db_, job_id, **kwargs)

        monkeypatch.setattr(asr_replay, "realign_job", _realign)

        result = run_asr_for_chapter(db, "ch-1", transcribe=self._counting(calls))

        assert calls == [], "за сломанную джобу заново не платим"
        assert result["realigned"] == 1 and result["done"] == 0


class TestTheEditorButtonEnqueuesForce:
    def test_the_endpoint_passes_force_and_the_task_threads_it(self, monkeypatch):
        from types import SimpleNamespace

        from app.v2 import api as v2_api
        import app.worker_tasks as worker_tasks

        enqueued = {}
        monkeypatch.setattr(v2_api, "_is_authenticated", lambda request: True)
        monkeypatch.setattr(v2_api, "_can_edit", lambda request: True)
        monkeypatch.setattr(v2_api, "chapter_recording_status", lambda db, cid: {"roles": [{"files": [1]}]})
        monkeypatch.setattr(v2_api, "enqueue_tracked_task", lambda **kw: enqueued.update(kw) or "job-1")
        monkeypatch.setattr(v2_api, "SessionLocal", lambda: _NullSession())

        response = v2_api.api_v2_chapter_asr(SimpleNamespace(), "ch-1")

        assert response.status_code == 200
        assert enqueued["force"] is True

        seen = {}

        def _run(db_, chapter_id, **kwargs):
            seen.update(kwargs)
            return {"chapter_id": chapter_id, "takes": 2, "done": 1, "realigned": 1, "failed": 0}

        finished = {}
        monkeypatch.setattr("app.services.asr_run.run_asr_for_chapter", _run)
        monkeypatch.setattr(worker_tasks, "SessionLocal", lambda: _NullSession())
        monkeypatch.setattr(worker_tasks, "_start_background_run", lambda *a, **k: None)
        monkeypatch.setattr(worker_tasks, "_finish_background_run",
                            lambda factory, cls, run_id, status, text: finished.update(text=text))

        worker_tasks.perform_asr_chapter_task("ch-1", "run-1", force=True)
        assert seen == {"force": True}
        assert finished["text"] == "распознано 1, пересчитано 1, не вышло 0"

        seen.clear()
        worker_tasks.perform_asr_chapter_task("ch-1", "run-2")
        assert seen == {"force": False}


class _NullSession:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def commit(self):
        pass


class TestTheFixFileClosesAMovedLineWithNoDonor:
    """Реплику Тупуга отдали Гамуку, а у Тупуга файла для неё нет вовсе — занять её
    негде (`asr_borrow` ищет только у того же актёра, а не выдумывает звук). Единственный
    способ закрыть дыру — прислать её отдельным фиксом; он должен закрыть главу без
    лишних писем и без повторной оплаты уже сданного основного файла."""

    def test_the_fix_closes_the_gap_without_a_letter_and_without_a_repay(self, monkeypatch):
        import app.models  # noqa: F401
        from datetime import datetime
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        from app.db import Base
        from app.models import AudioFile
        from app.services import asr_notice
        from app.services.asr_run import chapter_replicas, run_asr_for_chapter
        from app.services.chapter_delivery import _lines_by_audio, _place_by_script, book_recording_status
        from tests.asr_studio import BOOK_ID, CHAPTER_TITLE, add_take, heard, make_chapter, move_line

        engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(engine)
        letters = []
        monkeypatch.setattr(asr_notice, "resolve_actor_candidates", lambda db, name: [("chat", name)])
        monkeypatch.setattr(asr_notice, "send_telegram_message", lambda db, text, **k: letters.append(text) or 1)
        monkeypatch.setattr("app.services.asr_run._report_chapter_if_assembled", lambda *a, **k: False)
        monkeypatch.setattr("app.services.audio_storage.resolve_path", lambda key, location="local": key)
        with sessionmaker(bind=engine)() as db:
            lines = [("Тупуг", "Мы пойдём на север через перевал."),
                     ("Гамук", "Там нас давно ждут старые друзья."),
                     ("Тупуг", "Я знаю короткую дорогу к реке.")]
            chapter = make_chapter(db, lines, {"Тупуг": "Гончаров Иван", "Гамук": "Гончаров Иван"})
            # Основной файл Гамука — без перенесённой реплики; у Тупуга своего файла нет.
            add_take(db, chapter, role="Гамук", actor="Гончаров Иван",
                     said=["Там нас давно ждут старые друзья."], take_id="gamuk-main")
            db.get(AudioFile, "gamuk-main").uploaded_at = datetime(2026, 9, 15, 10, 0)
            db.commit()

            # Строку у Тупуга забирают Гамуку уже после того, как основной файл принят.
            move_line(db, chapter, 2, "Гамук")

            # Гамук присылает фикс отдельным, более поздним файлом.
            db.add(AudioFile(id="gamuk-fix", book_code="КП", original_filename="Гамук_fix.wav",
                             stored_key="k/gamuk-fix.wav", canonical_filename="KP_Ch11_Gamuk_fix.wav",
                             mime_type="audio/wav", size_bytes=10, chapter=CHAPTER_TITLE, role="Гамук",
                             actor_name="Гончаров Иван", kind="take", uploaded_at=datetime(2026, 9, 15, 11, 0)))
            db.commit()

            said = {"k/gamuk-fix.wav": heard("Я знаю короткую дорогу к реке.")}
            run_asr_for_chapter(db, chapter, transcribe=lambda path: said[path](path))

            assert letters == [], "фикс закрыл дыру полностью — писем нет"

            status = book_recording_status(db, BOOK_ID)
            row = status["chapters"][0]
            assert row["missing_lines"] == 0

            files = db.query(AudioFile).filter(AudioFile.kind == "take").order_by(AudioFile.uploaded_at.asc()).all()
            by_role: dict[str, list] = {}
            for item in files:
                by_role.setdefault(item.role, []).append(item)
            placed = _place_by_script(
                chapter_replicas(db, chapter), by_role, _lines_by_audio(db, [f.id for f in files]),
                relative=False, files_by_id={f.id: f for f in files},
            )
            moved = [clip for role, clip in placed if role == "Гамук" and clip.name.startswith("Я знаю короткую")]
            assert len(moved) == 1 and moved[0].path == "k/gamuk-fix.wav"


class TestChapterUploadBorrowsBeforeWriting:
    def test_a_line_closed_by_the_neighbour_file_gets_no_letter(self, monkeypatch):
        import app.models  # noqa: F401
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        from app.db import Base
        from app.models import AudioFile
        from app.services import asr_notice
        from app.services.asr_run import run_asr_for_chapter
        from app.time_utils import utcnow_naive
        from tests.asr_studio import CHAPTER_TITLE, heard, make_chapter

        engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(engine)
        letters = []
        monkeypatch.setattr(asr_notice, "resolve_actor_candidates", lambda db, name: [("chat", name)])
        monkeypatch.setattr(asr_notice, "send_telegram_message", lambda db, text, **k: letters.append(text) or 1)
        monkeypatch.setattr("app.services.asr_run._report_chapter_if_assembled", lambda *a, **k: False)
        with sessionmaker(bind=engine)() as db:
            chapter = make_chapter(db, [("Тупуг", "Мы пойдём на север через перевал."),
                                        ("Гамук", "Там нас давно ждут старые друзья."),
                                        ("Гамук", "Я знаю короткую дорогу к реке.")],
                                   {"Тупуг": "Гончаров Иван", "Гамук": "Гончаров Иван"})
            for take_id, role in [("r", "Тупуг"), ("t", "Гамук")]:
                db.add(AudioFile(id=take_id, book_code="КП", original_filename=f"{role}.wav", stored_key=f"k/{take_id}",
                                 mime_type="audio/wav", size_bytes=1, chapter=CHAPTER_TITLE, role=role,
                                 actor_name="Гончаров Иван", kind="take", uploaded_at=utcnow_naive()))
            db.flush()
            said = {"k/r": heard("Мы пойдём на север через перевал.", "Я знаю короткую дорогу к реке."),
                    "k/t": heard("Там нас давно ждут старые друзья.")}
            monkeypatch.setattr("app.services.audio_storage.resolve_path", lambda key, location="local": key)

            run_asr_for_chapter(db, chapter, transcribe=lambda path: said[path](path))

        assert letters == []

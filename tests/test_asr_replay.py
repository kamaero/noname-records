"""Переигрываемая сверка: услышанное хранится, выравнивание пересчитывается бесплатно.

Распознавание — единственный платный шаг во всём пути дубля, и оно же единственное
невоспроизводимое: робот слушает файл один раз. Сверка со сценарием, наоборот, своя,
бесплатная и меняется у нас постоянно. Пока услышанное выбрасывалось, каждая правка
сверки применялась только к будущим записям, а архив оставался жить по старым правилам —
переигрывать его было нечем, кроме повторной оплаты распознавания.

Здесь проверяется то, что закрывает эту дыру: услышанное сохраняется дословно, и
пересчёт из сохранённого даёт ровно тот же итог, что посчитала сама джоба.
"""
import json
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.time_utils import utcnow_naive
from app.v2.models import V2Attribution, V2Segment


@pytest.fixture()
def studio():
    """Глава с двумя репликами Дгарнина и одним присланным дублем."""
    from app.models import AudioFile, ScriptBook, ScriptChapter

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        chapter_id = "ch-1"
        for ordinal, text in [
            (0, "Дгарнин обернулся. — Я не пойду туда, — сказал он."),
            (1, "— Почему же? — Там холодно."),
        ]:
            db.add(V2Segment(id=f"{chapter_id}:{ordinal:05d}", book_id="b1", chapter_id=chapter_id,
                             ordinal=ordinal, kind="paragraph", text=text))
        db.add(ScriptBook(id="b1", title="Крылья полумрака", source_filename="k.txt",
                          source_format="txt", created_at=utcnow_naive()))
        db.add(ScriptChapter(id=chapter_id, book_id="b1", chapter_index=4,
                             chapter_title="Глава 4. Проба пера", status="published"))
        db.add(AudioFile(id="a1", book_code="КП", original_filename="d.wav", stored_key="k/d.wav",
                         mime_type="audio/wav", size_bytes=10, chapter="Глава 4. Проба пера",
                         role="Дгарнин", actor_name="Сергей Зотов", kind="take"))
        for segment_ordinal, (start, end) in [(0, (21, 38)), (1, (15, 27))]:
            db.add(V2Attribution(id=str(uuid.uuid4()), segment_id=f"{chapter_id}:{segment_ordinal:05d}",
                                 version=1, speaker="Дгарнин", span_start=start, span_end=end,
                                 source="llm", created_at=utcnow_naive()))
        db.commit()
        yield db


def _heard(*texts, gap: float = 0.5):
    """Подмена распознавания: каждый текст — свой сегмент со словными таймингами."""
    segments, clock = [], 0.0
    for text in texts:
        words, cursor = [], clock
        for word in text.split():
            words.append({"word": word, "start": cursor, "end": cursor + 0.4})
            cursor += 0.5
        segments.append({"text": text, "start": clock, "end": cursor, "words": words})
        clock = cursor + gap
    return lambda path: {"text": " ".join(texts), "segments": segments}


class TestWhatTheRobotHeardIsKept:
    def test_the_job_keeps_the_transcript_verbatim(self, studio):
        """Услышанное ложится в базу тем же, чем пришло: сегменты и слова со временем."""
        from app.services.asr_run import run_asr_for_take

        transcribe = _heard("Я не пойду туда", "Там холодно")
        job = run_asr_for_take(studio, "a1", transcribe=transcribe)

        assert json.loads(job.heard_json) == transcribe("любой путь")


class TestRecognisingWithoutTellingTheActor:
    """Массовое перераспознавание архива — работа по нашей инициативе, не по диктора.

    Обычный приём дубля обязан сообщить о пропусках сразу: диктор ещё у микрофона.
    Но когда мы сами прогоняем заново то, что он прислал недели назад, письмо «у вас
    пропущены реплики» — неправда о его работе и повод перезаписывать уже сданное.
    """

    def test_a_silent_run_tells_the_actor_nothing(self, studio, monkeypatch):
        from app.services import asr_notice
        from app.services.asr_run import run_asr_for_take

        sent = []
        monkeypatch.setattr(asr_notice, "notify_missing_lines", lambda *a, **kw: sent.append(kw))

        job = run_asr_for_take(studio, "a1", transcribe=_heard("Я не пойду туда"), notify=False)

        assert json.loads(job.alignment_json)["missing"] == [1]
        assert sent == []

    def test_an_ordinary_take_still_tells_the_actor(self, studio, monkeypatch):
        """Тишина — только по явной просьбе: обычный приём дубля по-прежнему пишет диктору."""
        from app.services import asr_notice
        from app.services.asr_run import run_asr_for_take

        sent = []
        monkeypatch.setattr(asr_notice, "notify_missing_lines", lambda *a, **kw: sent.append(kw))

        run_asr_for_take(studio, "a1", transcribe=_heard("Я не пойду туда"))

        assert len(sent) == 1


class TestReplayingTheAlignment:
    """Пересчёт из сохранённого — не «почти то же», а ровно то же.

    Это главный инвариант всей затеи: если пересчёт расходится с тем, что посчитала
    сама джоба, значит сохранено не то, чем считали, и доверять переигранному архиву
    нельзя.
    """

    def test_a_replay_reproduces_what_the_job_itself_computed(self, studio):
        from app.services.asr_replay import realign_job
        from app.services.asr_run import run_asr_for_take

        job = run_asr_for_take(studio, "a1", transcribe=_heard("Я не пойду туда", "Там холодно"))
        was = job.alignment_json

        report = realign_job(studio, job.id)

        assert job.alignment_json == was
        assert report["changed"] is False

    def test_a_stale_alignment_is_brought_up_to_date(self, studio):
        """Ради этого всё и делается: итог, посчитанный старыми правилами, переписывается."""
        from app.services.asr_replay import realign_job
        from app.services.asr_run import run_asr_for_take

        job = run_asr_for_take(studio, "a1", transcribe=_heard("Я не пойду туда", "Там холодно"))
        # Как будто сверку считали прежними правилами и она нашла только половину.
        job.alignment_json = json.dumps({"coverage": 0.5, "missing": [1], "lines": []})
        job.coverage = 0.5
        studio.flush()

        report = realign_job(studio, job.id)

        assert report["changed"] is True
        assert report["before"]["missing"] == 1
        assert report["after"]["missing"] == 0
        assert job.coverage == pytest.approx(1.0)
        assert json.loads(job.alignment_json)["missing"] == []

    def test_a_job_from_before_the_column_is_left_alone(self, studio):
        """У старой джобы вход выброшен — пересчитывать нечем, и это не ошибка прогона."""
        from app.services.asr_replay import realign_job
        from app.services.asr_run import run_asr_for_take

        job = run_asr_for_take(studio, "a1", transcribe=_heard("Я не пойду туда"))
        job.heard_json = ""
        was = job.alignment_json
        studio.flush()

        assert realign_job(studio, job.id) is None
        assert job.alignment_json == was

    def test_a_dry_run_reports_without_writing(self, studio):
        """Холостой прогон показывает последствия правки до того, как она тронет архив."""
        from app.services.asr_replay import realign_job
        from app.services.asr_run import run_asr_for_take

        job = run_asr_for_take(studio, "a1", transcribe=_heard("Я не пойду туда", "Там холодно"))
        job.alignment_json = json.dumps({"coverage": 0.5, "missing": [1], "lines": []})
        was = job.alignment_json
        studio.flush()

        report = realign_job(studio, job.id, write=False)

        assert report["after"]["missing"] == 0
        assert job.alignment_json == was

    def test_a_replay_says_nothing_to_the_actor(self, studio, monkeypatch):
        """Пересчёт не событие записи: письмо о пропусках диктор получил тогда, когда прислал дубль."""
        from app.services import asr_notice
        from app.services.asr_replay import realign_job
        from app.services.asr_run import run_asr_for_take

        job = run_asr_for_take(studio, "a1", transcribe=_heard("Я не пойду туда"))
        sent = []
        monkeypatch.setattr(asr_notice, "notify_missing_lines", lambda *a, **kw: sent.append(kw))

        realign_job(studio, job.id)

        assert sent == []


class TestRealignChapterAfterAMove:
    @pytest.fixture()
    def db(self):
        import app.models  # noqa: F401

        engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(engine)
        with sessionmaker(bind=engine)() as session:
            yield session

    LINES = [("Тупуг", "Мы пойдём на север через перевал."),
             ("Гамук", "Там нас давно ждут старые друзья."),
             ("Тупуг", "Я знаю короткую дорогу к реке.")]

    @pytest.fixture()
    def letters(self, monkeypatch):
        from app.services import asr_notice
        box = []
        monkeypatch.setattr(asr_notice, "resolve_actor_candidates", lambda db, name: [(f"chat:{name}", name)])
        monkeypatch.setattr(asr_notice, "send_telegram_message",
                            lambda db, text, chat_ids=None, **k: box.append((chat_ids, text)) or 1)
        return box

    def test_same_actor_move_closes_the_gap_and_sends_nothing(self, db, letters):
        from tests.asr_studio import add_take, make_chapter, move_line
        from app.services.asr_replay import realign_chapter

        chapter = make_chapter(db, self.LINES, {"Тупуг": "Гончаров Иван", "Гамук": "Гончаров Иван"})
        add_take(db, chapter, role="Тупуг", actor="Гончаров Иван",
                 said=["Мы пойдём на север через перевал.", "Я знаю короткую дорогу к реке."])
        gamuk = add_take(db, chapter, role="Гамук", actor="Гончаров Иван", said=["Там нас давно ждут старые друзья."])
        move_line(db, chapter, 2, "Гамук")

        report = realign_chapter(db, chapter)

        assert gamuk.coverage == 1.0
        assert report["borrowed"] == 1 and report["new_missing"] == {} and letters == []
        assert report["changed"] is True

    def test_other_actor_gets_one_letter_with_only_the_moved_line(self, db, letters):
        from tests.asr_studio import add_take, make_chapter, move_line
        from app.services.asr_replay import realign_chapter

        chapter = make_chapter(db, self.LINES + [("Гамук", "Эта реплика так и не прозвучала.")],
                               {"Тупуг": "Гончаров Иван", "Гамук": "Иванов Пётр"})
        add_take(db, chapter, role="Тупуг", actor="Гончаров Иван",
                 said=["Мы пойдём на север через перевал.", "Я знаю короткую дорогу к реке."])
        add_take(db, chapter, role="Гамук", actor="Иванов Пётр", said=["Там нас давно ждут старые друзья."])
        move_line(db, chapter, 2, "Гамук")

        report = realign_chapter(db, chapter)

        assert report["new_missing"] == {"Гамук": ["Я знаю короткую дорогу к реке."]}
        assert len(letters) == 1
        chat_ids, text = letters[0]
        assert chat_ids == ["chat:Иванов Пётр"]
        assert "Я знаю короткую дорогу к реке." in text
        assert "так и не прозвучала" not in text  # старый пропуск — не новость

    def test_a_second_pass_sends_nothing_new(self, db, letters):
        from tests.asr_studio import add_take, make_chapter, move_line
        from app.services.asr_replay import realign_chapter

        chapter = make_chapter(db, self.LINES, {"Тупуг": "Гончаров Иван", "Гамук": "Иванов Пётр"})
        add_take(db, chapter, role="Тупуг", actor="Гончаров Иван",
                 said=["Мы пойдём на север через перевал.", "Я знаю короткую дорогу к реке."])
        add_take(db, chapter, role="Гамук", actor="Иванов Пётр", said=["Там нас давно ждут старые друзья."])
        move_line(db, chapter, 2, "Гамук")
        realign_chapter(db, chapter)

        again = realign_chapter(db, chapter)

        assert again["new_missing"] == {} and len(letters) == 1 and again["changed"] is False

    def test_notify_false_sends_nothing(self, db, letters):
        from tests.asr_studio import add_take, make_chapter, move_line
        from app.services.asr_replay import realign_chapter

        chapter = make_chapter(db, self.LINES, {"Тупуг": "Гончаров Иван", "Гамук": "Иванов Пётр"})
        add_take(db, chapter, role="Тупуг", actor="Гончаров Иван",
                 said=["Мы пойдём на север через перевал.", "Я знаю короткую дорогу к реке."])
        add_take(db, chapter, role="Гамук", actor="Иванов Пётр", said=["Там нас давно ждут старые друзья."])
        move_line(db, chapter, 2, "Гамук")

        report = realign_chapter(db, chapter, notify=False)

        assert report["new_missing"] == {"Гамук": ["Я знаю короткую дорогу к реке."]} and letters == []

    def test_notify_false_returns_the_letters_and_the_helper_sends_them(self, db, letters):
        from tests.asr_studio import add_take, make_chapter, move_line
        from app.services.asr_replay import realign_chapter, send_moved_letters

        chapter = make_chapter(db, self.LINES, {"Тупуг": "Гончаров Иван", "Гамук": "Иванов Пётр"})
        add_take(db, chapter, role="Тупуг", actor="Гончаров Иван",
                 said=["Мы пойдём на север через перевал.", "Я знаю короткую дорогу к реке."])
        add_take(db, chapter, role="Гамук", actor="Иванов Пётр", said=["Там нас давно ждут старые друзья."])
        move_line(db, chapter, 2, "Гамук")

        report = realign_chapter(db, chapter, notify=False)
        assert letters == [] and len(report["pending_letters"]) == 1

        sent = send_moved_letters(db, report["pending_letters"])

        assert len(sent) == 1 and sent[0]["notified"] is True
        assert letters[0][0] == ["chat:Иванов Пётр"] and "Я знаю короткую дорогу к реке." in letters[0][1]

    def test_a_job_from_before_heard_json_is_reported_skipped_not_a_gap(self, db, letters):
        from tests.asr_studio import add_take, make_chapter, move_line
        from app.services.asr_replay import realign_chapter

        chapter = make_chapter(db, self.LINES, {"Тупуг": "Гончаров Иван", "Гамук": "Иванов Пётр"})
        add_take(db, chapter, role="Тупуг", actor="Гончаров Иван",
                 said=["Мы пойдём на север через перевал.", "Я знаю короткую дорогу к реке."])
        gamuk = add_take(db, chapter, role="Гамук", actor="Иванов Пётр", said=["Там нас давно ждут старые друзья."])
        gamuk.heard_json = ""
        db.flush()
        move_line(db, chapter, 2, "Гамук")

        report = realign_chapter(db, chapter)

        assert report["jobs"] == 2
        assert report["skipped"] == 1

    def test_a_changed_realign_marks_an_archived_session_outdated(self, db, letters):
        from app.models import ScriptChapter
        from app.services.asr_replay import realign_chapter
        from app.time_utils import utcnow_naive
        from tests.asr_studio import add_take, make_chapter, move_line

        chapter = make_chapter(db, self.LINES, {"Тупуг": "Гончаров Иван", "Гамук": "Иванов Пётр"})
        add_take(db, chapter, role="Тупуг", actor="Гончаров Иван",
                 said=["Мы пойдём на север через перевал.", "Я знаю короткую дорогу к реке."])
        add_take(db, chapter, role="Гамук", actor="Иванов Пётр", said=["Там нас давно ждут старые друзья."])
        db.get(ScriptChapter, chapter).session_archived_at = utcnow_naive()
        move_line(db, chapter, 2, "Гамук")

        realign_chapter(db, chapter, notify=False)

        assert db.get(ScriptChapter, chapter).session_outdated_at is not None


class TestEnqueueRealignForChapter:
    """Постановка в очередь — без дедупликации по ключу главы."""

    def test_it_queues_high_with_the_realign_task_and_no_dedup(self, monkeypatch):
        from app.services import asr_replay

        calls = []

        def _fake_enqueue(**kwargs):
            calls.append(kwargs)
            return "job-1"

        monkeypatch.setattr("app.workers.launcher.enqueue_tracked_task", _fake_enqueue)

        job_id = asr_replay.enqueue_realign_for_chapter("ch-11")

        assert job_id == "job-1"
        assert len(calls) == 1
        kwargs = calls[0]
        assert kwargs["queue_name"] == "high"
        assert kwargs["func_ref"] == "app.worker_tasks.perform_realign_chapter_task"
        assert kwargs["dedupe"] is False


class TestRealignTaskSendsAfterTheCommit:
    @pytest.fixture()
    def events(self, monkeypatch):
        import app.worker_tasks as worker_tasks
        from app.services import asr_replay

        class Events(list):
            fake = None

        box = Events()

        class FakeDb:
            fail_commit = False

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def commit(self):
                if FakeDb.fail_commit:
                    raise RuntimeError("database is locked")
                box.append("commit")

        box.fake = FakeDb
        monkeypatch.setattr(worker_tasks, "SessionLocal", FakeDb)
        monkeypatch.setattr(worker_tasks, "_start_background_run", lambda *a, **k: None)
        monkeypatch.setattr(worker_tasks, "_finish_background_run", lambda *a, **k: None)
        monkeypatch.setattr(worker_tasks, "_fail_background_run", lambda *a, **k: None)
        letter = {"actor_name": "Иванов Пётр", "role": "Гамук", "chapter": "Г", "book_title": "К",
                  "book_id": "b1", "texts": ["Я знаю короткую дорогу к реке."]}
        monkeypatch.setattr(asr_replay, "realign_chapter",
                            lambda db, chapter_id, notify=True: box.append(("realign", notify)) or {
                                "borrowed": 0, "new_missing": {"Гамук": letter["texts"]}, "notified": [],
                                "pending_letters": [letter]})
        monkeypatch.setattr(asr_replay, "send_moved_letters",
                            lambda db, pending: box.append(("send", len(pending))) or [])
        return box

    def test_letters_go_out_only_after_the_commit(self, events):
        from app.worker_tasks import perform_realign_chapter_task

        perform_realign_chapter_task("ch-11", run_id="r1")

        assert events == [("realign", False), "commit", ("send", 1)]

    def test_a_failed_commit_sends_nothing(self, events):
        from app.worker_tasks import perform_realign_chapter_task

        events.fake.fail_commit = True
        with pytest.raises(RuntimeError):
            perform_realign_chapter_task("ch-11", run_id="r1")

        assert ("send", 1) not in events

"""Дубль из бота в воркере: распознать раз, разложить по главам, сверить куски, отчитаться
(спека 2026-10-28-bot-takes, часть 3, «Обработка»)."""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import wave
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import AsrJob, AudioFile, AuditLog, BotUpload, Character, ScriptBook, ScriptChapter
from app.services import asr_replay, asr_run, audio_integrity, audio_storage, bot_takes
from app.services.role_deadlines import role_progress
from app.services.take_split import slice_transcript

#: настоящая функция — до подмены в фикстуре
REAL_EXPECTED_LINES = asr_run.expected_lines

NOW = datetime(2026, 10, 28, 12, 0, 0)
FIXTURE = Path(__file__).parent / "fixtures" / "asr" / "role_six_chapters.json"
DATA = json.loads(FIXTURE.read_text(encoding="utf-8"))
DURATION = 434.31
CHAPTERS = [1, 12, 14, 34, 47, 59]


def _wav(path: Path, seconds: float, rate: int = 8000) -> Path:
    """Тишина нужной длины: резке и пробе ffprobe этого достаточно."""
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(b"\0\0" * int(seconds * rate))
    return path


def _md5(path) -> str:
    return hashlib.md5(Path(path).read_bytes()).hexdigest()


@pytest.fixture()
def lines():
    """Реплики роли по главам — как их отдал бы `expected_lines`; тест может дописать."""
    return {f"ch{key}": list(value) for key, value in DATA["chapters"].items()}


@pytest.fixture()
def db(monkeypatch, tmp_path, lines):
    monkeypatch.setattr(audio_storage.settings, "audio_storage_path", str(tmp_path / "rec"))
    fake = lambda db, chapter_id, role: list(lines.get(chapter_id, []))  # noqa: E731
    # `asr_replay` берёт `expected_lines` по имени при импорте — подменяем у обоих, иначе
    # пересчёт сверки (`run_asr_for_chapter`) видел бы настоящие реплики, а не фикстуру.
    monkeypatch.setattr(asr_run, "expected_lines", fake)
    monkeypatch.setattr(asr_replay, "expected_lines", fake)
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, autoflush=False)() as session:
        session.add(ScriptBook(id="b1", title="Карта Приозерья", display_title="Карта Приозерья",
                               source_filename="x", source_format="docx", total_chars=0, author_sheets_x1000=0,
                               chapter_count=60, has_chapters="true", status="processing"))
        for index in CHAPTERS:
            session.add(ScriptChapter(id=f"ch{index}", book_id="b1", chapter_index=index,
                                      chapter_title=f"Глава {index}"))
        session.add(Character(id="c1", book_id="b1", name="Юмкуба", actor_name="Ветрова Ольга",
                              appears_in=",".join(str(i) for i in CHAPTERS)))
        session.commit()
        _row(session)
        yield session


def _row(db, *, upload_id="up1", file_name="Yumkuba_vse.wav", unique="U1", created=NOW, state="queued"):
    db.add(BotUpload(id=upload_id, telegram_user_id="41", file_id="F" + upload_id, file_unique_id=unique,
                     kind="document", mime_type="audio/wav", file_name=file_name, file_size=1, duration=0,
                     character_id="c1", actor_name="Ветрова Ольга", purpose="take", role_name="Юмкуба",
                     book_id="b1", state=state, created_at=created, updated_at=created))
    db.commit()


def _fetch(path):
    @contextlib.contextmanager
    def fetch(file_id):
        with open(path, "rb") as handle:
            yield handle
    return fetch


class Run:
    """Один вызов `process` с подменами: распознавание считает вызовы, Telegram — списки."""

    def __init__(self, db, tmp_path, source, heard=None):
        self.db = db
        self.tmp_path = tmp_path
        self.source = source
        self.heard = heard if heard is not None else {"text": "", "segments": DATA["segments"]}
        self.calls = 0
        self.dictor: list[tuple[str, str]] = []
        self.owner: list[str] = []

    def transcribe(self, path):
        self.calls += 1
        if isinstance(self.heard, Exception):
            raise self.heard
        return self.heard

    def __call__(self, upload_id="up1"):
        return bot_takes.process(
            self.db, upload_id, fetch=_fetch(self.source), transcribe_part=self.transcribe,
            send_text=lambda chat, text: self.dictor.append((chat, text)) or True,
            notify_owner=lambda text: self.owner.append(text) or True,
            workdir=str(self.tmp_path / "work" / upload_id), now=NOW,
        )


@pytest.fixture()
def whole(tmp_path):
    return _wav(tmp_path / "Yumkuba_vse.wav", DURATION)


@pytest.fixture(autouse=True)
def _verify_queue(monkeypatch):
    queued: list[str] = []
    monkeypatch.setattr(audio_integrity, "enqueue_verify_for_chapter", lambda chapter_id: queued.append(chapter_id))
    return queued


def test_whole_role_becomes_six_takes(db, tmp_path, whole):
    run = Run(db, tmp_path, whole)
    assert run() == "done"
    takes = db.query(AudioFile).order_by(AudioFile.chapter).all()
    assert len(takes) == 6
    assert {take.book_code for take in takes} == {"КП"}
    assert sorted(take.chapter for take in takes) == sorted(f"Глава {i}" for i in CHAPTERS)
    assert {take.role for take in takes} == {"Юмкуба"} and {take.actor_name for take in takes} == {"Ветрова Ольга"}
    assert {take.kind for take in takes} == {"take"}
    assert role_progress(db, db.get(Character, "c1"), "Ветрова Ольга") == (6, 6)
    # Распознали один раз за весь файл, а каждый кусок сверен без второй оплаты.
    assert run.calls == 1
    jobs = db.query(AsrJob).all()
    assert len(jobs) == 6 and {job.status for job in jobs} == {"done"}
    row = db.get(BotUpload, "up1")
    assert row.state == "done" and row.md5 == _md5(whole)
    result = json.loads(row.result_json)
    assert sorted(result["audio_ids"]) == sorted(take.id for take in takes)
    assert [item["index"] for item in result["chapters"]] == CHAPTERS
    assert db.query(AuditLog).filter(AuditLog.action == "bot_take_stored", AuditLog.entity_id == "up1").count() == 1
    # Диктору — один отчёт, владельцу — одна сводка.
    assert len(run.dictor) == 1 and run.dictor[0][0] == "41"
    assert run.dictor[0][1].startswith("Принято — «Юмкуба», «Карта Приозерья», 6 глав:")
    assert len(run.owner) == 1 and "Ветрова Ольга" in run.owner[0] and "Юмкуба" in run.owner[0]
    # Временные файлы убраны: обработка закончена.
    assert not (tmp_path / "work" / "up1").exists()


def test_pieces_follow_the_chapter_blocks(db, tmp_path, whole):
    Run(db, tmp_path, whole)()
    by_chapter = {take.chapter: take for take in db.query(AudioFile).all()}
    assert abs(by_chapter["Глава 1"].duration_seconds - 22.2) < 1.5
    assert abs(sum(take.duration_seconds for take in by_chapter.values()) - DURATION) < 1.0


def test_report_lists_missing_lines(db, tmp_path, whole, lines):
    lines["ch47"].append("Этой реплики диктор не читал вовсе, её нет в записи")
    run = Run(db, tmp_path, whole)
    assert run() == "done"
    text = run.dictor[0][1]
    assert "Этой реплики диктор не читал вовсе" in text
    assert "гл. 47 — " in text and " из 37, не нашли: «" in text
    assert text.rstrip().endswith("Если реплика записана, но не нашлась, ничего делать не нужно — студия проверит.")
    assert "гл. 47" in run.owner[0]


def test_take_report_wording():
    text = bot_takes.take_report("Юмкуба", "Карта Приозерья", [
        {"index": 1, "found": 1, "total": 1, "missing": []},
        {"index": 12, "found": 3, "total": 3, "missing": []},
        {"index": 14, "found": 21, "total": 21, "missing": []},
        {"index": 47, "found": 29, "total": 36, "missing": [f"реплика {n}" for n in range(1, 8)]},
    ])
    assert text.splitlines() == [
        "Принято — «Юмкуба», «Карта Приозерья», 4 главы:",
        "гл. 1 — единственная реплика",
        "гл. 12 — все 3 реплики",
        "гл. 14 — все 21 реплика",
        "гл. 47 — 29 из 36, не нашли: «реплика 1», «реплика 2», «реплика 3», «реплика 4», «реплика 5» "
        "и ещё 2",
        "Если реплика записана, но не нашлась, ничего делать не нужно — студия проверит.",
    ]
    one = bot_takes.take_report("Юмкуба", "КП", [{"index": 5, "found": 7, "total": 7, "missing": []}])
    assert one.splitlines() == ["Принято — «Юмкуба», «КП», 1 глава:", "гл. 5 — все 7 реплик"]
    many = bot_takes.take_report("Юмкуба", "КП", [{"index": i, "found": 2, "total": 2, "missing": []}
                                                  for i in range(5)])
    assert many.splitlines()[0] == "Принято — «Юмкуба», «КП», 5 глав:"


def test_single_chapter_file_is_stored_uncut(db, tmp_path):
    # Гл. 12 у «юмкуби» — между 22,2 и 62,0 с записи целиком.
    source = _wav(tmp_path / "KP_Yumkuba_12.wav", 39.8)
    heard = slice_transcript(DATA["segments"], 22.2, 62.0)
    run = Run(db, tmp_path, source, heard=heard)
    assert run() == "done"
    take = db.query(AudioFile).one()
    assert take.chapter == "Глава 12" and take.md5 == _md5(source)
    assert take.original_filename == "Yumkuba_vse.wav"


def test_name_mismatch_is_told_to_the_owner(db, tmp_path):
    source = _wav(tmp_path / "x.wav", 39.8)
    _row(db, upload_id="up2", file_name="KP_Ch55_Yumkuba.wav", unique="U2")
    run = Run(db, tmp_path, source, heard=slice_transcript(DATA["segments"], 22.2, 62.0))
    assert run("up2") == "done"
    assert db.query(AudioFile).one().chapter == "Глава 12"
    assert "В имени файла глава 55, а по записи — глава 12; положил по записи." in run.owner[0]


def test_nothing_found_goes_to_unsorted_not_audition(db, tmp_path, whole):
    run = Run(db, tmp_path, whole, heard={"text": "", "segments": []})
    assert run() == "unsorted"
    assert db.query(AudioFile).count() == 0
    row = db.get(BotUpload, "up1")
    assert row.state == "unsorted"
    assert row.stored_key == "KP/Unsorted/up1_Yumkuba_vse.wav"
    stored = audio_storage.resolve_path(row.stored_key)
    assert os.path.exists(stored) and _md5(stored) == _md5(whole)
    assert run.dictor == [("41", "Не нашёл в файле реплик роли «Юмкуба». Передал студии, с вами свяжутся.")]
    assert len(run.owner) == 1 and row.stored_key in run.owner[0] and "Ветрова Ольга" in run.owner[0]
    # Копия файла убрана (он в «не разобрано»), оплаченное услышанное — осталось.
    assert os.listdir(tmp_path / "work" / "up1") == ["heard.json"]


def test_failure_after_download_keeps_file(db, tmp_path, whole):
    run = Run(db, tmp_path, whole, heard=RuntimeError("azure 500"))
    assert run() == "unsorted"
    row = db.get(BotUpload, "up1")
    assert row.state == "unsorted" and _md5(audio_storage.resolve_path(row.stored_key)) == _md5(whole)
    assert run.dictor == [("41", "Не получилось обработать файл — студия разберётся, присылать заново не нужно.")]
    assert "azure 500" in run.owner[0] and row.stored_key in run.owner[0]
    assert db.query(AudioFile).count() == 0


def test_not_an_audio_file_goes_to_unsorted(db, tmp_path):
    junk = tmp_path / "notes.wav"
    junk.write_bytes(b"this is not audio at all" * 100)
    run = Run(db, tmp_path, junk)
    assert run() == "unsorted"
    assert run.calls == 0  # за распознавание мусора не платим
    assert db.get(BotUpload, "up1").state == "unsorted"


def test_chapters_out_of_order_go_to_unsorted(db, tmp_path, whole, monkeypatch):
    from app.services.take_split import OutOfOrder

    def raise_out_of_order(*args, **kwargs):
        raise OutOfOrder("глава 14 начинается раньше, чем кончается глава 12")
    monkeypatch.setattr("app.services.take_split.plan_cuts", raise_out_of_order)
    run = Run(db, tmp_path, whole)
    assert run() == "unsorted"
    assert db.query(AudioFile).count() == 0
    assert "не по порядку" in run.owner[0]
    # Реплики в файле есть — диктору нельзя сказать, что их не нашли.
    assert run.dictor == [("41", "Не получилось обработать файл — студия разберётся, присылать заново не нужно.")]


def test_unsorted_write_failure_is_failed(db, tmp_path, whole, monkeypatch):
    monkeypatch.setattr(audio_storage, "write_stream", lambda key, source: (_ for _ in ()).throw(OSError("disk")))
    run = Run(db, tmp_path, whole, heard={"text": "", "segments": []})
    assert run() == "failed"
    assert db.get(BotUpload, "up1").state == "failed"
    assert "disk" in run.owner[0]


def test_download_failure_is_failed(db, tmp_path):
    run = Run(db, tmp_path, tmp_path / "missing.wav")
    assert run() == "failed"
    assert db.get(BotUpload, "up1").state == "failed"
    assert run.calls == 0 and len(run.owner) == 1
    assert run.dictor == [("41", "Не получилось обработать файл — студия разберётся, присылать заново не нужно.")]


def test_recognition_failure_keeps_paid_parts(db, tmp_path, whole, monkeypatch):
    """Упавшее распознавание длинной записи: уже оплаченные части остаются для повтора."""
    def fake_whole(path, *, transcribe_part, workdir, duration):
        Path(workdir, "part-0.json").write_text("{}", encoding="utf-8")
        raise RuntimeError("часть 2 упала")
    monkeypatch.setattr(bot_takes, "transcribe_whole", fake_whole)
    run = Run(db, tmp_path, whole)
    assert run() == "unsorted"
    work = tmp_path / "work" / "up1"
    assert sorted(os.listdir(work)) == ["part-0.json"]


def test_second_process_call_is_skip(db, tmp_path, whole):
    run = Run(db, tmp_path, whole)
    assert run() == "done"
    assert run() == "skip"
    assert run.calls == 1 and db.query(AudioFile).count() == 6


def test_same_file_twice_is_not_paid_twice(db, tmp_path, whole):
    """Гонка двух приёмов одного файла (ревью задачи 7): второй не распознаётся."""
    run = Run(db, tmp_path, whole)
    _row(db, upload_id="up2", unique="U1", created=NOW + timedelta(minutes=1))
    assert run("up1") == "done"
    assert run("up2") == "skip"
    assert run.calls == 1
    assert db.get(BotUpload, "up2").state == "cancelled"
    assert run.dictor[-1] == ("41", "Этот файл уже принят 28.10.")


def test_same_content_by_md5_is_not_paid_twice(db, tmp_path, whole):
    run = Run(db, tmp_path, whole)
    _row(db, upload_id="up2", unique="OTHER", created=NOW + timedelta(minutes=1))
    assert run("up1") == "done"
    assert run("up2") == "skip"
    assert run.calls == 1 and db.get(BotUpload, "up2").state == "cancelled"
    assert run.dictor[-1] == ("41", "Этот файл уже принят 28.10.")


def test_no_missing_line_letters(db, tmp_path, whole, lines, monkeypatch):
    lines["ch47"].append("Этой реплики диктор не читал вовсе, её нет в записи")
    letters = []
    monkeypatch.setattr(asr_run, "notify_take_missing", lambda db, job: letters.append(job))
    from app.services import asr_notice
    monkeypatch.setattr(asr_notice, "notify_missing_lines", lambda db, **kw: letters.append(kw))
    assert Run(db, tmp_path, whole)() == "done"
    assert letters == []


def test_chapter_asr_later_does_not_rerecognise(db, tmp_path, whole):
    assert Run(db, tmp_path, whole)() == "done"

    def boom(path):
        raise AssertionError("второе платное распознавание")

    for index in CHAPTERS:
        result = asr_run.run_asr_for_chapter(db, f"ch{index}", transcribe=boom)
        assert result["failed"] == 0 and result["done"] == 0 and result["realigned"] == 1


def test_chapter_steps_run_after_takes(db, tmp_path, whole, monkeypatch, _verify_queue):
    seen = []
    monkeypatch.setattr(asr_run, "after_new_takes",
                        lambda db, chapter, *, take_ids, **kw: seen.append((chapter.id, sorted(take_ids))))
    monkeypatch.setattr(audio_integrity, "chapter_needs_verification", lambda db, chapter_id: True)
    assert Run(db, tmp_path, whole)() == "done"
    assert [chapter_id for chapter_id, _ in seen] == [f"ch{i}" for i in CHAPTERS]
    assert all(len(ids) == 1 for _, ids in seen)
    assert _verify_queue == [f"ch{i}" for i in CHAPTERS]


def test_owner_summary_wording():
    text = bot_takes.owner_summary("Ветрова Ольга", "Юмкуба", [
        {"index": 1, "found": 5, "total": 5, "missing": []},
        {"index": 47, "found": 30, "total": 36, "missing": ["a"] * 6},
    ])
    assert "Ветрова Ольга" in text and "«Юмкуба»" in text and "главы 1, 47." in text
    assert "гл. 47 — 6" in text and "гл. 1 " not in text


def test_owner_summary_names_the_chapter_so_two_files_do_not_look_like_a_repeat():
    """29.10: Кораненко прислала четыре файла — главы 5, 9, 16, 18 — и владелец получил
    четыре одинаковых «Саяху», 1 глава»: без номера это выглядело как дубли сообщений."""
    one = bot_takes.owner_summary("Примершупядяобб Примерцвфмллрив", "Саяху", [{"index": 5, "found": 4, "total": 4, "missing": []}])
    other = bot_takes.owner_summary("Примершупядяобб Примерцвфмллрив", "Саяху", [{"index": 9, "found": 3, "total": 3, "missing": []}])
    assert "«Саяху», гл. 5." in one and "«Саяху», гл. 9." in other
    assert one != other


def test_worker_task_wires_direct_letters_and_a_stable_workdir(db, tmp_path, monkeypatch):
    """Воркер: диктору — `direct=True` (иначе отчёт уедет владельцу), владельцу — без него;
    каталог частей — постоянный для строки учёта, чтобы ручной повтор их нашёл."""
    import app.worker_tasks as worker_tasks
    from app.services import telegram

    @contextlib.contextmanager
    def session():
        yield db

    seen = {}

    def fake_process(db_, upload_id, **kw):
        seen.update(kw, upload_id=upload_id)
        kw["send_text"]("41", "отчёт")
        kw["notify_owner"]("сводка")
        return "done"

    sent = []
    monkeypatch.setattr(worker_tasks, "SessionLocal", session)
    monkeypatch.setattr(bot_takes, "process", fake_process)
    monkeypatch.setattr(telegram, "send_telegram_message",
                        lambda db_, text, chat_ids=None, direct=False, **kw: sent.append((text, chat_ids, direct)) or 1)
    worker_tasks.perform_bot_take_task("up1")
    assert seen["upload_id"] == "up1"
    assert seen["workdir"] == bot_takes.takes_workdir("up1")
    assert sent == [("отчёт", ["41"], True), ("сводка", None, False)]


# --- ревью, раунд 1 ------------------------------------------------------------------------------

def _requeue(db, upload_id="up1"):
    row = db.get(BotUpload, upload_id)
    row.state = "queued"
    db.commit()


def test_failure_after_recognition_keeps_heard_and_retry_does_not_pay(db, tmp_path, whole, monkeypatch):
    from app.services import audio_uploads

    real = audio_uploads.store_audio_file
    monkeypatch.setattr(audio_uploads, "store_audio_file",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("диск отвалился")))
    run = Run(db, tmp_path, whole)
    assert run() == "unsorted"
    assert (tmp_path / "work" / "up1" / "heard.json").exists()
    monkeypatch.setattr(audio_uploads, "store_audio_file", real)
    _requeue(db)
    assert run() == "done"
    assert run.calls == 1 and db.query(AudioFile).count() == 6


def _fail_on_chapter(monkeypatch, chapter_label):
    from app.services import audio_uploads

    real = audio_uploads.store_audio_file

    def store(db_, **kw):
        if kw["chapter"] == chapter_label:
            raise OSError("диск отвалился")
        return real(db_, **kw)
    monkeypatch.setattr(audio_uploads, "store_audio_file", store)
    return real


def test_partial_store_runs_chapter_steps_for_stored_pieces(db, tmp_path, whole, monkeypatch, _verify_queue):
    seen = []
    monkeypatch.setattr(asr_run, "after_new_takes", lambda db_, chapter, *, take_ids, **kw: seen.append(chapter.id))
    monkeypatch.setattr(audio_integrity, "chapter_needs_verification", lambda db_, chapter_id: True)
    _fail_on_chapter(monkeypatch, "Глава 34")
    run = Run(db, tmp_path, whole)
    assert run() == "unsorted"
    assert sorted(seen) == sorted(["ch1", "ch12", "ch14"]) and sorted(_verify_queue) == sorted(seen)
    stored = json.loads(db.get(BotUpload, "up1").result_json)["stored"]
    assert [item["chapter"] for item in stored] == [1, 12, 14]
    assert "гл. 1, 12, 14" in run.owner[0]
    assert all(item["audio_id"] in run.owner[0] for item in stored)


def test_retry_after_partial_store_does_not_duplicate(db, tmp_path, whole, monkeypatch):
    real = _fail_on_chapter(monkeypatch, "Глава 34")
    run = Run(db, tmp_path, whole)
    assert run() == "unsorted"
    first = {take.chapter: take.id for take in db.query(AudioFile).all()}
    assert len(first) == 3
    from app.services import audio_uploads
    monkeypatch.setattr(audio_uploads, "store_audio_file", real)
    _requeue(db)
    assert run() == "done"
    takes = db.query(AudioFile).all()
    assert len(takes) == 6 and len({take.chapter for take in takes}) == 6
    assert all(next(t.id for t in takes if t.chapter == label) == audio_id for label, audio_id in first.items())
    assert run.calls == 1
    assert run.dictor[-1][1].startswith("Принято — «Юмкуба», «Карта Приозерья», 6 глав:")


def test_unexpected_exception_does_not_leave_the_row_in_flight(db, tmp_path, whole, monkeypatch):
    monkeypatch.setattr(bot_takes, "_already_taken", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("база")))
    with pytest.raises(RuntimeError):
        Run(db, tmp_path, whole)()
    assert db.get(BotUpload, "up1").state == "failed"


def test_a_failing_commit_in_the_fail_path_still_marks_failed(db, tmp_path, monkeypatch):
    real_commit = db.commit
    raised = []

    def commit():
        # Первый коммит, несущий `failed` из `fail()`, падает — как упавшая база.
        if not raised and db.get(BotUpload, "up1").state == "failed":
            raised.append(1)
            raise RuntimeError("database is locked")
        real_commit()
    monkeypatch.setattr(db, "commit", commit)
    run = Run(db, tmp_path, tmp_path / "missing.wav")
    with pytest.raises(RuntimeError):
        run()
    assert raised
    db.rollback()
    assert db.get(BotUpload, "up1").state == "failed"


def test_claim_is_atomic(db, tmp_path, whole):
    """Второй запуск той же задачи, пока первый в работе, ничего не делает и не платит."""
    run = Run(db, tmp_path, whole)
    nested = []
    inner = run.transcribe

    def transcribe(path):
        if not nested:
            nested.append(bot_takes.process(
                db, "up1", fetch=_fetch(whole), transcribe_part=inner, send_text=lambda c, t: True,
                notify_owner=lambda t: True, workdir=str(tmp_path / "work" / "other"), now=NOW))
        return inner(path)
    run.transcribe = transcribe
    assert run() == "done"
    assert nested == ["skip"] and run.calls == 1


def test_fresh_processing_row_blocks_same_file_but_stale_does_not(db, tmp_path, whole):
    _row(db, upload_id="up0", unique="U1", created=NOW - timedelta(minutes=5), state="processing")
    run = Run(db, tmp_path, whole)
    assert run() == "skip" and run.calls == 0
    assert db.get(BotUpload, "up1").state == "cancelled"

    db.get(BotUpload, "up0").updated_at = NOW - timedelta(hours=7)
    db.get(BotUpload, "up1").state = "queued"
    db.commit()
    assert run() == "done" and run.calls == 1


def test_unsorted_with_heard_is_not_paid_again(db, tmp_path, whole):
    run = Run(db, tmp_path, whole, heard={"text": "", "segments": []})
    assert run() == "unsorted"
    _row(db, upload_id="up2", unique="OTHER", created=NOW + timedelta(minutes=1))
    assert run("up2") == "skip"
    assert run.calls == 1 and db.get(BotUpload, "up2").state == "cancelled"
    assert run.dictor[-1] == ("41", "Этот файл уже передан студии.")


def test_report_counts_paragraphs_not_pieces(db, tmp_path, lines, monkeypatch):
    """Абзац, разорванный словами автора, — одна реплика (ловушка «Реплика — это абзац»)."""
    import uuid

    from app.v2.models import V2Attribution, V2Segment

    torn = "— Ты уже минуту с чашкой сидишь, — сказала она. — Что-то случилось?"
    whole_line = "— Я сейчас."
    for ordinal, text in enumerate((torn, whole_line)):
        db.add(V2Segment(id=f"s{ordinal}", book_id="b1", chapter_id="ch1", ordinal=ordinal, kind="paragraph",
                         text=text, char_start=0, char_end=len(text)))
    cut1, cut2 = torn.index(", — сказала"), torn.index(" — Что-то")
    for segment_id, start, end, speaker in (("s0", 0, cut1, "Юмкуба"), ("s0", cut1, cut2, "Рассказчик"),
                                            ("s0", cut2, len(torn), "Юмкуба"), ("s1", 0, len(whole_line), "Юмкуба")):
        db.add(V2Attribution(id=str(uuid.uuid4()), segment_id=segment_id, span_start=start, span_end=end,
                             speaker=speaker, confidence=0.9, source="llm", version=1))
    db.commit()
    def expected(db_, chapter_id, role):
        # Настоящие реплики для гл. 1 (из разметки выше), фикстура — для остальных глав.
        if chapter_id == "ch1":
            return REAL_EXPECTED_LINES(db_, chapter_id, role)
        return list(lines.get(chapter_id, []))
    monkeypatch.setattr(asr_run, "expected_lines", expected)
    monkeypatch.setattr(asr_replay, "expected_lines", expected)
    assert len(expected(db, "ch1", "Юмкуба")) == 3  # три куска сверки — две реплики
    # Гл. 1 записана целиком: «Ты уже минуту…», «Что-то случилось?», «Я сейчас.» — две реплики, а не три.
    source = _wav(tmp_path / "ch1.wav", 22.2)
    run = Run(db, tmp_path, source, heard=slice_transcript(DATA["segments"], 0, 22.2))
    assert run() == "done"
    assert run.dictor[0][1].splitlines()[1] == "гл. 1 — все 2 реплики"


# --- ревью, раунд 2 ------------------------------------------------------------------------------

def test_long_run_keeps_its_row_alive(db, tmp_path, whole, monkeypatch):
    """Распознавание дольше STALE_AFTER: отметки после частей и глав держат строку «живой»,
    и тот же файл, присланный заново, не распознаётся второй раз."""
    clock = [NOW]
    _row(db, upload_id="up2", unique="U1", created=NOW + timedelta(minutes=1))
    run = Run(db, tmp_path, whole)
    inner = run.transcribe

    def slow(path):
        clock[0] += bot_takes.STALE_AFTER + timedelta(hours=1)
        return inner(path)

    seen = []
    real_beat = bot_takes._beat

    def beat(db_, upload_id, at):
        real_beat(db_, upload_id, at)
        seen.append(bot_takes._already_taken(db, db.get(BotUpload, "up2"), clock[0]) is not None)
    monkeypatch.setattr(bot_takes, "_beat", beat)
    assert bot_takes.process(
        db, "up1", fetch=_fetch(whole), transcribe_part=slow, send_text=lambda c, t: True,
        notify_owner=lambda t: True, workdir=str(tmp_path / "work" / "up1"), now=NOW,
        clock=lambda: clock[0]) == "done"
    assert len(seen) == 7 and all(seen)  # одна часть + шесть глав


def test_failed_row_with_heard_is_not_paid_again(db, tmp_path, whole, monkeypatch):
    from app.services import audio_storage as storage

    monkeypatch.setattr(storage, "write_stream", lambda key, source: (_ for _ in ()).throw(OSError("disk")))
    run = Run(db, tmp_path, whole, heard={"text": "", "segments": []})
    assert run() == "failed"
    assert (tmp_path / "work" / "up1" / "heard.json").exists()
    _row(db, upload_id="up2", unique="OTHER", created=NOW + timedelta(minutes=1))
    assert run("up2") == "skip"
    assert run.calls == 1 and run.dictor[-1] == ("41", "Этот файл уже передан студии.")


def test_retry_aligns_a_stored_take_whose_alignment_crashed(db, tmp_path, whole, monkeypatch):
    real = asr_run.run_asr_for_take
    crashed = []

    def flaky(db_, audio_id, **kw):
        if db_.get(AudioFile, audio_id).chapter == "Глава 34" and not crashed:
            crashed.append(audio_id)
            raise RuntimeError("сверка упала")
        return real(db_, audio_id, **kw)
    monkeypatch.setattr(asr_run, "run_asr_for_take", flaky)
    run = Run(db, tmp_path, whole)
    assert run() == "unsorted"
    assert db.query(AsrJob).filter(AsrJob.audio_file_id == crashed[0]).count() == 0
    _requeue(db)
    assert run() == "done"
    job = db.query(AsrJob).filter(AsrJob.audio_file_id == crashed[0]).one()
    assert job.status == "done" and run.calls == 1
    assert db.query(AudioFile).count() == 6


def test_workdir_is_outside_the_audio_root_that_gets_backed_up():
    from app.services.bot_takes import takes_workdir

    path = os.path.abspath(takes_workdir("up1"))
    root = os.path.abspath(audio_storage.local_root())
    assert os.path.commonpath([path, root]) != root
    assert os.sep + os.path.join("data", "spool", "bot_takes", "up1") in path

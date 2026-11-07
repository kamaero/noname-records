"""Strict narrator check: классификатор строк и прогон с подменённой сетью.




Проверяются синтетические примеры и поведение при сбоях.
"""
import json
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import AsrAudit, ScriptBook, ScriptChapter
from app.services import asr_audit
from app.services.asr_audit import classify_narrator_line, run_asr_audit
from app.v2.models import V2Attribution, V2Segment

BOOK = "book-asr-audit"


@pytest.mark.parametrize(("text", "expected"), [
    ("Chapter 12. The crossing", "title"), ("Chapter IV", "title"),
    ("Epilogue", "title"), ("said Aster.", "tag"), ("she whispered.", "tag"),
    ("Aster said a prayer.", "required"), ("Chapter closed the gate.", "required"),
    ("said Aster. Then she left.", "required"),
])
def test_english_audit_classification_keeps_events_required(text, expected):
    assert classify_narrator_line(text, language="en") == expected


def test_book_language_changes_both_audit_fingerprint_paths(db):
    chapter_id = _make_ready_chapter(db)
    before = asr_audit.narrator_input_fingerprint(db, chapter_id)
    db.get(ScriptBook, BOOK).language = "en"
    db.commit()
    after = asr_audit.narrator_input_fingerprint(db, chapter_id)
    assert after != before
    assert asr_audit._narrator_fingerprints(db, [chapter_id]) == {chapter_id: after}


def test_english_audit_freezes_language_for_first_and_confirmation_transcription(db, monkeypatch):
    chapter_id = _make_ready_chapter(db)
    db.get(ScriptBook, BOOK).language = "en"
    texts = ["There are 21 keys beside the gate.", "The captain waited beside the quiet bridge.",
             "said Aster.", "Chapter 12. The crossing"]
    for segment in db.query(V2Segment).filter(V2Segment.chapter_id == chapter_id):
        segment.text = texts[segment.ordinal]
        for attr in db.query(V2Attribution).filter(V2Attribution.segment_id == segment.id):
            attr.span_end = len(segment.text)
    db.commit()
    seen, systems = [], []
    def transcribe(path, *, model, phrases, language):
        seen.append(language)
        return _heard_with(texts[0], "The captain beside the quiet bridge."), .5
    def judge(system, user, schema):
        systems.append(system)
        if "results" in json.dumps(schema):
            return {"results": [{"index": 1, "confirmed": True, "note": "missing waited"}]}
        return {"verdicts": [{"index": 0, "status": "ok", "missing_words": [], "note": ""},
                             {"index": 1, "status": "partial", "missing_words": ["waited"], "note": "missing"}]}
    monkeypatch.setattr(asr_audit, "_transcribe_fresh", transcribe)
    monkeypatch.setattr(asr_audit, "_cut_window", lambda *a: "unused")
    audit = run_asr_audit(db, chapter_id, judge=judge)
    assert audit.status == "done" and audit.language == "en"
    assert seen == ["en", "en"]
    assert all("return all notes in English" in system for system in systems)
    assert json.loads(audit.lines_json)["language"] == "en"


def test_changed_queued_audit_language_stops_before_payment(db):
    chapter_id = _make_ready_chapter(db)
    audit = run_asr_audit(db, chapter_id, language="en",
                          transcribe=lambda path: pytest.fail("unexpected paid call"))
    assert audit.status == "skipped" and audit.error_message == "narrator_input_changed"

@pytest.fixture(autouse=True)
def audio_sources(tmp_path, monkeypatch):
    from app.config import settings
    root = tmp_path / "audio"
    (root / "k").mkdir(parents=True)
    (root / "k/take-r.wav").write_bytes(b"0" * 10)
    (root / "k/fix.wav").write_bytes(b"1" * 5)
    monkeypatch.setattr(settings, "audio_storage_path", str(root))
    return root


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    with sessionmaker(bind=engine)() as session:
        yield session


# ---------------------------------------------------------------------------
# Классификатор
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("Глава 3. Самое сложное", "title"),
        ("- сказал он.", "tag"),
        ("– улыбнулся персонаж.", "tag"),
        ("- тихо сказал персонаж, отодвинув стул.", "tag"),
        ("- раздался звонкий голосок.", "tag"),
        ("- сказал персонаж. Затем он ушёл.", "required"),   # вторая фраза — факт
        ("- сказал он, когда вошел гость.", "required"),     # событийная клауза
        ("- сказал он" + " очень" * 20 + ".", "required"),   # длинная тирада
        ("И герой сказал «нет».", "required"),             # без тире — не реприза
        ("", "required"),
        ("Обычное повествование без тире.", "required"),
    ],
)
def test_classifier_edges(text, kind):
    assert classify_narrator_line(text) == kind


# ---------------------------------------------------------------------------
# Прогон аудита с подменённой сетью
# ---------------------------------------------------------------------------

NARRATION_FULL = "На площади зажглись фонари, и ветер стих до самого утра."
NARRATION_PARTIAL = "На дороге наступила тишина, хотя впереди ещё много работы."
TAG_LINE = "- улыбнулся персонаж."
TITLE_LINE = "Глава 12. Дорога"


def _make_ready_chapter(db):
    from app.models import AudioFile, AsrJob, Character
    from app.time_utils import utcnow_naive

    db.add(ScriptBook(id=BOOK, title="Книга примеров", source_filename="k.txt",
                      source_format="txt", created_at=utcnow_naive()))
    chapter_id = "ch-12"
    db.add(ScriptChapter(id=chapter_id, book_id=BOOK, chapter_index=12,
                         chapter_title=TITLE_LINE, status="published"))
    db.add(Character(book_id=BOOK, name="Рассказчик", actor_name="Алексей Тестов"))
    lines = [NARRATION_FULL, NARRATION_PARTIAL, TAG_LINE, TITLE_LINE]
    for ordinal, text in enumerate(lines):
        seg = f"{chapter_id}:{ordinal:05d}"
        db.add(V2Segment(id=seg, book_id=BOOK, chapter_id=chapter_id, ordinal=ordinal,
                         kind="paragraph", text=text, char_start=0, char_end=len(text)))
        db.add(V2Attribution(id=str(uuid.uuid4()), segment_id=seg, span_start=0,
                             span_end=len(text), speaker="Рассказчик",
                             confidence=0.9, source="llm", version=1))
    db.add(AudioFile(id="take-r", book_code="КП", original_filename="r.wav",
                     stored_key="k/take-r.wav", canonical_filename="KP_Ch12_Rasskazchik.wav",
                     mime_type="audio/wav", size_bytes=10, chapter=TITLE_LINE,
                     role="Рассказчик", actor_name="Тестов Алексей", kind="take",
                     uploaded_at=utcnow_naive()))
    db.add(AsrJob(audio_file_id="take-r", chapter_id=chapter_id, status="done",
                 alignment_json=json.dumps({"total": 4, "matched": 2, "missing": [2, 3], "lines": [
                     {"index": 0, "text": NARRATION_FULL, "matched": True, "start": 0., "end": 5.5},
                     {"index": 1, "text": NARRATION_PARTIAL, "matched": True, "start": 6.5, "end": 11.5},
                     {"index": 2, "text": TAG_LINE, "matched": False},
                     {"index": 3, "text": TITLE_LINE, "matched": False},
                 ]})))
    db.flush()
    return chapter_id


def _heard_with(full: str, partial: str):
    """Транскрипт: первая строка целиком, вторая без слов «хотя» и «много»."""
    segments = []
    clock = 0.0
    for text in (full, partial.replace("хотя ", "").replace("много ", "")):
        words = []
        for word in text.split():
            words.append({"word": word, "start": clock, "end": clock + 0.4})
            clock += 0.5
        segments.append({"text": text, "start": clock - len(words) * 0.5, "end": clock, "words": words})
        clock += 1.0
    return {"text": " ".join(s["text"] for s in segments), "segments": segments}


def _judge(*, confirm: bool):
    """Один судья на оба вызова; решает по схеме, какой это проход."""
    def ask(system, user, schema):
        if "results" in json.dumps(schema):
            return {"results": [{"index": 1, "confirmed": confirm, "note": "тест"}]}
        return {"verdicts": [
            {"index": 0, "status": "ok", "missing_words": [], "note": ""},
            {"index": 1, "status": "partial", "missing_words": ["хотя", "много"], "note": "пропущено"},
        ]}
    return ask


def _spy_sends(monkeypatch):
    box = []
    from app.services import asr_notice, role_approval, telegram as telegram_module

    def spy(db, text, chat_ids=None, direct=False):
        box.append({"text": text, "chat_ids": chat_ids, "direct": direct})
        return 1

    # Три точки привязки имени: `asr_audit` и `asr_notice` импортируют функцию
    # по-разному (внутри функции / наверху модуля) — закрываем все, иначе письмо
    # уйдёт через неподменённую копию.
    monkeypatch.setattr(telegram_module, "send_telegram_message", spy)
    monkeypatch.setattr(asr_notice, "send_telegram_message", spy)
    monkeypatch.setattr(role_approval, "send_telegram_message", spy)
    return box


def _run_audit(db, monkeypatch, *, confirm: bool):
    from app.models import TelegramAuthAccount

    chapter_id = _make_ready_chapter(db)
    db.add(TelegramAuthAccount(telegram_user_id="111", role="dictor",
                               display_name="Тестов Алексей", is_active="true"))
    db.flush()
    box = _spy_sends(monkeypatch)
    # Вырез окна — настоящий ffmpeg над несуществующим файлом: в тесте он не нужен.
    monkeypatch.setattr(asr_audit, "_cut_window", lambda *a, **k: "/tmp/window.mp3")
    audit = run_asr_audit(
        db, chapter_id,
        transcribe=lambda path: (_heard_with(NARRATION_FULL, NARRATION_PARTIAL), 1.5),
        judge=_judge(confirm=confirm),
        confirm_transcribe=lambda path: ({"text": NARRATION_PARTIAL.replace("хотя ", "").replace("много ", ""), "segments": []}, 0.5),
    )
    db.flush()
    return audit, box


@pytest.mark.parametrize(("text", "expected"), [
    ("— сказал он.", "tag"),
    ("— спросила женщина, закрывая дверь.", "tag"),
    ("— ответил собеседник.", "tag"),
    ("— прошипела она.", "tag"),
    ("— кивнул проводник.", "tag"),
    ("— послышался голос.", "tag"),
    ("— сказал он, когда погас свет.", "required"),
    ("— сказала она. За дверью было тихо.", "required"),
    ("Персонаж ответил не сразу.", "required"),
    ("— Впереди тропа поворачивает к реке, и там мы остановимся до самого рассвета.", "required"),
    ("Глава 14. Начало пути", "title"),
    ("Chapter 3. The crossing", "title"),
])
def test_classifier_with_independent_synthetic_cases(text, expected):
    assert classify_narrator_line(text) == expected


def test_audit_done_flags_confirms_and_letters_both(db, monkeypatch):
    audit, box = _run_audit(db, monkeypatch, confirm=True)

    assert audit.status == "done"
    assert audit.stt_cost_rub == 2.0
    flags, confirmed_count = asr_audit._audit_findings(audit)
    assert len(flags) == 1 and flags[0]["status"] == "partial"
    assert flags[0]["confirmed"] is True
    assert confirmed_count == 1
    # Владельцу — отчёт, рассказчику — письмо (лично, direct).
    assert len(box) == 2
    owner_text = next(m["text"] for m in box if not m["direct"])
    assert "Strict narrator check" in owner_text
    assert "confirmed 1" in owner_text
    letter = next(m for m in box if m["direct"])
    assert letter["chat_ids"] == ["111"]
    assert "Lines flagged by the strict word-for-word check" in letter["text"]
    assert "2.00" in owner_text  # цена обоих прогонов STT названа


def test_audit_without_confirmation_sends_no_actor_letter(db, monkeypatch):
    audit, box = _run_audit(db, monkeypatch, confirm=False)

    flags, confirmed_count = asr_audit._audit_findings(audit)
    assert confirmed_count == 0 and flags[0]["confirmed"] is False
    # Письмо ушло только владельцу — рассказчика без подтверждения не обвиняем.
    assert len(box) == 1
    assert "unconfirmed" in box[0]["text"]


def test_audit_failed_keeps_error_and_notifies_owner(db, monkeypatch):
    chapter_id = _make_ready_chapter(db)
    box = _spy_sends(monkeypatch)

    def broken(path):
        raise RuntimeError("asr_http_503")

    audit = run_asr_audit(db, chapter_id, transcribe=broken)
    db.flush()

    assert audit.status == "failed"
    assert "asr_http_503" in audit.error_message
    assert len(box) == 1 and not box[0]["direct"]
    assert "Check incomplete" in box[0]["text"]


def test_audit_skipped_without_narrator_take(db, monkeypatch):
    from app.models import AudioFile
    from app.time_utils import utcnow_naive

    chapter_id = _make_ready_chapter(db)
    db.query(AudioFile).filter(AudioFile.id == "take-r").delete()
    db.add(AudioFile(id="take-other", book_code="КП", original_filename="d.wav",
                     stored_key="k/take-other.wav", canonical_filename="KP_Ch12_Hdybdyar.wav",
                     mime_type="audio/wav", size_bytes=10, chapter=TITLE_LINE,
                     role="герой", actor_name="Иной Актёр", kind="take",
                     uploaded_at=utcnow_naive()))
    db.flush()
    box = _spy_sends(monkeypatch)

    audit = run_asr_audit(db, chapter_id, transcribe=lambda path: ({}, 0.0))

    assert audit.status == "skipped"
    assert audit.error_message == "no_narrator_take"
    assert box == []


# ---------------------------------------------------------------------------
# Постановка в очередь: после commit и один раз на дубль
# ---------------------------------------------------------------------------

def test_enqueue_wrapper_delegates_to_assembly_lifecycle(monkeypatch):
    from app.services import asr_audit_lifecycle
    calls = []
    monkeypatch.setattr(asr_audit_lifecycle, "enqueue_audit", lambda cid, **kw: calls.append((cid, kw)) or "job")
    assert asr_audit.enqueue_asr_audit("chapter", force=True) == "job"
    assert calls == [("chapter", {"force": True})]


def test_enqueue_silent_when_audit_disabled(db, monkeypatch):
    from app.config import settings
    from app.services import asr_audit
    from app.workers import launcher

    chapter_id = _make_ready_chapter(db)
    calls = []
    monkeypatch.setattr(launcher, "enqueue_tracked_task", lambda *a, **k: calls.append(k) or "job")
    monkeypatch.setattr(settings, "asr_audit_enabled", False)

    assert asr_audit.enqueue_asr_audit(chapter_id) is None
    assert calls == []


def test_perform_asr_chapter_task_does_not_trigger_paid_audit(db, monkeypatch):
    """Распознавание ещё не означает, что глава собрана и готова к платному аудиту."""
    from sqlalchemy.orm import sessionmaker

    from app.services import asr_audit, asr_run
    from app.worker_tasks import perform_asr_chapter_task

    _make_ready_chapter(db)
    calls = []
    # Проверяем проводку (вызов после commit), а не гейтинг — он покрыт тестами due выше.
    monkeypatch.setattr(asr_audit, "enqueue_asr_audit_if_due",
                        lambda db, cid: calls.append(cid) or "job")
    monkeypatch.setattr(asr_run, "run_asr_for_chapter", lambda db, chapter_id, force=False: {
        "done": 1, "realigned": 0, "failed": 0,
    })
    # Задача зовёт глобальный SessionLocal — тытяд её на тестовую базу.
    factory = sessionmaker(bind=db.bind)
    import app.worker_tasks as worker_tasks

    monkeypatch.setattr(worker_tasks, "SessionLocal", factory)

    perform_asr_chapter_task("ch-12", run_id="")

    assert calls == []


def test_latest_audits_badge_summarises_last_run(db, monkeypatch):
    audit, _box = _run_audit(db, monkeypatch, confirm=True)

    badge = asr_audit.latest_audits(db, [audit.chapter_id])[audit.chapter_id]
    assert badge["status"] == "done"
    assert badge["flags"] == 1 and badge["confirmed"] == 1
    assert badge["stt_cost_rub"] == 2.0
    assert asr_audit.latest_audits(db, ["чужая-глава"]) == {}


def test_narrator_lines_keep_exemptions_and_original_indexes(db):
    chapter_id = _make_ready_chapter(db)
    lines = asr_audit.narrator_lines(db, chapter_id)
    assert [r["index"] for r in lines["required"]] == [0, 1]
    assert [r["text"] for r in lines["tags"]] == [TAG_LINE]
    assert [r["text"] for r in lines["titles"]] == [TITLE_LINE]


@pytest.mark.parametrize("answer", [
    {}, {"verdicts": []},
    {"verdicts": [{"index": 0, "status": "unexpected", "missing_words": [], "note": ""}]},
    {"verdicts": [{"index": 0, "status": "ok", "missing_words": [], "note": ""}]},
    {"verdicts": [{"index": i, "status": "ok", "missing_words": [], "note": ""} for i in [0, 0, 1]]},
    {"verdicts": [{"index": i, "status": "ok", "missing_words": [], "note": ""} for i in [0, 1, 99]]},
])
def test_invalid_or_incomplete_judge_never_yields_clean_result(db, monkeypatch, answer):
    chapter_id = _make_ready_chapter(db)
    box = _spy_sends(monkeypatch)
    audit = run_asr_audit(db, chapter_id,
        transcribe=lambda p: (_heard_with(NARRATION_FULL, NARRATION_PARTIAL), 1.5),
        judge=lambda *a: answer)
    assert audit.status == "failed"
    assert audit.transcript_json
    assert audit.stt_cost_rub == 1.5
    assert len(box) == 1 and "Check incomplete" in box[0]["text"]


def _flag(index=0):
    return {"index": index, "segment_id": "s", "text": "Он выпил горячий чай.",
            "status": "partial", "missing_words": ["горячий"], "note": "",
            "anchor": {"start": 20.0, "end": 25.0}, "confirmed": False, "confirm_note": ""}


@pytest.mark.parametrize("answer", [
    {"results": [{"index": 99, "confirmed": True, "note": ""}]},
    {"results": [{"index": 0, "confirmed": "false", "note": ""}]},
    {"results": []},
])
def test_confirmation_requires_exact_index_and_boolean(monkeypatch, answer):
    monkeypatch.setattr(asr_audit, "_cut_window", lambda *a: None)
    flag = _flag()
    confirmed = asr_audit._confirm_flags(flags=[flag], source_path="unused",
        judge=lambda *a: answer, confirm_transcribe=lambda p: ({"text": "Он выпил чай."}, 0.2))
    assert confirmed == []
    assert flag["confirmed"] is False
    assert "confirmation incomplete" in flag["confirm_note"]


@pytest.mark.parametrize(("transcript", "expected"), [
    ("Он выпил чай.", True), ("Он выпил горячий чай.", False), ("", False),
])
def test_confirmation_index_zero_with_actual_words(monkeypatch, transcript, expected):
    monkeypatch.setattr(asr_audit, "_cut_window", lambda *a: None)
    flag = _flag()
    calls = []
    def judge(system, user, schema):
        calls.append(user)
        return {"results": [{"index": 0, "confirmed": True, "note": "пропуск"}]}
    result = asr_audit._confirm_flags(flags=[flag], source_path="unused", judge=judge,
        confirm_transcribe=lambda p: ({"text": transcript}, 0.5))
    assert bool(result) is expected
    assert flag["confirmed"] is expected
    if transcript:
        assert "Индекс строки: 0" in calls[0]


def test_confirmation_failure_isolated_per_flag(monkeypatch):
    monkeypatch.setattr(asr_audit, "_cut_window", lambda *a: None)
    flags = [_flag(0), _flag(1)]
    def judge(system, user, schema):
        if "Индекс строки: 0" in user:
            raise RuntimeError("judge unavailable")
        return {"results": [{"index": 1, "confirmed": True, "note": ""}]}
    result = asr_audit._confirm_flags(flags=flags, source_path="unused", judge=judge,
        confirm_transcribe=lambda p: {"text": "Он выпил чай."})
    assert result == [flags[1]]
    assert not flags[0]["confirmed"]


def test_missing_line_anchor_uses_neighbor_times_and_word_position():
    alignment = {"lines": [
        {"index": 0, "matched": True, "start": 50, "end": 55},
        {"index": 2, "matched": False, "start": None, "end": None},
        {"index": 4, "matched": True, "start": 60, "end": 65},
    ]}
    assert asr_audit._anchor_of(alignment, 0) == {"start": 50., "end": 55.}
    assert asr_audit._anchor_of(alignment, 2) == {"start": 55., "end": 60., "approximate": True}
    anchor = {"start": 50., "end": 60.}
    positions = asr_audit._word_anchors("Он выпил горячий чай.", ["горячий"], anchor,
        [("Он", 50., 51.), ("выпил", 51., 52.), ("чай", 56., 57.)])
    assert positions == [{"word": "горячий", "start": 52., "end": 56., "approximate": True}]


def test_required_indexes_remap_past_exempt_lines(db, monkeypatch):
    chapter_id = _make_ready_chapter(db)
    segs = db.query(V2Segment).order_by(V2Segment.ordinal).all()
    first, tag = segs[0].text, segs[2].text
    segs[0].text, segs[2].text = tag, first
    attrs = {a.segment_id: a for a in db.query(V2Attribution).all()}
    attrs[segs[0].id].span_end = len(tag)
    attrs[segs[2].id].span_end = len(first)
    _spy_sends(monkeypatch)
    def judge(system, user, schema):
        assert "[0]" not in user and "[3]" not in user
        assert "[1]" in user and "[2]" in user
        return {"verdicts": [{"index": i, "status": "ok", "missing_words": [], "note": ""} for i in [1, 2]]}
    audit = run_asr_audit(db, chapter_id,
        transcribe=lambda p: (_heard_with(NARRATION_PARTIAL, NARRATION_FULL), 1.), judge=judge)
    assert audit.status == "done"


def test_full_line_text_is_sent_to_judges():
    text = "слово " * 100 + "последнее_слово"
    prompt = asr_audit._judge_user_prompt([{"index": 0, "text": text}], {}, [])
    assert text in prompt


def test_unknown_cost_is_not_displayed_as_free(db, monkeypatch):
    chapter_id = _make_ready_chapter(db)
    box = _spy_sends(monkeypatch)
    audit = run_asr_audit(db, chapter_id,
        transcribe=lambda p: _heard_with(NARRATION_FULL, NARRATION_PARTIAL),
        judge=lambda *a: {"verdicts": [{"index": i, "status": "ok", "missing_words": [], "note": ""} for i in [0, 1]]})
    assert audit.status == "done"
    assert asr_audit.latest_audits(db, [chapter_id])[chapter_id]["stt_cost_rub"] is None
    assert "STT cost unconfirmed (excluding LLM)" in box[0]["text"]


def test_default_transcribe_keeps_real_usage_and_model(monkeypatch, tmp_path):
    from app.services import asr_transcribe
    payload = asr_transcribe.parse_transcription({"text": "слова", "segments": [], "usage": {"cost": 1.25, "currency": "RUB"}})
    assert payload["usage"]["cost"] == 1.25
    monkeypatch.setattr(asr_audit, "compress_for_asr", lambda *a: None)
    seen = []
    monkeypatch.setattr(asr_audit, "transcribe_file", lambda path, **kw: seen.append(kw["model"]) or payload)
    heard, cost = asr_audit._transcribe_fresh(str(tmp_path / "source.wav"), model="frozen", phrases=[])
    assert cost == 1.25 and seen == ["frozen"]
    assert heard["text"] == "слова"


def test_file_sqlite_allows_other_writes_during_paid_calls(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'audit.sqlite'}", connect_args={"timeout": 0.1})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    calls = []
    monkeypatch.setattr(asr_audit, "_cut_window", lambda *a: None)
    _spy_sends(monkeypatch)
    with factory() as session:
        chapter_id = _make_ready_chapter(session)
        def concurrent_write(stage):
            assert not session.in_transaction(), stage
            with factory() as other:
                running = other.query(AsrAudit).one()
                assert running.status == "running"
                other.get(ScriptBook, BOOK).source_filename = stage
                other.commit()
            calls.append(stage)
        def transcribe(path):
            concurrent_write("stt")
            return _heard_with(NARRATION_FULL, NARRATION_PARTIAL), 1.5
        def judge(system, user, schema):
            concurrent_write("confirm_judge" if "results" in schema["properties"] else "judge")
            return _judge(confirm=True)(system, user, schema)
        def confirm(path):
            concurrent_write("confirm_stt")
            return {"text": NARRATION_PARTIAL.replace("хотя ", "").replace("много ", "")}, .5
        audit = run_asr_audit(session, chapter_id, transcribe=transcribe, judge=judge, confirm_transcribe=confirm)
        assert audit.status == "done"
    assert calls == ["stt", "judge", "confirm_stt", "confirm_judge"]


def test_notification_failure_does_not_lose_audit_or_block_narrator(db, monkeypatch):
    chapter_id = _make_ready_chapter(db)
    monkeypatch.setattr(asr_audit, "_cut_window", lambda *a: None)
    notified = []
    def owner(session, **kwargs):
        assert session.get(AsrAudit, session.query(AsrAudit.id).scalar()).status == "done"
        raise RuntimeError("telegram unavailable")
    monkeypatch.setattr(asr_audit, "_notify_owner", owner)
    monkeypatch.setattr(asr_audit, "_notify_narrator", lambda *a, **kw: notified.append(kw))
    audit = run_asr_audit(db, chapter_id,
        transcribe=lambda p: (_heard_with(NARRATION_FULL, NARRATION_PARTIAL), 1.5),
        judge=_judge(confirm=True),
        confirm_transcribe=lambda p: ({"text": "На дороге наступила тишина впереди ещё работы."}, .5))
    assert audit.status == "done" and audit.stt_cost_rub == 2.
    assert len(notified) == 1


def _add_fix(db, *, starts=(0., 5.5)):
    from app.models import AudioFile, AsrJob
    from app.time_utils import utcnow_naive
    from datetime import timedelta
    original = db.get(AudioFile, "take-r")
    db.add(AudioFile(id="fix", book_code="КП", original_filename="fix.wav", stored_key="k/fix.wav",
        canonical_filename="fix.wav", mime_type="audio/wav", size_bytes=5, chapter=TITLE_LINE,
        role="Рассказчик", actor_name=original.actor_name, kind="take", uploaded_at=utcnow_naive() + timedelta(seconds=1)))
    db.add(AsrJob(audio_file_id="fix", chapter_id="ch-12", status="done", alignment_json=json.dumps({
        "total": 4, "matched": 1, "missing": [0, 2, 3], "lines": [
            {"index": 0, "matched": False, "text": NARRATION_FULL},
            {"index": 1, "matched": True, "text": NARRATION_PARTIAL, "start": starts[0], "end": starts[1]},
            {"index": 2, "matched": False, "text": TAG_LINE},
            {"index": 3, "matched": False, "text": TITLE_LINE},
        ]})))
    db.flush()


def test_multifile_assembly_uses_original_and_fix_without_comparing_latest_to_whole_chapter(db, monkeypatch):
    chapter_id = _make_ready_chapter(db)
    _add_fix(db)
    _spy_sends(monkeypatch)
    seen = []
    def transcribe(path):
        seen.append(path)
        if path.endswith("fix.wav"):
            return _heard_with(NARRATION_PARTIAL, ""), .5
        return _heard_with(NARRATION_FULL, "текст старой заменённой записи"), 1.5
    def judge(system, user, schema):
        assert NARRATION_FULL in user
        assert "Подход 1: на дороге наступила тишина хотя впереди еще много работы" in user
        assert "текст старой заменённой записи" not in user
        return {"verdicts": [{"index": i, "status": "ok", "missing_words": [], "note": ""} for i in [0, 1]]}
    audit = run_asr_audit(db, chapter_id, transcribe=transcribe, judge=judge)
    assert audit.status == "done"
    assert len(seen) == 2 and audit.stt_cost_rub == 2.
    assert set(json.loads(audit.transcript_json)["sources"]) == {"take-r", "fix"}
    assert json.loads(audit.lines_json)["narrator_input_fingerprint"] == asr_audit.narrator_input_fingerprint(db, chapter_id)


def test_multifile_flag_carries_actual_fix_source_and_confirms_only_clip(db, monkeypatch):
    chapter_id = _make_ready_chapter(db)
    _add_fix(db)
    _spy_sends(monkeypatch)
    cuts = []
    monkeypatch.setattr(asr_audit, "_cut_window", lambda *a: cuts.append(a))
    audit = run_asr_audit(db, chapter_id,
        transcribe=lambda path: (_heard_with(NARRATION_PARTIAL if path.endswith("fix.wav") else NARRATION_FULL, ""), 1.),
        judge=_judge(confirm=True),
        confirm_transcribe=lambda p: ({"text": "На дороге наступила тишина впереди ещё работы."}, .5))
    assert audit.status == "done"
    flags, count = asr_audit._audit_findings(audit)
    assert count == 1 and flags[0]["source_audio_file_id"] == "fix"
    assert flags[0]["anchor"]["source_audio_file_id"] == "fix"
    assert len(cuts) == 1 and cuts[0][0].endswith("fix.wav")
    assert cuts[0][4]["source_audio_file_id"] == "fix"


def test_any_complete_second_approach_prevents_actor_accusation(monkeypatch):
    monkeypatch.setattr(asr_audit, "_cut_window", lambda *a: None)
    flag = _flag()
    flag["source_windows"] = [
        {"source_audio_file_id": "a", "start": 5., "end": 10.},
        {"source_audio_file_id": "a", "start": 15., "end": 20.},
    ]
    transcripts = iter(["Он выпил чай.", "Он выпил горячий чай."])
    costs = []
    result = asr_audit._confirm_flags(flags=[flag], source_paths={"a": "unused"},
        judge=lambda *a: {"results": [{"index": 0, "confirmed": True, "note": ""}]},
        confirm_transcribe=lambda p: ({"text": next(transcripts)}, .5), record_cost=costs.append)
    assert result == [] and not flag["confirmed"]
    assert costs == [.5, .5]


def test_fingerprint_changes_with_narrator_source_text_or_cut_but_not_other_role_timing(db):
    from app.models import AsrJob
    chapter_id = _make_ready_chapter(db)
    original = asr_audit.narrator_input_fingerprint(db, chapter_id)
    _add_fix(db)
    assert asr_audit.narrator_input_fingerprint(db, chapter_id) != original
    before_cut = asr_audit.narrator_input_fingerprint(db, chapter_id)
    job = db.query(AsrJob).filter(AsrJob.audio_file_id == "fix").one()
    payload = json.loads(job.alignment_json)
    payload["lines"][1]["end"] += .1
    job.alignment_json = json.dumps(payload)
    db.flush()
    assert asr_audit.narrator_input_fingerprint(db, chapter_id) != before_cut
    before_text = asr_audit.narrator_input_fingerprint(db, chapter_id)
    segment = db.query(V2Segment).filter(V2Segment.ordinal == 0).one()
    segment.text = "Совсем другие слова в сценарии."
    db.flush()
    assert asr_audit.narrator_input_fingerprint(db, chapter_id) != before_text


def test_expected_fingerprint_rejects_stale_input_without_paid_calls(db, monkeypatch):
    chapter_id = _make_ready_chapter(db)
    _spy_sends(monkeypatch)
    calls = []
    audit = run_asr_audit(db, chapter_id, expected_narrator_fingerprint="stale",
                         transcribe=lambda p: calls.append(p))
    assert audit.status == "skipped" and audit.error_message == "narrator_input_changed"
    assert calls == []


def test_bare_provider_cost_currency_is_not_misrepresented_as_rubles(monkeypatch):
    monkeypatch.setattr(asr_audit, "compress_for_asr", lambda *a: None)
    monkeypatch.setattr(asr_audit, "transcribe_file", lambda *a, **kw: {"text": "текст", "usage": {"cost": .5}})
    _, cost = asr_audit._transcribe_fresh("unused", model="model", phrases=[])
    assert cost is None


def test_latest_audits_reads_durable_queue_and_unknown_outcome(db):
    from app.models import BackgroundRun
    chapter_id = _make_ready_chapter(db)
    run = BackgroundRun(run_key=f"asr-audit:{chapter_id}", job_kind="asr_audit", entity_type="script_chapter",
                        entity_id=chapter_id, status="queued")
    db.add(run)
    db.flush()
    assert asr_audit.latest_audits(db, [chapter_id])[chapter_id]["status"] == "queued"
    run.error_message = "queue_outcome_unknown: owner review is required владельцем"
    db.flush()
    badge = asr_audit.latest_audits(db, [chapter_id])[chapter_id]
    assert badge["status"] == "unknown" and "owner review is required" in badge["error_message"]


def test_mutation_during_paid_audit_keeps_cost_without_actor_accusation(db, monkeypatch):
    chapter_id = _make_ready_chapter(db)
    box = _spy_sends(monkeypatch)
    monkeypatch.setattr(asr_audit, "_cut_window", lambda *a: None)
    marked = []
    from app.services import chapter_delivery
    monkeypatch.setattr(chapter_delivery, "mark_session_outdated", lambda *a: marked.append(a))
    def transcribe(path):
        segment = db.query(V2Segment).filter(V2Segment.ordinal == 0).one()
        segment.text = "Уже исправленный текст сценария."
        db.commit()
        return _heard_with(NARRATION_FULL, NARRATION_PARTIAL), 1.5
    audit = run_asr_audit(db, chapter_id, transcribe=transcribe, judge=_judge(confirm=True),
        confirm_transcribe=lambda p: ({"text": "На дороге наступила тишина впереди ещё работы."}, .5))
    assert audit.status == "skipped" and audit.error_message == "narrator_input_changed_after_payment"
    assert audit.stt_cost_rub == 2.
    assert asr_audit._audit_findings(audit)[1] == 1
    assert marked == []
    assert len(box) == 1 and not box[0]["direct"]
    assert "Stale result" in box[0]["text"]


def test_all_source_files_are_checked_before_first_paid_request(db, monkeypatch, audio_sources):
    chapter_id = _make_ready_chapter(db)
    _add_fix(db)
    (audio_sources / "k/fix.wav").unlink()
    box = _spy_sends(monkeypatch)
    calls = []
    audit = run_asr_audit(db, chapter_id, transcribe=lambda p: calls.append(p))
    assert audit.status == "failed"
    assert audit.error_message == "narrator_source_unreadable:fix"
    assert audit.stt_cost_rub == 0.
    assert calls == []
    assert len(box) == 1 and "Check incomplete" in box[0]["text"]


def test_nas_preflight_checks_liveness_before_touching_source(monkeypatch):
    from app.services import nas_health
    monkeypatch.setattr(nas_health, "reachable_now", lambda: False)
    monkeypatch.setattr(asr_audit.os.path, "getsize", lambda p: pytest.fail("NAS source was touched"))
    with pytest.raises(asr_audit.AsrAuditError, match="narrator_storage_unavailable"):
        asr_audit._preflight_sources({"a": {"location": "nas"}}, {"a": "/dead-nas/file.wav"})


@pytest.mark.parametrize("failure", ["size", "checksum", "verify"])
def test_corrupt_source_does_not_start_paid_requests(db, monkeypatch, failure):
    from app.models import AudioFile
    chapter_id = _make_ready_chapter(db)
    item = db.get(AudioFile, "take-r")
    if failure == "size":
        item.size_bytes += 1
    elif failure == "checksum":
        item.md5 = "bad"
    else:
        item.verify_state = "mismatch"
    _spy_sends(monkeypatch)
    calls = []
    audit = run_asr_audit(db, chapter_id, transcribe=lambda p: calls.append(p))
    assert audit.status == "failed"
    assert calls == []


def test_narrator_aliases_with_same_role_index_resolve_to_distinct_script_lines(db):
    from app.models import AudioFile, AsrJob
    chapter_id = _make_ready_chapter(db)
    _add_fix(db)
    segment = db.query(V2Segment).filter(V2Segment.ordinal == 1).one()
    db.query(V2Attribution).filter(V2Attribution.segment_id == segment.id).one().speaker = "Автор"
    db.get(AudioFile, "fix").role = "Автор"
    job = db.query(AsrJob).filter(AsrJob.audio_file_id == "fix").one()
    job.alignment_json = json.dumps({"total": 1, "matched": 1, "missing": [], "lines": [
        {"index": 0, "text": NARRATION_PARTIAL, "matched": True, "start": 0., "end": 5.5}]})
    db.flush()
    snapshot = asr_audit._narrator_snapshot(db, chapter_id)
    required = [c for c in snapshot["clips"] if c["index"] in {0, 1}]
    assert [(c["index"], c["role_index"], c["source_audio_file_id"]) for c in required] == [
        (0, 0, "take-r"), (1, 0, "fix")]


def test_other_role_timing_shift_does_not_invalidate_narrator_input(db):
    from app.models import AudioFile, AsrJob
    from app.services.chapter_delivery import _script_layout
    from app.time_utils import utcnow_naive
    chapter_id = _make_ready_chapter(db)
    db.add(V2Segment(id=f"{chapter_id}:90000", book_id=BOOK, chapter_id=chapter_id, ordinal=-1,
                    kind="paragraph", text="Герой говорит до рассказчика.", char_start=0, char_end=28))
    db.add(V2Attribution(id=str(uuid.uuid4()), segment_id=f"{chapter_id}:90000", span_start=0,
                        span_end=28, speaker="Герой", confidence=1, source="llm", version=1))
    db.add(AudioFile(id="hero", book_code="КП", original_filename="hero.wav", stored_key="k/hero.wav",
        canonical_filename="hero.wav", mime_type="audio/wav", size_bytes=5, chapter=TITLE_LINE,
        role="Герой", actor_name="Голос Героя", kind="take", uploaded_at=utcnow_naive()))
    job = AsrJob(audio_file_id="hero", chapter_id=chapter_id, status="done", alignment_json=json.dumps({
        "total": 1, "matched": 1, "missing": [], "lines": [
        {"index": 0, "text": "Герой говорит до рассказчика.", "matched": True, "start": 0., "end": 10.}]}))
    db.add(job)
    db.flush()
    original = asr_audit.narrator_input_fingerprint(db, chapter_id)
    old_start = next(c.start for role, c in _script_layout(db, chapter_id)[3] if role == "Рассказчик")
    payload = json.loads(job.alignment_json)
    payload["lines"][0]["end"] = 20.
    job.alignment_json = json.dumps(payload)
    db.flush()
    new_start = next(c.start for role, c in _script_layout(db, chapter_id)[3] if role == "Рассказчик")
    assert old_start != new_start
    assert asr_audit.narrator_input_fingerprint(db, chapter_id) == original
    assert asr_audit._narrator_fingerprints(db, [chapter_id]) == {chapter_id: original}


def test_models_freeze_at_run_start_for_all_stages(db, monkeypatch):
    from app.config import settings
    chapter_id = _make_ready_chapter(db)
    _spy_sends(monkeypatch)
    monkeypatch.setattr(asr_audit, "_cut_window", lambda *a: None)
    monkeypatch.setattr(settings, "asr_audit_model", "audit-before")
    monkeypatch.setattr(settings, "asr_audit_confirm_model", "confirm-before")
    monkeypatch.setattr(settings, "llm_audit_model", "judge-before")
    heard_models, judge_models = [], []
    def transcribe(path, *, model, phrases):
        heard_models.append(model)
        settings.asr_audit_model = "audit-after"
        settings.asr_audit_confirm_model = "confirm-after"
        settings.llm_audit_model = "judge-after"
        if model == "audit-before":
            return _heard_with(NARRATION_FULL, NARRATION_PARTIAL), 1.5
        return {"text": "На дороге наступила тишина впереди ещё работы."}, .5
    def judge(system, user, schema, *, model):
        judge_models.append(model)
        return _judge(confirm=True)(system, user, schema)
    monkeypatch.setattr(asr_audit, "_transcribe_fresh", transcribe)
    monkeypatch.setattr(asr_audit, "default_judge_ask", judge)
    audit = run_asr_audit(db, chapter_id)
    assert audit.status == "done"
    assert heard_models == ["audit-before", "confirm-before"]
    assert judge_models == ["judge-before", "judge-before"]
    assert (audit.model, audit.confirm_model, audit.judge_model) == ("audit-before", "confirm-before", "judge-before")


def test_foreign_book_take_with_same_chapter_label_never_reaches_paid_audit(db, monkeypatch):
    from app.models import AudioFile
    chapter_id = _make_ready_chapter(db)
    _add_fix(db)
    db.get(AudioFile, "fix").book_code = "ДК"
    _spy_sends(monkeypatch)
    calls = []
    audit = run_asr_audit(db, chapter_id, transcribe=lambda p: calls.append(p))
    assert audit.status == "failed" and audit.error_message == "narrator_source_scope_mismatch:fix"
    assert calls == []


def test_unplaced_required_line_cannot_be_declared_clean_by_judge(db, monkeypatch):
    from app.models import AsrJob
    chapter_id = _make_ready_chapter(db)
    job = db.query(AsrJob).filter(AsrJob.audio_file_id == "take-r").one()
    payload = json.loads(job.alignment_json)
    payload["lines"][1] = {"index": 1, "matched": False, "text": NARRATION_PARTIAL}
    job.alignment_json = json.dumps(payload)
    db.flush()
    box = _spy_sends(monkeypatch)
    audit = run_asr_audit(db, chapter_id,
        transcribe=lambda p: (_heard_with(NARRATION_FULL, ""), 1.),
        judge=lambda *a: {"verdicts": [{"index": i, "status": "ok", "missing_words": [], "note": ""} for i in [0, 1]]})
    flags, count = asr_audit._audit_findings(audit)
    assert audit.status == "done" and count == 0
    assert len(flags) == 1 and flags[0]["index"] == 1 and flags[0]["status"] == "missing"
    assert flags[0]["source_windows"] == [] and flags[0]["confirmed"] is False
    assert len(box) == 1 and not box[0]["direct"]


def test_clean_repeat_invalidates_archive_to_remove_previous_confirmed_markers(db, monkeypatch):
    from app.services.chapter_delivery import build_chapter_session
    from app.time_utils import utcnow_naive

    first, _ = _run_audit(db, monkeypatch, confirm=True)
    chapter = db.get(ScriptChapter, first.chapter_id)
    archived_xml = build_chapter_session(db, chapter.id, relative=True)
    assert "РАССКАЗЧИК · пропуск" in archived_xml
    # Проект с прежними метками уже сохранён; только новый аудит теперь может
    # подсказать фону, что эти метки надо удалить из архивной версии.
    chapter.session_archived_at = utcnow_naive()
    chapter.session_outdated_at = None
    db.commit()
    second = run_asr_audit(db, chapter.id,
        transcribe=lambda p: (_heard_with(NARRATION_FULL, NARRATION_PARTIAL), 1.5),
        judge=lambda *a: {"verdicts": [
            {"index": i, "status": "ok", "missing_words": [], "note": ""} for i in [0, 1]]})
    assert second.status == "done"
    assert chapter.session_outdated_at is not None
    assert "РАССКАЗЧИК · пропуск" not in build_chapter_session(db, chapter.id, relative=True)



def test_gateway_failure_does_not_retry_a_paid_audit(db, monkeypatch):
    from app.services import asr_run
    chapter_id = _make_ready_chapter(db)
    _spy_sends(monkeypatch)
    calls = []
    def unavailable(*args, **kwargs):
        calls.append(1)
        raise RuntimeError("asr_http_504")
    monkeypatch.setattr(asr_audit, "_transcribe_fresh", unavailable)
    monkeypatch.setattr(asr_run, "_sleep", lambda seconds: pytest.fail("повтор после неопределённого списания"))
    audit = run_asr_audit(db, chapter_id)
    assert audit.status == "failed"
    assert audit.error_message == "asr_http_504"
    assert calls == [1]


def test_latest_audit_is_not_current_after_script_changes(db):
    chapter_id = _make_ready_chapter(db)
    fingerprint = asr_audit.narrator_input_fingerprint(db, chapter_id)
    db.add(AsrAudit(chapter_id=chapter_id, status="done", model="test",
                    lines_json=json.dumps({"narrator_input_fingerprint": fingerprint}),
                    findings_json=json.dumps({"flags": [], "confirmed": 0})))
    db.flush()
    assert asr_audit.latest_audits(db, [chapter_id])[chapter_id]["status"] == "done"
    segment = db.query(V2Segment).filter(V2Segment.chapter_id == chapter_id, V2Segment.ordinal == 0).one()
    segment.text = "Изменённое содержание первой реплики."
    db.flush()
    assert asr_audit.latest_audits(db, [chapter_id])[chapter_id]["status"] == "stale"


@pytest.mark.parametrize("change", ["original", "fix", "alias", "borrowed", "version", "checksum", "duration"])
def test_batch_fingerprint_matches_session_input(db, change):
    from app.models import AudioFile, AsrJob

    chapter_id = _make_ready_chapter(db)
    if change in {"fix", "alias", "borrowed"}:
        _add_fix(db)
    if change == "alias":
        segment = db.query(V2Segment).filter(V2Segment.ordinal == 1).one()
        db.query(V2Attribution).filter(V2Attribution.segment_id == segment.id).one().speaker = "Автор"
        db.get(AudioFile, "fix").role = "Автор"
    elif change == "borrowed":
        db.get(AudioFile, "fix").role = "Герой"
        job = db.query(AsrJob).filter(AsrJob.audio_file_id == "take-r").one()
        alignment = json.loads(job.alignment_json)
        alignment["lines"][1]["source_audio_file_id"] = "fix"
        job.alignment_json = json.dumps(alignment)
    elif change == "version":
        segment = db.query(V2Segment).filter(V2Segment.ordinal == 0).one()
        db.add(V2Attribution(id="latest", segment_id=segment.id, span_start=3,
                             span_end=len(segment.text), speaker="Рассказчик", version=2))
    elif change == "checksum":
        db.get(AudioFile, "take-r").md5 = "updated"
    elif change == "duration":
        db.get(AudioFile, "take-r").duration_seconds = 11.6
    db.flush()
    assert asr_audit._narrator_fingerprints(db, [chapter_id]) == {
        chapter_id: asr_audit.narrator_input_fingerprint(db, chapter_id)}


@pytest.mark.parametrize("cached_chapters", [None, 0, 1, 20])
def test_latest_audits_checks_many_chapters_with_bounded_queries(db, cached_chapters):
    from app.models import AudioFile, AsrJob
    from sqlalchemy import event

    db.add(ScriptBook(id=BOOK, title="Книга примеров", source_filename="test.txt", source_format="txt"))
    chapter_ids = [f"chapter-{index}" for index in range(20)]
    for index, cid in enumerate(chapter_ids):
        text, label, aid, sid = "Начался новый день.", f"Глава {index}", f"audio-{index}", f"{cid}:00000"
        db.add(ScriptChapter(id=cid, book_id=BOOK, chapter_index=index, chapter_title=label, status="published"))
        db.add(V2Segment(id=sid, book_id=BOOK, chapter_id=cid, ordinal=0, kind="paragraph", text=text))
        db.add(V2Attribution(id=f"attr-{index}", segment_id=sid, span_start=0,
                             span_end=len(text), speaker="Рассказчик", version=1))
        db.add(AudioFile(id=aid, book_code="КП", chapter=label, role="Рассказчик", kind="take",
                         original_filename="test.wav", canonical_filename="test.wav", stored_key="test.wav",
                         mime_type="audio/wav", size_bytes=1))
        db.add(AsrJob(audio_file_id=aid, chapter_id=cid, status="done", alignment_json=json.dumps({
            "lines": [{"index": 0, "text": text, "matched": True, "start": 0., "end": 2.}]})))
    db.flush()
    for cid in chapter_ids:
        db.add(AsrAudit(chapter_id=cid, status="done", lines_json=json.dumps({
            "narrator_input_fingerprint": asr_audit.narrator_input_fingerprint(db, cid)})))
    db.flush()
    chapter_map = None if cached_chapters is None else {
        row.id: row for row in db.query(ScriptChapter).filter(
            ScriptChapter.id.in_(chapter_ids[:cached_chapters])).all()}
    extra = {} if chapter_map is None else {"chapters_by_id": chapter_map}
    queries = []
    chapter_queries = []
    def count_queries(conn, cursor, statement, *args):
        queries.append(1)
        if "FROM script_chapters" in statement:
            chapter_queries.append(statement)
    event.listen(db.get_bind(), "before_cursor_execute", count_queries)
    try:
        current = asr_audit.latest_audits(db, chapter_ids, **extra)
        assert all(current[cid]["status"] == "done" for cid in chapter_ids)
        assert len(queries) <= 10
        assert len(chapter_queries) == (0 if cached_chapters == 20 else 1)
        segment = db.get(V2Segment, f"{chapter_ids[0]}:00000")
        segment.text = "Сценарий изменился."
        db.flush()
        queries.clear()
        chapter_queries.clear()
        current = asr_audit.latest_audits(db, chapter_ids, **extra)
        assert current[chapter_ids[0]]["status"] == "stale"
        assert all(current[cid]["status"] == "done" for cid in chapter_ids[1:])
        assert len(queries) <= 10
        assert len(chapter_queries) == (0 if cached_chapters == 20 else 1)
        job = db.query(AsrJob).filter(AsrJob.audio_file_id == "audio-1").one()
        job.alignment_json = "[]"
        db.flush()
        current = asr_audit.latest_audits(db, chapter_ids, **extra)
        assert current[chapter_ids[1]]["status"] == "stale"
        assert all(current[cid]["status"] == "done" for cid in chapter_ids[2:])
    finally:
        event.remove(db.get_bind(), "before_cursor_execute", count_queries)


def test_dictor_cannot_read_audit_cost(db):
    from app.services.chapter_delivery import book_recording_status
    chapter_id = _make_ready_chapter(db)
    db.add(AsrAudit(chapter_id=chapter_id, status="done", model="test",
                    stt_cost_rub=12.5, lines_json=json.dumps({"cost_complete": True})))
    db.flush()
    editor = book_recording_status(db, BOOK, ambient=True)["chapters"][0]["audit"]
    reader = book_recording_status(db, BOOK, ambient=False)["chapters"][0]["audit"]
    assert editor["stt_cost_rub"] == 12.5
    assert reader["stt_cost_rub"] is None


def test_ambiguous_legacy_book_code_never_starts_paid_audit(db, monkeypatch):
    chapter_id = _make_ready_chapter(db)
    db.add(ScriptBook(id="other-book", title="Котёнок прыгает", source_filename="x.txt", source_format="txt"))
    db.flush()
    called = []
    with pytest.raises(asr_audit.AsrAuditError, match="ambiguous_book_code"):
        run_asr_audit(db, chapter_id, transcribe=lambda path: called.append(path))
    assert called == []


def test_duplicate_chapter_label_never_starts_paid_audit(db):
    chapter_id = _make_ready_chapter(db)
    db.add(ScriptChapter(id="duplicate", book_id=BOOK, chapter_index=13,
                         chapter_title=TITLE_LINE, status="published"))
    db.flush()
    called = []
    with pytest.raises(asr_audit.AsrAuditError, match="ambiguous_chapter_label"):
        run_asr_audit(db, chapter_id, transcribe=lambda path: called.append(path))
    assert called == []


def test_audit_uses_byok_and_records_real_cost(monkeypatch, tmp_path):
    from app.services import audio_probe, provider_keys, spend
    calls = []
    monkeypatch.setattr(provider_keys, "provider_key", lambda provider: "byok-test")
    monkeypatch.setattr(asr_audit, "compress_for_asr", lambda *args: None)
    monkeypatch.setattr(audio_probe, "probe_audio_file", lambda path: {"duration_seconds": 12.2})
    monkeypatch.setattr(spend, "record_call", lambda *args, **kwargs: calls.append((args, kwargs)))
    def transcribe(path, **kwargs):
        assert kwargs["api_key"] == "byok-test"
        return {"segments": [], "usage": {"cost": 1.25, "currency": "RUB"}}
    monkeypatch.setattr(asr_audit, "transcribe_file", transcribe)
    _, cost = asr_audit._transcribe_fresh(str(tmp_path / "source.wav"), model="test", phrases=[])
    assert cost == 1.25
    assert calls[0][1]["actual_rub"] == 1.25
    assert calls[0][1]["input_units"] == 13


def test_audit_failed_provider_call_records_unknown_cost(monkeypatch, tmp_path):
    from app.services import audio_probe, provider_keys, spend
    calls = []
    monkeypatch.setattr(provider_keys, "provider_key", lambda provider: "byok-test")
    monkeypatch.setattr(asr_audit, "compress_for_asr", lambda *args: None)
    monkeypatch.setattr(audio_probe, "probe_audio_file", lambda path: {"duration_seconds": 12})
    monkeypatch.setattr(spend, "record_call", lambda *args, **kwargs: calls.append((args, kwargs)))
    def fail(*args, **kwargs):
        raise TimeoutError("test timeout")
    monkeypatch.setattr(asr_audit, "transcribe_file", fail)
    with pytest.raises(TimeoutError):
        asr_audit._transcribe_fresh(str(tmp_path / "source.wav"), model="test", phrases=[])
    assert calls[0][1]["estimated"] is True

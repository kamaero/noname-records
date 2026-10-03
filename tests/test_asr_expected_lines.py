"""Что диктор должен был произнести в этой главе — в порядке сценария.

Реплика в v2 — не строка текста, а отрезок внутри абзаца: `text[span_start:span_end]`.
Один абзац может нести и слова рассказчика, и реплику героя, поэтому «реплики роли» —
это выборка отрезков, а не выборка абзацев.

Порядок здесь не украшение: на нём стоит всё выравнивание. Диктор читает сверху вниз,
и если мы отдадим реплики вперемешку, поиск пойдёт не туда с первой же строки.
"""
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.services.asr_run import expected_lines
from app.time_utils import utcnow_naive
from app.v2.models import V2Attribution, V2Segment


@pytest.fixture()
def chapter(monkeypatch):
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        chapter_id = "ch-1"
        segments = [
            (0, "Дгарнин обернулся. — Я не пойду туда, — сказал он."),
            (1, "— Почему же? — Там холодно."),
        ]
        for ordinal, text in segments:
            db.add(V2Segment(id=f"{chapter_id}:{ordinal:05d}", book_id="b1", chapter_id=chapter_id,
                             ordinal=ordinal, kind="paragraph", text=text))
        db.flush()
        yield db, chapter_id


def _say(db, segment_id, speaker, start, end, version=1):
    db.add(V2Attribution(id=str(uuid.uuid4()), segment_id=segment_id, version=version, speaker=speaker,
                         span_start=start, span_end=end, source="llm", created_at=utcnow_naive()))
    db.flush()


def test_only_the_lines_of_that_role(chapter):
    db, chapter_id = chapter
    _say(db, f"{chapter_id}:00000", "Рассказчик", 0, 19)
    _say(db, f"{chapter_id}:00000", "Дгарнин", 21, 38)

    assert expected_lines(db, chapter_id, "Дгарнин") == ["Я не пойду туда,"]


def test_they_come_in_the_order_of_the_script(chapter):
    db, chapter_id = chapter
    _say(db, f"{chapter_id}:00001", "Дгарнин", 15, 27)
    _say(db, f"{chapter_id}:00000", "Дгарнин", 21, 38)

    assert expected_lines(db, chapter_id, "Дгарнин") == ["Я не пойду туда,", "Там холодно."]


def test_a_role_that_says_nothing_here(chapter):
    db, chapter_id = chapter
    _say(db, f"{chapter_id}:00000", "Рассказчик", 0, 19)

    assert expected_lines(db, chapter_id, "Сатухух") == []


def test_the_name_is_matched_the_way_the_cast_matches_it(chapter):
    """«Дгарни́н» с ударением и «дгарнин» — тот же персонаж."""
    db, chapter_id = chapter
    _say(db, f"{chapter_id}:00000", "Дгарни́н", 21, 38)

    assert expected_lines(db, chapter_id, "дгарнин") == ["Я не пойду туда,"]


def test_a_later_version_of_an_attribution_wins(chapter):
    """Оператор переназначил реплику — читаем его решение, а не догадку модели."""
    db, chapter_id = chapter
    _say(db, f"{chapter_id}:00000", "Дгарнин", 21, 38, version=1)
    _say(db, f"{chapter_id}:00000", "Сатухух", 21, 38, version=2)

    assert expected_lines(db, chapter_id, "Дгарнин") == []
    assert expected_lines(db, chapter_id, "Сатухух") == ["Я не пойду туда,"]


def test_an_empty_span_is_not_a_line(chapter):
    db, chapter_id = chapter
    _say(db, f"{chapter_id}:00000", "Дгарнин", 5, 5)

    assert expected_lines(db, chapter_id, "Дгарнин") == []


class TestRunningItOnATake:
    """Три шага сходятся: что ждали, что услышали, что совпало.

    Само распознавание сюда не ходит — оно подменяется. Проверяется склейка: та ли
    глава нашлась, те ли реплики взяты, тем ли записан итог.
    """

    @pytest.fixture()
    def studio(self, chapter):
        from app.models import AudioFile, ScriptBook, ScriptChapter

        db, chapter_id = chapter
        book = ScriptBook(id="b1", title="Крылья полумрака", source_filename="k.txt",
                          source_format="txt", created_at=utcnow_naive())
        db.add(book)
        db.add(ScriptChapter(id=chapter_id, book_id="b1", chapter_index=4,
                             chapter_title="Глава 4. Проба пера", status="published"))
        db.add(AudioFile(id="a1", book_code="КП", original_filename="d.wav", stored_key="k/d.wav",
                         mime_type="audio/wav", size_bytes=10, chapter="Глава 4. Проба пера",
                         role="Дгарнин", actor_name="Сергей Зотов", kind="take"))
        _say(db, f"{chapter_id}:00000", "Дгарнин", 21, 38)
        _say(db, f"{chapter_id}:00001", "Дгарнин", 15, 27)
        db.commit()
        return db

    def _heard(self, *texts):
        segments, clock = [], 0.0
        for text in texts:
            segments.append({"text": text, "start": clock, "end": clock + 2.0, "words": []})
            clock += 2.5
        return lambda path: {"text": " ".join(texts), "segments": segments}

    def test_everything_read_gives_full_coverage(self, studio):
        from app.services.asr_run import run_asr_for_take

        job = run_asr_for_take(studio, "a1", transcribe=self._heard("Я не пойду туда", "Там холодно"))

        assert job.status == "done"
        assert job.coverage == pytest.approx(1.0)
        assert job.expected_role == "Дгарнин"

    def test_a_skipped_line_is_written_down(self, studio):
        import json

        from app.services.asr_run import run_asr_for_take

        job = run_asr_for_take(studio, "a1", transcribe=self._heard("Я не пойду туда"))

        assert job.coverage == pytest.approx(0.5)
        payload = json.loads(job.alignment_json)
        assert payload["missing"] == [1]
        assert payload["lines"][1]["text"] == "Там холодно."

    def test_an_audition_is_not_checked_against_a_chapter(self, studio):
        from app.models import AudioFile
        from app.services.asr_run import AsrRunError, run_asr_for_take

        studio.query(AudioFile).filter(AudioFile.id == "a1").one().kind = "audition"
        studio.flush()

        with pytest.raises(AsrRunError, match="not_a_take"):
            run_asr_for_take(studio, "a1", transcribe=self._heard("что-нибудь"))

    def test_a_file_we_do_not_have(self, studio):
        from app.services.asr_run import AsrRunError, run_asr_for_take

        with pytest.raises(AsrRunError, match="audio_not_found"):
            run_asr_for_take(studio, "нет-такого", transcribe=self._heard("х"))

    def test_a_failure_is_recorded_not_swallowed(self, studio):
        from app.services.asr_run import run_asr_for_take
        from app.services.asr_transcribe import AsrError

        def explode(path):
            raise AsrError("asr_http_429")

        job = run_asr_for_take(studio, "a1", transcribe=explode)

        assert job.status == "failed"
        assert job.error_message == "asr_http_429"
        assert job.coverage == pytest.approx(0.0)


class TestTheWholeChapterInOrder:
    """Порядок реплик главы — не по ролям, а как в тексте: он и есть порядок звучания.

    `expected_lines` отвечает на вопрос диктора «что мне читать». Этот отвечает на
    вопрос монтажёра «что за чем идёт», и без него сессию не собрать черновиком.
    """

    def test_replicas_come_in_the_order_of_the_text_not_of_the_roles(self, chapter):
        db, chapter_id = chapter
        from app.services.asr_run import chapter_replicas

        _say(db, f"{chapter_id}:00000", "Рассказчик", 0, 18)
        _say(db, f"{chapter_id}:00000", "Дгарнин", 21, 38)
        _say(db, f"{chapter_id}:00001", "Сатухух", 0, 12)
        _say(db, f"{chapter_id}:00001", "Дгарнин", 15, 27)

        rows = chapter_replicas(db, chapter_id)

        assert [row["role"] for row in rows] == ["Рассказчик", "Дгарнин", "Сатухух", "Дгарнин"]

    def test_each_one_knows_which_of_its_roles_lines_it_is(self, chapter):
        """Выравнивание считает реплики внутри роли; связать их можно только этим номером."""
        db, chapter_id = chapter
        from app.services.asr_run import chapter_replicas

        _say(db, f"{chapter_id}:00000", "Дгарнин", 21, 38)
        _say(db, f"{chapter_id}:00001", "Сатухух", 0, 12)
        _say(db, f"{chapter_id}:00001", "Дгарнин", 15, 27)

        rows = chapter_replicas(db, chapter_id)

        assert [(row["role"], row["role_index"]) for row in rows] == [
            ("Дгарнин", 0), ("Сатухух", 0), ("Дгарнин", 1),
        ]

    def test_the_text_is_the_one_the_actor_read(self, chapter):
        db, chapter_id = chapter
        from app.services.asr_run import chapter_replicas

        _say(db, f"{chapter_id}:00000", "Дгарнин", 21, 38)

        assert chapter_replicas(db, chapter_id)[0]["text"] == "Я не пойду туда,"

    def test_it_agrees_with_what_the_actor_was_given(self, chapter):
        """Два способа спросить одно и то же должны давать одно и то же."""
        db, chapter_id = chapter
        from app.services.asr_run import chapter_replicas, expected_lines

        _say(db, f"{chapter_id}:00000", "Дгарнин", 21, 38)
        _say(db, f"{chapter_id}:00001", "Сатухух", 0, 12)
        _say(db, f"{chapter_id}:00001", "Дгарнин", 15, 27)

        from_chapter = [row["text"] for row in chapter_replicas(db, chapter_id) if row["role"] == "Дгарнин"]

        assert from_chapter == expected_lines(db, chapter_id, "Дгарнин")

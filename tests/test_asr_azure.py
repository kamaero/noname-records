"""Распознавание через Azure — второй провайдер за тем же швом.

Наверх ничего не меняется: выравнивание получает те же сегменты со временем, что и от
whisper. Меняется только то, кто их произвёл. Ради этого шов и заводился.

У Azure два отличия, которые надо перевести на нашу сторону. Время он считает в
миллисекундах и отдаёт длительностью, а не концом отрезка. И он принимает список фраз —
подсказку, каких слов ждать; для книги, полной выдуманных имён вроде «Бимькмолепуса»,
это не украшение, а разница между «реплика найдена» и «реплика пропущена».
"""
import pytest

# Модели импортируются здесь, а не внутри фикстуры: `Base.metadata.create_all` создаёт
# только те таблицы, чьи классы к тому моменту объявлены. Импорт внутри теста делал
# результат зависимым от порядка тестов.
from app.models import AudioFile, Character, ScriptBook, ScriptChapter  # noqa: F401
from app.services.asr_azure import (
    MAX_PHRASES,
    build_definition,
    parse_azure_transcription,
    transcribe_file_azure,
)
from app.services.asr_transcribe import AsrError


class TestReadingTheAnswer:
    def test_milliseconds_become_seconds(self):
        result = parse_azure_transcription({
            "combinedPhrases": [{"text": "Я не пойду туда"}],
            "phrases": [{"offsetMilliseconds": 960, "durationMilliseconds": 640, "text": "Я не пойду туда", "words": []}],
        })

        assert result["segments"][0]["start"] == pytest.approx(0.96)
        assert result["segments"][0]["end"] == pytest.approx(1.6)

    def test_words_keep_their_own_times(self):
        result = parse_azure_transcription({
            "combinedPhrases": [{"text": "Я не"}],
            "phrases": [{
                "offsetMilliseconds": 0, "durationMilliseconds": 1000, "text": "Я не",
                "words": [
                    {"text": "Я", "offsetMilliseconds": 100, "durationMilliseconds": 200},
                    {"text": "не", "offsetMilliseconds": 400, "durationMilliseconds": 300},
                ],
            }],
        })

        assert result["segments"][0]["words"] == [
            {"word": "Я", "start": 0.1, "end": 0.3},
            {"word": "не", "start": 0.4, "end": 0.7},
        ]

    def test_the_whole_text_comes_from_the_combined_phrases(self):
        result = parse_azure_transcription({
            "combinedPhrases": [{"text": "Первое. Второе."}],
            "phrases": [{"offsetMilliseconds": 0, "durationMilliseconds": 100, "text": "Первое.", "words": []}],
        })

        assert result["text"] == "Первое. Второе."

    def test_an_empty_answer_is_not_a_crash(self):
        assert parse_azure_transcription({}) == {"text": "", "segments": []}

    def test_the_shape_is_the_one_the_aligner_expects(self):
        """Тот же вид, что у whisper: выравнивание не должно знать, кто слушал."""
        from app.services.asr_align import align_transcript

        heard = parse_azure_transcription({
            "combinedPhrases": [{"text": "Я не пойду туда"}],
            "phrases": [{"offsetMilliseconds": 0, "durationMilliseconds": 2000, "text": "Я не пойду туда", "words": []}],
        })
        result = align_transcript(["Я не пойду туда"], heard["segments"])

        assert result["lines"][0]["matched"] is True


class TestTellingItWhatToExpect:
    def test_the_locale_and_the_phrases_go_in(self):
        definition = build_definition(locale="ru-RU", phrases=["Бимькмолепус", "Полумрак"])

        assert definition["locales"] == ["ru-RU"]
        assert definition["phraseList"]["phrases"] == ["Бимькмолепус", "Полумрак"]

    def test_without_phrases_the_key_is_not_sent_at_all(self):
        """Пустой список — не то же, что отсутствие списка; сервис вправе поспорить."""
        assert "phraseList" not in build_definition(locale="ru-RU", phrases=[])

    def test_too_many_phrases_are_cut_not_sent(self):
        definition = build_definition(locale="ru-RU", phrases=[f"имя{n}" for n in range(MAX_PHRASES + 50)])

        assert len(definition["phraseList"]["phrases"]) == MAX_PHRASES

    def test_blanks_and_repeats_do_not_take_up_room(self):
        definition = build_definition(locale="ru-RU", phrases=["Дгарнин", "  ", "Дгарнин", "Сатухух"])

        assert definition["phraseList"]["phrases"] == ["Дгарнин", "Сатухух"]


class TestSendingIt:
    def _response(self, status=200, payload=None):
        class _R:
            status_code = status
            text = "нет"

            @staticmethod
            def json():
                return payload or {"combinedPhrases": [{"text": "ок"}], "phrases": []}

        return _R()

    def test_it_goes_to_the_fast_transcription_endpoint_with_the_key(self, tmp_path, monkeypatch):
        sent = {}

        def fake_post(url, headers=None, files=None, data=None, timeout=None):
            sent.update({"url": url, "headers": headers, "data": data})
            return self._response()

        monkeypatch.setattr("app.services.asr_azure.requests.post", fake_post)
        path = tmp_path / "a.mp3"
        path.write_bytes(b"x")

        transcribe_file_azure(str(path), key="k", endpoint="https://studio.cognitiveservices.azure.com", locale="ru-RU")

        assert "/speechtotext/transcriptions:transcribe" in sent["url"]
        assert "api-version=" in sent["url"]
        assert sent["headers"]["Ocp-Apim-Subscription-Key"] == "k"
        assert "definition" in sent["data"]

    def test_a_trailing_slash_on_the_endpoint_does_not_double_up(self, tmp_path, monkeypatch):
        sent = {}
        monkeypatch.setattr(
            "app.services.asr_azure.requests.post",
            lambda url, **kw: sent.update({"url": url}) or self._response(),
        )
        path = tmp_path / "a.mp3"
        path.write_bytes(b"x")

        transcribe_file_azure(str(path), key="k", endpoint="https://studio.cognitiveservices.azure.com/", locale="ru-RU")

        assert "azure.com//" not in sent["url"]

    def test_without_a_key_or_endpoint_we_do_not_pretend(self, tmp_path):
        path = tmp_path / "a.mp3"
        path.write_bytes(b"x")

        with pytest.raises(AsrError, match="no_api_key"):
            transcribe_file_azure(str(path), key="", endpoint="https://x.azure.com", locale="ru-RU")
        with pytest.raises(AsrError, match="no_endpoint"):
            transcribe_file_azure(str(path), key="k", endpoint="", locale="ru-RU")

    def test_a_refusal_is_reported_with_its_code(self, tmp_path, monkeypatch):
        monkeypatch.setattr("app.services.asr_azure.requests.post", lambda *a, **k: self._response(status=401))
        path = tmp_path / "a.mp3"
        path.write_bytes(b"x")

        with pytest.raises(AsrError, match="asr_http_401"):
            transcribe_file_azure(str(path), key="k", endpoint="https://x.azure.com", locale="ru-RU")


class TestChoosingTheProvider:
    """Кто слушает — настройка, а не развилка по коду в десяти местах."""

    def _audio(self, db):
        from app.time_utils import utcnow_naive

        book = ScriptBook(id="b1", title="Крылья полумрака", source_filename="k.txt",
                          source_format="txt", created_at=utcnow_naive())
        db.add(book)
        db.add(ScriptChapter(id="ch1", book_id="b1", chapter_index=4,
                             chapter_title="Глава 4. Проба", status="published"))
        db.add(Character(book_id="b1", name="Бимькмолепус", aliases="Хальтрек, Барон"))
        db.add(Character(book_id="b1", name="Дгарнин"))
        db.add(AudioFile(id="a1", book_code="КП", original_filename="d.wav", stored_key="k/d.wav",
                         mime_type="audio/wav", size_bytes=10, chapter="Глава 4. Проба",
                         role="Дгарнин", actor_name="Зотов", kind="take"))
        db.commit()

    @pytest.fixture()
    def db(self):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        from app.db import Base

        engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(engine)
        with sessionmaker(bind=engine)() as session:
            self._audio(session)
            yield session

    def test_the_names_of_the_book_become_the_hint(self, db):
        from app.services.asr_run import book_phrases

        phrases = book_phrases(db, "b1")

        assert "Бимькмолепус" in phrases
        assert "Дгарнин" in phrases
        assert "Барон" in phrases, "алиасы тоже звучат вслух"

    def test_a_book_nobody_populated_gives_no_hint(self, db):
        from app.services.asr_run import book_phrases

        assert book_phrases(db, "нет-такой-книги") == []

    def test_azure_is_called_when_it_is_chosen(self, db, monkeypatch):
        from app.config import settings
        from app.services import asr_run

        called = {}
        monkeypatch.setattr(settings, "asr_provider", "azure")
        monkeypatch.setattr(settings, "azure_speech_key", "k")
        monkeypatch.setattr(settings, "azure_speech_endpoint", "https://x.azure.com")
        monkeypatch.setattr(asr_run, "compress_for_asr", lambda src, dst: dst)
        monkeypatch.setattr(
            asr_run, "transcribe_file_azure",
            lambda path, **kw: called.update(kw) or {"text": "ок", "segments": []},
        )
        monkeypatch.setattr(asr_run, "transcribe_file", lambda *a, **k: pytest.fail("не тот провайдер"))

        asr_run.run_asr_for_take(db, "a1")

        assert called["key"] == "k"
        assert "Бимькмолепус" in called["phrases"], "подсказка со списком имён — весь смысл затеи"

    def test_openai_is_called_when_it_is_chosen(self, db, monkeypatch):
        from app.config import settings
        from app.services import asr_run

        called = {}
        monkeypatch.setattr(settings, "asr_provider", "openai")
        monkeypatch.setattr(settings, "openai_api_key", "k")
        monkeypatch.setattr(asr_run, "compress_for_asr", lambda src, dst: dst)
        monkeypatch.setattr(asr_run, "transcribe_file", lambda path, **kw: called.update(kw) or {"text": "ок", "segments": []})
        monkeypatch.setattr(asr_run, "transcribe_file_azure", lambda *a, **k: pytest.fail("не тот провайдер"))

        asr_run.run_asr_for_take(db, "a1")

        assert called["model"] == settings.asr_model


    def test_the_site_choice_wins_over_env(self, db, monkeypatch):
        from app.config import settings
        from app.services import asr_run, step_models

        called = {}
        monkeypatch.setattr(settings, "asr_provider", "openai")
        monkeypatch.setattr(step_models, "step_model", lambda key: ("routerai", "vendor/asr-model"))
        monkeypatch.setattr(settings, "routerai_api_key", "rk")
        monkeypatch.setattr(asr_run, "compress_for_asr", lambda src, dst: dst)
        monkeypatch.setattr(asr_run, "transcribe_file", lambda path, **kw: called.update(kw) or {"text": "ок", "segments": []})

        asr_run.run_asr_for_take(db, "a1")

        assert (called["model"], called["api_key"]) == ("vendor/asr-model", "rk")


class TestRouterAi:
    """RouterAI — тот же формат, что у OpenAI, но свой адрес, ключ и модели."""

    @pytest.fixture()
    def db(self):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        from app.db import Base

        engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(engine)
        with sessionmaker(bind=engine)() as session:
            TestChoosingTheProvider()._audio(session)
            yield session

    def test_it_goes_to_routerai_with_the_routerai_key(self, db, monkeypatch):
        from app.config import settings
        from app.services import asr_run

        called = {}
        monkeypatch.setattr(settings, "asr_provider", "routerai")
        monkeypatch.setattr(settings, "routerai_api_key", "sk-router")
        monkeypatch.setattr(settings, "asr_model", "microsoft/mai-transcribe-2")
        monkeypatch.setattr(asr_run, "compress_for_asr", lambda src, dst: dst)
        monkeypatch.setattr(asr_run, "transcribe_file", lambda path, **kw: called.update(kw) or {"text": "ок", "segments": []})

        asr_run.run_asr_for_take(db, "a1")

        assert called["api_key"] == "sk-router"
        assert called["base_url"] == settings.routerai_base_url
        assert called["model"] == "microsoft/mai-transcribe-2"

    def test_the_names_of_the_book_go_along_as_a_hint(self, db, monkeypatch):
        from app.config import settings
        from app.services import asr_run

        called = {}
        monkeypatch.setattr(settings, "asr_provider", "routerai")
        monkeypatch.setattr(settings, "routerai_api_key", "sk-router")
        monkeypatch.setattr(settings, "asr_prompt", "")
        monkeypatch.setattr(asr_run, "compress_for_asr", lambda src, dst: dst)
        monkeypatch.setattr(asr_run, "transcribe_file", lambda path, **kw: called.update(kw) or {"text": "ок", "segments": []})

        asr_run.run_asr_for_take(db, "a1")

        assert "Бимькмолепус" in called["prompt"]

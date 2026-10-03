"""Как называется аудиофайл в студии. Одно имя — у диктора на диске и у нас в базе.

Стандарт задал владелец:

    утверждённая роль   KP_Ch01_Dgarnin_Zotov.wav
    проба на роль       KP_Ch01_Osniva_NataliGolubeva_proba.wav

Шаблоны отличаются одним — хвостом `_proba`, — и это единственное, чем проба
отличается от дубля. Глава у пробы необязательна: читают её из какого-то куска, но
роль ещё не назначена, и требовать главу нельзя — именно это требование и заставило
диктора писать «пробы» внутрь имени файла.

Раньше сервер переименовывал загруженное по-своему, кириллицей
(«КП_Глава01_Дгарнин_Zotov.wav»), и в студии жили два написания одного файла.
"""
import pytest

from app.services.audio_naming import (
    book_token,
    canonical_audio_name,
    chapter_token,
    latin_word,
)


class TestTheStandardItself:
    def test_an_approved_role(self):
        name = canonical_audio_name(
            book_code="КП", chapter="Глава 1. Ничего особенного", role="Дгарнин",
            actor_name="Зотов", original_filename="запись.wav",
        )

        assert name == "KP_Ch01_Dgarnin_Zotov.wav"

    def test_an_audition(self):
        name = canonical_audio_name(
            book_code="КП", chapter="Глава 1. Ничего особенного", role="Оснива",
            actor_name="Натали Голубева", original_filename="проба.wav", kind="audition",
        )

        assert name == "KP_Ch01_Osniva_NataliGolubeva_proba.wav"

    def test_an_audition_read_from_nowhere_in_particular(self):
        name = canonical_audio_name(
            book_code="КП", chapter="", role="Оснива",
            actor_name="Натали Голубева", original_filename="проба.wav", kind="audition",
        )

        assert name == "KP_Osniva_NataliGolubeva_proba.wav"

    def test_a_second_attempt_at_the_same_role(self):
        name = canonical_audio_name(
            book_code="КП", chapter="", role="Оснива", actor_name="Натали Голубева",
            original_filename="ещё.wav", kind="audition", ordinal=2,
        )

        assert name == "KP_Osniva_NataliGolubeva_proba_2.wav"


class TestTheWordsInside:
    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            ("Дгарнин", "Dgarnin"),
            ("Слуга Билсима", "SlugaBilsima"),
            ("Царь Клопов", "TsarKlopov"),
            ("Тот-Кто-Знает", "TotKtoZnaet"),
            ("Pavel Petrovich", "PavelPetrovich"),
            ("Зарина Мельникова (Яковлева)", "ZarinaMelnikovaYakovleva"),
            ("Дгарни́н", "Dgarnin"),
        ],
    )
    def test_a_name_becomes_one_latin_word(self, source, expected):
        assert latin_word(source) == expected

    def test_a_book_code_is_the_same_code_in_latin(self):
        assert book_token("КП") == "KP"
        assert book_token("KP") == "KP"

    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            ("Глава 1. Ничего особенного", "Ch01"),
            ("Глава 32. Я ваш щит", "Ch32"),
            ("Глава 100", "Ch100"),
            ("Ch04", "Ch04"),
            ("", ""),
            ("Пролог", ""),
        ],
    )
    def test_a_chapter_becomes_its_number(self, source, expected):
        assert chapter_token(source) == expected


class TestWhatIsMissing:
    def test_a_role_nobody_named(self):
        name = canonical_audio_name(
            book_code="КП", chapter="Глава 4", role="", actor_name="Зотов", original_filename="x.wav",
        )

        assert name == "KP_Ch04_Role_Zotov.wav"

    def test_an_actor_nobody_named(self):
        name = canonical_audio_name(
            book_code="КП", chapter="Глава 4", role="Дгарнин", actor_name="", original_filename="x.wav",
        )

        assert name == "KP_Ch04_Dgarnin_Unknown.wav"

    def test_a_take_without_a_chapter_still_makes_a_name(self):
        """Ошибку об отсутствующей главе ловит проверка загрузки, а не имя файла."""
        name = canonical_audio_name(
            book_code="КП", chapter="", role="Дгарнин", actor_name="Зотов", original_filename="x.wav",
        )

        assert name == "KP_Dgarnin_Zotov.wav"

    def test_the_extension_survives_and_is_lowercased(self):
        name = canonical_audio_name(
            book_code="КП", chapter="Глава 4", role="Дгарнин", actor_name="Зотов", original_filename="X.WAV",
        )

        assert name.endswith(".wav")

    def test_a_file_with_no_extension_is_assumed_to_be_wav(self):
        name = canonical_audio_name(
            book_code="КП", chapter="Глава 4", role="Дгарнин", actor_name="Зотов", original_filename="запись",
        )

        assert name.endswith(".wav")

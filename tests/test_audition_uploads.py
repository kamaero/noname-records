"""Пробы на роль — это отдельный вид загрузки, а не глава.

Дикторы начали присылать пробы вперемешку с дублями утверждённых ролей. Форма умеет
говорить только «дубль такой-то главы», поэтому проба уезжала главой 2, а слово
«пробы» диктор дописывал в имя файла — единственное место, где его было куда написать.
Имя файла стало комментарием к системе.

Главу у пробы не требуют: роль ещё не назначена. Назвать её можно — из какого куска
читали, — но именно требование главы и загоняло слово «пробы» внутрь имени файла.

Как называется файл — в `tests/test_audio_naming.py`; здесь про то, чего проба требует.
"""
import pytest

from app.services.audio_uploads import (
    apply_batch_overrides,
    filename_names_role,
    parse_batch_audio_filename,
)


def _norm(value):
    return (value or "").strip()


def _parse(filename, book_code="КП", actor="Иванова"):
    """As the panel calls it: the book code and the actor come from the session, not the name."""
    return parse_batch_audio_filename(
        filename, default_book_code=book_code, default_actor_name=actor, normalize_role_label=_norm,
    )


def _override(parsed, override):
    return apply_batch_overrides(parsed, override, normalize_role_label=_norm)


class TestWhatAnAuditionNeeds:
    def test_no_chapter_is_asked_for(self):
        parsed = _override(_parse("Энея_пробы.wav", actor="Ершова"), {"kind": "audition", "role": "Эней", "actor_name": "Ершова", "book_code": "КП"})

        assert "chapter_not_found" not in parsed["errors"]
        assert parsed["ok"]

    def test_a_role_is_still_required(self):
        """Проба без роли — файл, который некому слушать."""
        parsed = _override(_parse("проба.wav"), {"kind": "audition", "role": "", "actor_name": "Ершова"})

        assert "role_required" in parsed["errors"]
        assert not parsed["ok"]

    def test_a_take_still_demands_its_chapter(self):
        parsed = _override(_parse("Химера.wav"), {"kind": "take", "role": "Химера", "actor_name": "Иванова", "chapter": ""})

        assert "chapter_not_found" in parsed["errors"]

    def test_the_word_проба_is_not_mistaken_for_the_role(self):
        parsed = _parse("Энея_пробы.wav", actor="Ершова")

        assert parsed["role"] == "Энея"


class TestWhenTheFilenameAndTheFormDisagree:
    """Сомов выбрал в форме «Сатухух», а прислал 03-Za'Maor.wav. Никто не заметил."""

    def test_a_different_name_in_the_file_is_flagged(self):
        parsed = _override(_parse("03-Za'Maor.wav", actor="Сомов"), {"role": "Сатухух", "chapter": "Глава3", "actor_name": "Сомов"})

        assert "role_name_mismatch" in parsed["warnings"]

    def test_a_flag_is_not_a_refusal(self):
        """Роль в имени файла — догадка парсера; решает диктор, а не парсер."""
        parsed = _override(_parse("03-Za'Maor.wav", actor="Сомов"), {"role": "Сатухух", "chapter": "Глава3", "actor_name": "Сомов"})

        assert parsed["ok"]

    def test_the_same_name_in_both_places_says_nothing(self):
        parsed = _override(_parse("KP_CH28_Sluga_Bilsima_Zotov.wav", book_code="KP", actor="Zotov"), {"role": "Слуга Билсима", "chapter": "Глава28", "actor_name": "Зотов"})

        assert "role_name_mismatch" not in parsed["warnings"]

    def test_a_filename_with_no_role_in_it_is_not_a_disagreement(self):
        parsed = _override(_parse("KP_CH04.wav"), {"role": "Химера", "chapter": "Глава4", "actor_name": "Иванова"})

        assert "role_name_mismatch" not in parsed["warnings"]


@pytest.mark.parametrize(
    ("in_file", "chosen", "same"),
    [
        ("Эней", "Эней", True),
        ("Энея", "Эней", True),          # склонение — та же роль
        ("Za'Maor", "Сатухух", False),
        ("Sluga Bilsima", "Слуга Билсима", True),
        ("Химера", "Дгарнин", False),
    ],
)
def test_filename_names_role(in_file, chosen, same):
    assert filename_names_role(in_file, chosen) is same

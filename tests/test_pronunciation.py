import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.pronunciation import (
    apply_manual_pronunciation_overrides,
    apply_pronunciation_dictionary,
    build_pronunciation_variants,
    detect_unknown_words,
    parse_pronunciation_notes,
)


def test_parse_pronunciation_notes_parses_simple_pairs() -> None:
    result = parse_pronunciation_notes("договор=догово́р")
    assert result["договор"] == "догово́р"


def test_apply_pronunciation_dictionary_applies_manual_override() -> None:
    result, info = apply_pronunciation_dictionary("В договор включили пункт.", "договор=догово́р")
    assert "догово́р" in result
    assert info["applied"] >= 1


def test_detect_unknown_words_respects_extra_known_words() -> None:
    text = " ".join(["Ромашкин"] * 10)
    result = detect_unknown_words(text, extra_known_words={"Ромашкин"})
    words = [item["word"].lower() for item in result]
    assert "ромашкин" not in words


def test_build_pronunciation_variants_expands_matching_word_family() -> None:
    variants = build_pronunciation_variants(
        "пресвитер",
        "пресви́тер",
        ["Пресвитер", "пресвитера", "пресвитером", "пресвитеры", "договор"],
    )

    assert variants["пресвитер"] == "пресви́тер"
    assert variants["пресвитера"] == "пресви́тера"
    assert variants["пресвитером"] == "пресви́тером"
    assert variants["пресвитеры"] == "пресви́теры"
    assert "договор" not in variants


def test_apply_manual_pronunciation_overrides_updates_all_detected_forms() -> None:
    mapping = {
        "пресвитер": "пресви́тер",
        "пресвитера": "пресви́тера",
        "пресвитером": "пресви́тером",
    }

    result, applied = apply_manual_pronunciation_overrides(
        "Пресвитер видел пресвитера и спорил с пресвитером.",
        mapping,
    )

    assert "Пресви́тер" in result
    assert "пресви́тера" in result
    assert "пресви́тером" in result
    assert applied == 3


def test_satuhuh_family_accent_applies_to_base_and_declined_form() -> None:
    variants = build_pronunciation_variants(
        "сатухух",
        "сатуху́х",
        ["Сатухух", "Сатухуха", "Сатухуху", "металл"],
    )

    result, applied = apply_manual_pronunciation_overrides(
        "У Сатухуха медная шкура. Сатухух сумеет.",
        variants,
    )

    assert variants["сатухух"] == "сатуху́х"
    assert variants["сатухуха"] == "сатуху́ха"
    assert "Сатуху́ха" in result
    assert "Сатуху́х" in result
    assert applied == 2


def test_apply_manual_pronunciation_overrides_preserves_case_pattern() -> None:
    mapping = {
        "сатухух": "сатуху́х",
        "сатухуха": "сатуху́ха",
    }

    result, applied = apply_manual_pronunciation_overrides(
        "САТУХУХ. Сатухух. сатухуха.",
        mapping,
    )

    assert "САТУХУ́Х" in result
    assert "Сатуху́х" in result
    assert "сатуху́ха" in result
    assert applied == 3


def test_apply_manual_pronunciation_overrides_collapses_duplicate_stress_marks() -> None:
    mapping = {
        "дгарнин": "дгарни́́н",
    }

    result, applied = apply_manual_pronunciation_overrides(
        "Дгарнин пришёл.",
        mapping,
    )

    assert "Дгарни́н" in result
    assert "Дгарни́́н" not in result
    assert applied == 1


def test_manual_override_replaces_existing_wrong_stress() -> None:
    mapping = {
        "сатухух": "сатуху́х",
        "сатухуха": "сатуху́ха",
    }

    result, applied = apply_manual_pronunciation_overrides(
        "Сату́хух увидел Сату́хуха.",
        mapping,
    )

    assert "Сатуху́х" in result
    assert "Сатуху́ха" in result
    assert "Сату́хух" not in result
    assert "Сату́хуха" not in result
    assert applied == 2

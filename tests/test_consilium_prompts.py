"""Промпты консилиума перенесены из откалиброванных скриптов 2026-09-12 — без изменения смысла."""
from app.pipeline.consilium_prompts import (
    ARBITER_SCHEMA, ARBITER_SYSTEM, READER_SCHEMA, READER_SYSTEM,
    arbiter_user_prompt, as_pair, normalize_id, reader_user_prompt,
)


def test_ids_in_any_spelling_are_the_same_paragraph():
    assert normalize_id("#00017") == normalize_id("00017") == normalize_id("17") == 17
    assert normalize_id("абзац") == -1


def test_reader_answers_are_read_in_both_shapes_the_models_give():
    """На главе 25 чтец вернул массив строк «#00123 Дгарнин» и формально не нарушил схему."""
    assert as_pair({"id": "#00017", "speaker": " Дгарнин ", "confidence": 0.9}) == (17, "Дгарнин")
    assert as_pair("#00123 → Гамук") == (123, "Гамук")
    assert as_pair("#00123") is None
    assert as_pair({"id": "без номера", "speaker": "Гамук"}) is None
    assert as_pair(42) is None


def test_the_reader_sees_only_its_own_last_decisions_as_context():
    already = [(n, "Гамук") for n in range(30)]
    user = reader_user_prompt(["- Гамук — демон"], [(30, "— Идём.")], already)
    assert "КОНТЕКСТ — твои же предыдущие решения по этой главе (за них не отвечай):" in user
    assert "#00005 → Гамук" in user and "#00004 → Гамук" not in user  # только последние 25
    assert user.index("ПЕРСОНАЖИ ГЛАВЫ:") < user.index("КОНТЕКСТ") < user.index("АБЗАЦЫ:\n#00030 — Идём.")
    assert "КОНТЕКСТ" not in reader_user_prompt(["- Гамук"], [(0, "x")], [])


def test_the_arbiter_is_not_told_whose_version_is_whose():
    user = arbiter_user_prompt(["- Гамук — демон"], [(62, "а"), (63, "б")], 63, ["Гамук", "Тупуг"])
    assert "СПОРНЫЙ АБЗАЦ: #00063" in user
    assert "ВЕРСИИ: «Гамук»; «Тупуг»" in user
    assert "сценари" not in user.lower() and "чтец" not in user.lower()


def test_the_calibrated_wording_is_kept():
    assert "«UNSURE» — нормальный ответ, а не поражение." in READER_SYSTEM
    assert "context_only никогда ничего не закрывает" in ARBITER_SYSTEM
    assert READER_SCHEMA["required"] == ["lines"]
    assert ARBITER_SCHEMA["required"] == ["speaker", "evidence_kind", "evidence_para", "evidence_quote", "reason"]

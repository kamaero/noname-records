"""Звуковая разметка: разбор и проверка ответа модели — без сети и базы."""
from app.pipeline.sound_markers import chapter_prompt, find_quote, norm, validate

PARAS = [(0, "Глава 3"), (1, "Дверь распахнулась, и в зал ввалился Дгарнин."),
         (2, "— Эля, — бросил он."), (3, "Вдали ударил гром."), (4, "Ночь прошла тихо.")]


def scene(ordinal, **place):
    return {"start": ordinal, "place": {"id": "", "name": "Таверна", "description": "зал",
                                        "ambience_queries": ["tavern crowd"], **place},
            "time_of_day": "ночь", "weather": "дождь", "ambience": "гомон", "mood": "тревога",
            "music_queries": ["dark suspense"]}


def test_norm_folds_case_yo_and_spaces():
    assert norm("  Ещё   ЁЛКА ") == "еще елка"


def test_quote_found_in_own_paragraph_or_neighbour():
    texts = dict(PARAS)
    assert find_quote(texts, 1, "дверь распахнулась") == 1
    assert find_quote(texts, 1, "ударил гром") == 3        # сосед в пределах ±2
    assert find_quote(texts, 1, "ночь прошла") is None     # дальше ±2 — не ищем
    assert find_quote(texts, 1, "") is None


def test_first_scene_is_pulled_to_chapter_start_and_order_kept():
    got = validate(PARAS, {"scenes": [scene(2), scene(1)], "transitions": [], "sounds": []}, set())
    assert [s["ordinal"] for s in got.scenes] == [0, 2]


def test_unknown_paragraph_and_bad_quote_are_dropped_not_guessed():
    answer = {"scenes": [scene(0)], "transitions": [{"at": 99, "what": "сон"}],
              "sounds": [{"at": 1, "quote": "хлопнула калитка", "description": "калитка", "queries": ["gate"]},
                         {"at": 1, "quote": "ударил гром", "description": "гром", "queries": ["thunder"]}]}
    got = validate(PARAS, answer, set())
    assert got.transitions == []
    assert [(s["ordinal"], s["quote"]) for s in got.sounds] == [(3, "ударил гром")]
    assert {d["reason"] for d in got.dropped} == {"no_paragraph", "quote_not_found"}


def test_unknown_place_id_becomes_new_place():
    got = validate(PARAS, {"scenes": [scene(0, id="nope")], "transitions": [], "sounds": []}, {"p1"})
    assert got.scenes[0]["place"]["id"] == ""
    got = validate(PARAS, {"scenes": [scene(0, id="p1")], "transitions": [], "sounds": []}, {"p1"})
    assert got.scenes[0]["place"]["id"] == "p1"


def test_prompt_numbers_paragraphs_and_lists_places():
    text = chapter_prompt(PARAS[:2], [{"id": "p1", "name": "Таверна", "description": "зал"}])
    assert "[1] Дверь распахнулась" in text and "p1 — Таверна: зал" in text

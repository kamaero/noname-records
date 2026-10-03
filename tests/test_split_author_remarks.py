"""Разрез слов автора внутри уже размеченной реплики — только чистые функции и
поддельная модель. Ошибка в опасную сторону — когда речь персонажа уезжает
Рассказчику, — поэтому большая часть тестов здесь про guard-функции, которые эту
ошибку не пропускают.
"""
import json
import re

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.v2.attribute import NARRATOR
from app.v2.models import V2Attribution

from scripts.split_author_remarks import (
    Candidate,
    apply_plan,
    ask_batch,
    format_report,
    guard_character_kept_speech,
    guard_first_reply_belongs_to_character,
    guard_known_speakers,
    guard_narrator_dash_after_sentence_end_uppercase,
    guard_narrator_piece_has_no_inner_dash,
    guard_narrator_share,
    guard_spans_valid,
    process_candidates,
    propose_split,
    render_batch_prompt,
    select_candidates,
)
from scripts.split_author_remarks import _parse_items, _tokens_for  # noqa: E402 — приватные, но проверяем сами


def _tokens_in_prompt(user_prompt: str) -> list[str]:
    """Токены, которые фактически ушли в промпт — тем же способом, каким их читал бы
    внимательный человек: `#<токен> (текущий голос: ...)`."""
    return re.findall(r"#(\S+) \(текущий голос", user_prompt)

GARIADOLL_TEXT = "- Ну что ещё? – устало потёр пальцами виски Петуунегр. – Полибнугак, это уже похоже на манию."

KSAURR_TEXT = (
    "- Болота… - впервые за вечер отозвался Бдеукс. Ростом всего лишь с крупного "
    "пса, он все это время лениво грелся у огня, как старый кот. – Это не похоже на "
    "лёгкую прогулку. Но эти Топи… хм… нет… вдруг промокнут лапы?.. Гнилая Трясина – это "
    "не шутка. Я могу простыть ненароком. Я не пойду."
)

KSAURR_QUOTE = (
    "- впервые за вечер отозвался Бдеукс. Ростом всего лишь с "
    "крупного пса, он все это время лениво грелся у огня, как старый кот."
)


def _para(text, spans, ordinal=1, segment_id="s1"):
    return {"segment_id": segment_id, "ordinal": ordinal, "text": text, "spans": spans}


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)


# --- 1. отбор кандидатов -----------------------------------------------------------


def test_select_candidates_accepts_single_full_span_with_inner_dash():
    paragraphs = [_para(GARIADOLL_TEXT, [{"start": 0, "end": len(GARIADOLL_TEXT), "speaker": "Петуунегр"}])]
    result = select_candidates(paragraphs)
    assert [c.segment_id for c in result] == ["s1"]
    assert result[0].speaker == "Петуунегр"


def test_select_candidates_skips_narrator_paragraph():
    paragraphs = [_para(GARIADOLL_TEXT, [{"start": 0, "end": len(GARIADOLL_TEXT), "speaker": NARRATOR}])]
    assert select_candidates(paragraphs) == []


def test_select_candidates_skips_paragraph_with_two_attributions():
    half = len(GARIADOLL_TEXT) // 2
    spans = [
        {"start": 0, "end": half, "speaker": "Петуунегр"},
        {"start": half, "end": len(GARIADOLL_TEXT), "speaker": NARRATOR},
    ]
    assert select_candidates([_para(GARIADOLL_TEXT, spans)]) == []


def test_select_candidates_skips_paragraph_without_inner_dash():
    text = "Полибнугак долго молчал, не зная, что сказать в ответ."
    paragraphs = [_para(text, [{"start": 0, "end": len(text), "speaker": "Полибнугак"}])]
    assert select_candidates(paragraphs) == []


def test_select_candidates_skips_unsure_with_suffix():
    # UNSURE: <уточнение> — полноправная форма «не знаю» (app/v2/cast_ops.py,
    # dispute_ops.py), а не просто «какой-то говорящий».
    paragraphs = [_para(GARIADOLL_TEXT, [{"start": 0, "end": len(GARIADOLL_TEXT), "speaker": "UNSURE: голос из толпы"}])]
    assert select_candidates(paragraphs) == []


def test_select_candidates_skips_narrator_alias_case_insensitive():
    # is_narrator_name сверяет по смыслу, не по точной строке «Рассказчик».
    paragraphs = [_para(GARIADOLL_TEXT, [{"start": 0, "end": len(GARIADOLL_TEXT), "speaker": "рассказчик"}])]
    assert select_candidates(paragraphs) == []


def test_select_candidates_handles_none_text_without_crashing():
    paragraphs = [_para(None, [{"start": 0, "end": 0, "speaker": "Бдеукс"}])]
    assert select_candidates(paragraphs) == []


def test_select_candidates_carries_original_source_and_confidence():
    paragraphs = [_para(GARIADOLL_TEXT, [
        {"start": 0, "end": len(GARIADOLL_TEXT), "speaker": "Петуунегр", "confidence": 0.42, "source": "operator"},
    ])]
    result = select_candidates(paragraphs)
    assert result[0].source == "operator"
    assert result[0].confidence == 0.42


# --- 2. guard: больше 85% Рассказчику ----------------------------------------------


def test_guard_narrator_share_rejects_over_85_percent():
    text = "x" * 100
    spans = [{"start": 0, "end": 90, "speaker": NARRATOR}, {"start": 90, "end": 100, "speaker": "Бдеукс"}]
    reason = guard_narrator_share(spans, text, "Бдеукс")
    assert reason is not None
    assert "Рассказчику" in reason


def test_guard_narrator_share_allows_under_85_percent():
    text = "x" * 100
    spans = [{"start": 0, "end": 50, "speaker": NARRATOR}, {"start": 50, "end": 100, "speaker": "Бдеукс"}]
    assert guard_narrator_share(spans, text, "Бдеукс") is None


def test_guard_narrator_share_counts_narrator_alias_variants():
    text = "x" * 100
    spans = [{"start": 0, "end": 90, "speaker": "рассказчик"}, {"start": 90, "end": 100, "speaker": "Бдеукс"}]
    reason = guard_narrator_share(spans, text, "Бдеукс")
    assert reason is not None


# --- 3. guard: персонажу ничего не осталось ----------------------------------------


def test_guard_character_kept_speech_rejects_when_character_has_nothing():
    text = "x" * 20
    spans = [{"start": 0, "end": 10, "speaker": NARRATOR}, {"start": 10, "end": 20, "speaker": NARRATOR}]
    reason = guard_character_kept_speech(spans, text, "Бдеукс")
    assert reason is not None
    assert "Бдеукс" in reason


def test_guard_character_kept_speech_allows_when_character_present():
    text = "x" * 20
    spans = [{"start": 0, "end": 10, "speaker": NARRATOR}, {"start": 10, "end": 20, "speaker": "Бдеукс"}]
    assert guard_character_kept_speech(spans, text, "Бдеукс") is None


# --- 4. guard: посторонний speaker --------------------------------------------------


def test_guard_known_speakers_rejects_foreign_speaker():
    text = "x" * 20
    spans = [{"start": 0, "end": 10, "speaker": "Дгарнин"}, {"start": 10, "end": 20, "speaker": "Бдеукс"}]
    reason = guard_known_speakers(spans, text, "Бдеукс")
    assert reason is not None
    assert "Дгарнин" in reason


def test_guard_known_speakers_allows_character_and_narrator_only():
    text = "x" * 20
    spans = [{"start": 0, "end": 10, "speaker": NARRATOR}, {"start": 10, "end": 20, "speaker": "Бдеукс"}]
    assert guard_known_speakers(spans, text, "Бдеукс") is None


def test_guard_known_speakers_allows_narrator_alias_variant():
    text = "x" * 20
    spans = [{"start": 0, "end": 10, "speaker": "рассказчик"}, {"start": 10, "end": 20, "speaker": "Бдеукс"}]
    assert guard_known_speakers(spans, text, "Бдеукс") is None


# --- 4а. guard критичные: тире внутри куска Рассказчика, первая реплика -----------


def test_guard_narrator_piece_with_inner_dash_rejects_split():
    # Кусок Рассказчика прихватил возобновление реплики (« – Уже пора») — сигнал,
    # что модель заглотила и саму речь персонажа, не только слова автора.
    text = "- Идём. - сказал Бдеукс. – Уже пора, вставай!"
    narrator_start = text.index("- сказал")
    spans = [
        {"start": 0, "end": narrator_start, "speaker": "Бдеукс"},
        {"start": narrator_start, "end": len(text), "speaker": NARRATOR},
    ]
    reason = guard_narrator_piece_has_no_inner_dash(spans, text, "Бдеукс")
    assert reason is not None
    assert "тире-реплику" in reason


def test_guard_narrator_piece_without_inner_dash_allows_split():
    # Тот же кусок слов автора, что и в живом примере Бдеукса — без внутреннего тире.
    narrator_start = KSAURR_TEXT.index(KSAURR_QUOTE)
    narrator_end = narrator_start + len(KSAURR_QUOTE)
    spans = [
        {"start": 0, "end": narrator_start, "speaker": "Бдеукс"},
        {"start": narrator_start, "end": narrator_end, "speaker": NARRATOR},
        {"start": narrator_end, "end": len(KSAURR_TEXT), "speaker": "Бдеукс"},
    ]
    assert guard_narrator_piece_has_no_inner_dash(spans, KSAURR_TEXT, "Бдеукс") is None


def test_guard_first_reply_not_narrator_rejects_when_paragraph_opens_with_dash():
    text = "- Идём, - сказал Бдеукс. – Уже пора."
    spans = [{"start": 0, "end": 9, "speaker": NARRATOR}, {"start": 9, "end": len(text), "speaker": "Бдеукс"}]
    reason = guard_first_reply_belongs_to_character(spans, text, "Бдеукс")
    assert reason is not None


def test_guard_first_reply_not_narrator_allows_character_at_start():
    text = "- Идём, - сказал Бдеукс. – Уже пора."
    spans = [{"start": 0, "end": 9, "speaker": "Бдеукс"}, {"start": 9, "end": len(text), "speaker": NARRATOR}]
    assert guard_first_reply_belongs_to_character(spans, text, "Бдеукс") is None


def test_guard_first_reply_not_narrator_ignores_paragraphs_not_opening_with_dash():
    text = "Он подумал, что зря сюда пришёл."
    spans = [{"start": 0, "end": 5, "speaker": NARRATOR}, {"start": 5, "end": len(text), "speaker": "Бдеукс"}]
    assert guard_first_reply_belongs_to_character(spans, text, "Бдеукс") is None


# --- 4в. guard критичная (круг 2, расширена в круге 3): тире после конца предложения --


def test_guard_narrator_dash_after_sentence_end_uppercase_rejects_swallowed_reply():
    # Ровно находка ревью: модель объявляет ВТОРУЮ реплику Петуунегра словами автора.
    # Ни одна из шести прежних защит это не видит (см. propose_split-тест ниже).
    quote2 = "– Полибнугак, это уже похоже на манию."
    start = GARIADOLL_TEXT.index(quote2)
    spans = [
        {"start": 0, "end": start, "speaker": "Петуунегр"},
        {"start": start, "end": len(GARIADOLL_TEXT), "speaker": NARRATOR},
    ]
    reason = guard_narrator_dash_after_sentence_end_uppercase(spans, GARIADOLL_TEXT, "Петуунегр")
    assert reason is not None
    assert "большой буквы" in reason


def test_guard_narrator_dash_after_sentence_end_uppercase_allows_dash_after_ellipsis():
    # Живой (хороший) пример Бдеукса: тире Рассказчика стоит после «…», не «.» —
    # если бы регулярка не различала точку от прочих знаков, разрез отклонили бы.
    narrator_start = KSAURR_TEXT.index(KSAURR_QUOTE)
    spans = [
        {"start": 0, "end": narrator_start, "speaker": "Бдеукс"},
        {"start": narrator_start, "end": narrator_start + len(KSAURR_QUOTE), "speaker": NARRATOR},
        {"start": narrator_start + len(KSAURR_QUOTE), "end": len(KSAURR_TEXT), "speaker": "Бдеукс"},
    ]
    assert guard_narrator_dash_after_sentence_end_uppercase(spans, KSAURR_TEXT, "Бдеукс") is None


def test_guard_narrator_dash_after_sentence_end_uppercase_allows_lowercase_continuation():
    # 5,8% законных случаев из измерения: тире после точки, но продолжается со
    # строчной — это и есть слова автора при небрежной пунктуации, не реплика.
    text = "- Ладно. – он замолчал."
    start = text.index("– он")
    spans = [{"start": 0, "end": start, "speaker": "Бдеукс"}, {"start": start, "end": len(text), "speaker": NARRATOR}]
    assert guard_narrator_dash_after_sentence_end_uppercase(spans, text, "Бдеукс") is None


def test_guard_narrator_dash_after_sentence_end_uppercase_rejects_after_question_mark():
    # Круг 3: та же форма заглатывания, что и после точки, показана ревью и после
    # «?» — 0 из 523 живых случаев книги продолжаются с прописной, так что отклонить
    # такое безопасно.
    text = "- Ну что ещё? – Полибнугак, это уже похоже на манию."
    start = text.index("– Полибнугак")
    spans = [{"start": 0, "end": start, "speaker": "Петуунегр"}, {"start": start, "end": len(text), "speaker": NARRATOR}]
    reason = guard_narrator_dash_after_sentence_end_uppercase(spans, text, "Петуунегр")
    assert reason is not None


def test_guard_narrator_dash_after_sentence_end_uppercase_allows_question_mark_lowercase():
    # Законная ремарка вида «— Ты идёшь? — спросил он.» — тире после «?», но
    # продолжается со строчной, поэтому не отклоняется.
    text = "- Ты идёшь? – спросил он."
    start = text.index("– спросил")
    spans = [{"start": 0, "end": start, "speaker": "Бдеукс"}, {"start": start, "end": len(text), "speaker": NARRATOR}]
    assert guard_narrator_dash_after_sentence_end_uppercase(spans, text, "Бдеукс") is None


def test_propose_split_rejects_when_second_reply_is_swallowed_as_author_words():
    # ВАЖНОЕ (круг 2): тест на уровне propose_split, а не голой guard-функции — если
    # убрать любую из критичных защит из GUARDS, этот тест должен упасть. Проверяет
    # именно СВЯЗЬ «guard подключён к пайплайну», не только сам guard в изоляции.
    item = {
        "id": "s1", "speaker": "Петуунегр", "confidence": 0.9,
        "parts": [{"speaker": "Рассказчик", "quote": "– Полибнугак, это уже похоже на манию."}],
    }
    spans, reason = propose_split(item, segment_id="s1", text=GARIADOLL_TEXT, character="Петуунегр")
    assert spans is None
    assert reason is not None


def test_propose_split_rejects_when_author_quote_swallows_resumed_reply():
    # ВАЖНОЕ (круг 3): то же самое, но для защиты 1 (guard_narrator_piece_has_no_inner_dash).
    item = {"id": "s1", "speaker": "Бдеукс", "confidence": 0.9,
            "parts": [{"speaker": "Рассказчик", "quote": KSAURR_QUOTE + " – Это не похоже"}]}
    spans, reason = propose_split(item, segment_id="s1", text=KSAURR_TEXT, character="Бдеукс")
    assert spans is None


def test_propose_split_rejects_when_opening_reply_is_swallowed_as_author_words():
    # ВАЖНОЕ (круг 3): то же самое для защиты 2 (guard_first_reply_belongs_to_character).
    item = {"id": "s1", "speaker": "Петуунегр", "confidence": 0.9,
            "parts": [{"speaker": "Рассказчик", "quote": "- Ну что ещё?"}]}
    spans, reason = propose_split(item, segment_id="s1", text=GARIADOLL_TEXT, character="Петуунегр")
    assert spans is None


# --- 4б. guard: спаны валидны — normalise_spans молча подрезает, не бросает -------


def test_guard_spans_valid_rejects_span_past_text_end():
    text = "x" * 10
    spans = [{"start": 0, "end": 5, "speaker": NARRATOR}, {"start": 5, "end": 20, "speaker": "Бдеукс"}]
    reason = guard_spans_valid(spans, text, "Бдеукс")
    assert reason is not None
    assert "выходит за текст" in reason


def test_guard_spans_valid_allows_in_bounds_spans():
    text = "x" * 10
    spans = [{"start": 0, "end": 5, "speaker": NARRATOR}, {"start": 5, "end": 10, "speaker": "Бдеукс"}]
    assert guard_spans_valid(spans, text, "Бдеукс") is None


def test_guard_spans_valid_rejects_overlapping_spans():
    text = "x" * 10
    spans = [{"start": 0, "end": 6, "speaker": NARRATOR}, {"start": 4, "end": 10, "speaker": "Бдеукс"}]
    reason = guard_spans_valid(spans, text, "Бдеукс")
    assert reason is not None
    assert "normalise_spans" in reason


# --- 5. сборка спанов на живом примере из отчёта -----------------------------------


def test_propose_split_bdeuks_example_splits_into_three_with_middle_narrator():
    # Пример из холостого прогона (remark-dryrun-bbce8bb6.md, абзац 127), переписанный
    # под новое правило: тире возобновлённой речи не входит в цитату слов автора.
    item = {"id": "seg-bdeuks", "speaker": "Бдеукс", "confidence": 0.9,
            "parts": [{"speaker": "Рассказчик", "quote": KSAURR_QUOTE}]}
    spans, reason = propose_split(item, segment_id="seg-bdeuks", text=KSAURR_TEXT, character="Бдеукс")
    assert reason is None
    assert spans is not None
    assert [s["speaker"] for s in spans] == ["Бдеукс", "Рассказчик", "Бдеукс"]
    middle = KSAURR_TEXT[spans[1]["start"]:spans[1]["end"]]
    assert middle.endswith("кот.")
    tail = KSAURR_TEXT[spans[2]["start"]:spans[2]["end"]]
    assert tail.strip().startswith("–")


def test_propose_split_returns_none_when_no_remark_seen():
    item = {"id": "s1", "speaker": "Бдеукс", "confidence": 0.9, "parts": []}
    spans, reason = propose_split(item, segment_id="s1", text=KSAURR_TEXT, character="Бдеукс")
    assert spans is None
    assert reason is not None


# --- IMPORTANT 1 / круг 5: токен от segment_id, не порядковый номер ---------------


def test_render_batch_prompt_and_tokens_for_use_the_same_tokens():
    # render_batch_prompt и _tokens_for обязаны брать токен из одного места — иначе
    # prompt может попросить одно, а сверка ждать другое.
    batch = [Candidate(f"seg-{c}", i, f"текст {i}", "А") for i, c in enumerate("abcde")]
    prompt = render_batch_prompt(batch)
    expected = _tokens_for(batch)
    found = _tokens_in_prompt(prompt)
    assert len(found) == len(batch)
    assert set(found) == set(expected)


def test_parse_items_rejects_sequential_numbering_against_hash_tokens():
    # Круг 5 (критично): токены больше не последовательны, поэтому «модель
    # перенумеровала по-своему» больше не может случайно совпасть с чужим выданным
    # токеном — раньше (последовательные «#00000»…) индексы 1–4 совпали бы с
    # соседями, теперь ни один не совпадает ни с чем.
    batch = [Candidate(f"seg-{c}", i, f"текст {i}", "А") for i, c in enumerate("abcde")]
    expected = _tokens_for(batch)
    stats: dict = {}
    result = _parse_items(
        json.dumps({"items": [
            {"id": f"#{i:05d}", "speaker": "А", "confidence": 0.9, "parts": []} for i in range(5)
        ]}),
        expected, stats,
    )
    assert result == {}
    assert stats["foreign_ids"] == 5


def test_ask_batch_rejects_sequential_numbering_entirely_for_batch_of_five():
    # То же самое сквозь весь ask_batch, с повтором: ничего не применяется к
    # соседям, весь батч остаётся неотвеченным.
    batch = [Candidate(f"seg-{c}", i, f"текст {i}", "А") for i, c in enumerate("abcde")]

    def fake_llm(system_prompt, user_prompt, schema):
        return {"content": json.dumps({"items": [
            {"id": f"#{i:05d}", "speaker": "А", "confidence": 0.9, "parts": []} for i in range(5)
        ]})}

    stats: dict = {}
    answers = ask_batch(fake_llm, "sys", batch, retry_pause=0, stats=stats)
    assert answers == {}
    assert stats["foreign_ids"] == 10  # по 5 на исходный вызов и на повтор


def test_ask_batch_accepts_hex_tokens_without_leading_hash():
    # Форма без решётки (круг 4) остаётся нормальной и с новыми токенами.
    batch = [Candidate(f"seg-{c}", i, f"текст {i}", "А") for i, c in enumerate("abcde")]
    tokens = list(_tokens_for(batch))

    def fake_llm(system_prompt, user_prompt, schema):
        return {"content": json.dumps({"items": [
            {"id": token, "speaker": "А", "confidence": 0.9, "parts": []} for token in tokens
        ]})}

    answers = ask_batch(fake_llm, "sys", batch, retry_pause=0)
    assert sorted(answers) == [0, 1, 2, 3, 4]


def test_ask_batch_discards_unmatched_id_and_never_misapplies_for_single_candidate():
    # Одна порция из одного абзаца: id, не выданный нами, отбрасывается, а не
    # подставляется как ответ — с одним повтором, не больше.
    batch = [Candidate("s1", 1, "текст первого абзаца", "А")]
    calls = []

    def fake_llm(system_prompt, user_prompt, schema):
        calls.append(user_prompt)
        return {"content": json.dumps({"items": [
            {"id": "#этот-токен-мы-не-выдавали", "speaker": "А", "confidence": 0.9, "parts": []},
        ]})}

    stats: dict = {}
    answers = ask_batch(fake_llm, "sys", batch, retry_pause=0, stats=stats)
    assert answers == {}
    assert len(calls) == 2  # исходный запрос + один повтор
    assert stats["foreign_ids"] == 2


# --- 6. абзац без ответа модели остаётся с прежней атрибуцией ----------------------


def test_unanswered_paragraph_is_left_untouched_after_one_retry():
    candidates = [Candidate("s1", 1, "текст первого абзаца", "А"), Candidate("s2", 2, "текст второго абзаца", "Б")]
    calls = []

    def fake_llm(system_prompt, user_prompt, schema):
        calls.append(user_prompt)
        if len(calls) == 1:
            # Первый ответ — только за первый токен порции (s1); второй (s2) промолчал.
            token = _tokens_in_prompt(user_prompt)[0]
            return {"content": json.dumps({"items": [
                {"id": f"#{token}", "speaker": "А", "confidence": 0.9, "parts": []},
            ]})}
        # Повтор — по одному-единственному пропущенному, и он снова промолчал.
        return {"content": json.dumps({"items": []})}

    approved, report_rows, stats = process_candidates(candidates, llm=fake_llm, retry_pause=0)

    assert len(calls) == 2  # один повтор, не больше
    assert "s2" not in approved
    outcomes = {row["candidate"].segment_id: row["outcome"] for row in report_rows}
    assert outcomes["s2"] == "no_answer"


# --- IMPORTANT 4: смешанные source/confidence внутри одной версии ------------------


def test_process_candidates_preserves_original_source_and_confidence_for_character_speech():
    candidate = Candidate("s1", 127, KSAURR_TEXT, "Бдеукс", source="operator", confidence=0.55)

    def fake_llm(system_prompt, user_prompt, schema):
        token = _tokens_in_prompt(user_prompt)[0]
        return {"content": json.dumps({"items": [{
            "id": f"#{token}", "speaker": "Бдеукс", "confidence": 0.9,
            "parts": [{"speaker": "Рассказчик", "quote": KSAURR_QUOTE}],
        }]})}

    approved, report_rows, stats = process_candidates([candidate], llm=fake_llm, retry_pause=0)

    spans = approved["s1"]
    narrator_spans = [sp for sp in spans if sp["speaker"] == NARRATOR]
    character_spans = [sp for sp in spans if sp["speaker"] == "Бдеукс"]
    assert len(narrator_spans) == 1
    assert narrator_spans[0]["source"] == "llm_remarks"
    assert narrator_spans[0]["confidence"] == 0.9
    assert character_spans  # персонажу что-то осталось
    for sp in character_spans:
        # Реплика персонажа — те же слова, что и раньше: source/confidence исходной
        # атрибуции, а не модели ремарок.
        assert sp["source"] == "operator"
        assert sp["confidence"] == 0.55


# --- мелочи: счётчик ошибок модели, прогресс по батчам, пауза перед повтором ------


def test_process_candidates_counts_llm_errors_separately_from_no_answer():
    candidates = [Candidate("s1", 1, "текст один", "А")]

    def raising_llm(system_prompt, user_prompt, schema):
        raise RuntimeError("503 Service Unavailable")

    approved, report_rows, stats = process_candidates(candidates, llm=raising_llm, retry_pause=0)
    assert stats["llm_errors"] == 2  # исходный вызов и повтор оба упали
    assert report_rows[0]["outcome"] == "no_answer"


def test_process_candidates_reports_progress_per_batch():
    candidates = [Candidate(f"s{i}", i, "текст", "А") for i in range(7)]  # 2 батча по 5

    def fake_llm(system_prompt, user_prompt, schema):
        return {"content": json.dumps({"items": []})}

    seen = []
    process_candidates(
        candidates, llm=fake_llm, retry_pause=0,
        on_batch=lambda done, total: seen.append((done, total)),
    )
    assert seen == [(1, 2), (2, 2)]


def test_ask_batch_pauses_before_retry(monkeypatch):
    calls = []

    def fake_llm(system_prompt, user_prompt, schema):
        calls.append(1)
        if len(calls) == 1:
            return {"content": json.dumps({"items": []})}
        token = _tokens_in_prompt(user_prompt)[0]
        return {"content": json.dumps({"items": [
            {"id": f"#{token}", "speaker": "А", "confidence": 0.5, "parts": []},
        ]})}

    sleeps = []
    monkeypatch.setattr("scripts.split_author_remarks.time.sleep", lambda s: sleeps.append(s))
    ask_batch(fake_llm, "sys", [Candidate("s1", 1, "текст", "А")], retry_pause=1.5)
    assert sleeps == [1.5]


# --- IMPORTANT 5: запись сразу после порции, не после конца всего прохода ---------


def test_process_candidates_calls_after_batch_once_per_batch_not_at_the_end():
    candidates = [Candidate("s1", 1, KSAURR_TEXT, "Бдеукс"), Candidate("s2", 2, KSAURR_TEXT, "Бдеукс")]

    def fake_llm(system_prompt, user_prompt, schema):
        # batch_size=1 — у s1 и s2 РАЗНЫЕ токены (они от их segment_id), поэтому
        # нельзя захардкодить один ответ на оба: читаем токен из промпта.
        token = _tokens_in_prompt(user_prompt)[0]
        return {"content": json.dumps({"items": [{
            "id": f"#{token}", "speaker": "Бдеукс", "confidence": 0.9,
            "parts": [{"speaker": "Рассказчик", "quote": KSAURR_QUOTE}],
        }]})}

    seen = []
    seen_report_lengths = []
    process_candidates(
        candidates, llm=fake_llm, batch_size=1, retry_pause=0,
        after_batch=lambda batch_approved, report_rows_so_far: (
            seen.append(set(batch_approved)), seen_report_lengths.append(len(report_rows_so_far)),
        ),
    )
    assert seen == [{"s1"}, {"s2"}]
    # Снимок report_rows растёт с каждой порцией — отчёт можно перезаписывать на диск
    # прямо тут, не дожидаясь конца всего прохода.
    assert seen_report_lengths == [1, 2]


def test_process_candidates_calls_after_batch_even_when_nothing_approved_in_it():
    # Мелочь круга 2: отчёт должен расти по ходу прохода, даже если конкретная
    # порция ничего не одобрила — значит after_batch зовут ПОСЛЕ КАЖДОЙ порции.
    candidates = [Candidate("s1", 1, "текст один", "А")]

    def fake_llm(system_prompt, user_prompt, schema):
        return {"content": json.dumps({"items": []})}

    calls = []
    process_candidates(
        candidates, llm=fake_llm, retry_pause=0,
        after_batch=lambda batch_approved, report_rows_so_far: calls.append((batch_approved, report_rows_so_far)),
    )
    assert len(calls) == 1
    batch_approved, report_rows_so_far = calls[0]
    assert batch_approved == {}
    assert [row["outcome"] for row in report_rows_so_far] == ["no_answer"]


# --- запись: новая версия, source=llm_remarks по умолчанию, коммит порциями -------


def test_apply_plan_writes_next_version_in_chunks_using_per_span_source():
    SessionLocal = _session()
    with SessionLocal() as db:
        db.add(V2Attribution(id="a1", segment_id="s1", span_start=0, span_end=50, speaker="Бдеукс",
                              confidence=0.9, source="llm", version=1))
        db.commit()

        approved = {
            "s1": [
                {"start": 0, "end": 10, "speaker": NARRATOR, "confidence": 0.8, "source": "llm_remarks"},
                {"start": 10, "end": 50, "speaker": "Бдеукс", "confidence": 0.55, "source": "operator"},
            ],
        }
        stored = apply_plan(db, approved, chunk_size=1)
        assert stored == 2

        rows = db.query(V2Attribution).filter(V2Attribution.segment_id == "s1").all()
        latest = [r for r in rows if r.version == 2]
        assert len(latest) == 2
        by_speaker = {r.speaker: r for r in latest}
        assert by_speaker[NARRATOR].source == "llm_remarks"
        assert by_speaker["Бдеукс"].source == "operator"
        assert by_speaker["Бдеукс"].confidence == 0.55


def test_apply_plan_falls_back_to_default_source_when_span_has_none():
    SessionLocal = _session()
    with SessionLocal() as db:
        approved = {"s1": [{"start": 0, "end": 10, "speaker": "Бдеукс", "confidence": 0.5}]}
        apply_plan(db, approved, chunk_size=1)
        row = db.query(V2Attribution).filter(V2Attribution.segment_id == "s1").one()
        assert row.source == "llm_remarks"


# --- отчёт --------------------------------------------------------------------------


def test_format_report_has_counts_reason_breakdown_and_split_pieces():
    split_candidate = Candidate("s1", 5, KSAURR_TEXT, "Бдеукс")
    rejected_candidate = Candidate("s2", 6, GARIADOLL_TEXT, "Петуунегр")
    report_rows = [
        {"candidate": split_candidate, "outcome": "split", "reason": None,
         "pieces": [("Бдеукс", "- Болота…"), (NARRATOR, "- сказал он."), ("Бдеукс", "— и всё.")]},
        {"candidate": rejected_candidate, "outcome": "rejected",
         "reason": "Рассказчику отошло 90% абзаца — похоже, речь персонажа принята за ремарку", "pieces": None},
    ]
    text = format_report(
        book_id="book-1", chapter_id=None, candidates=[split_candidate, rejected_candidate],
        report_rows=report_rows, applied=False, stored_rows=0,
    )
    assert "кандидатов 2" in text
    assert "разрезано 1" in text
    assert ">85% Рассказчику" in text
    assert "Абзац 5 — Бдеукс" in text
    assert "**АВТОР**" in text


def test_format_report_shows_rejected_text_and_narrator_share():
    split_candidate = Candidate("s1", 5, KSAURR_TEXT, "Бдеукс")
    rejected_candidate = Candidate("s2", 6, "Короткий абзац персонажа целиком.", "Петуунегр")
    report_rows = [
        {"candidate": split_candidate, "outcome": "split", "reason": None,
         "pieces": [("Бдеукс", "- Болота…"), (NARRATOR, "слова автора."), ("Бдеукс", "– и всё.")]},
        {"candidate": rejected_candidate, "outcome": "rejected",
         "reason": "Рассказчику отошло 90% абзаца — похоже, речь персонажа принята за ремарку", "pieces": None},
    ]
    text = format_report(book_id="b", chapter_id=None, candidates=[split_candidate, rejected_candidate],
                          report_rows=report_rows, applied=False, stored_rows=0)
    assert "Короткий абзац персонажа целиком." in text  # текст отклонённого — в отчёте
    assert "Абзац 6" in text
    assert "Рассказчику" in text and "%)" in text  # доля Рассказчика у принятого разреза


def test_format_report_flags_splits_without_speech_verb_or_character_name():
    # Тире после запятой запретом не закрыть — владелец должен УВИДЕТЬ эти разрезы.
    with_verb = Candidate("s1", 1, "текст", "Бдеукс")
    without_verb = Candidate("s2", 2, "текст", "Петуунегр")
    report_rows = [
        {"candidate": with_verb, "outcome": "split", "reason": None,
         "pieces": [("Бдеукс", "- Реплика,"), (NARRATOR, "сказал Бдеукс,"), ("Бдеукс", "- ещё.")]},
        {"candidate": without_verb, "outcome": "split", "reason": None,
         "pieces": [("Петуунегр", "- Реплика,"), (NARRATOR, "тут было тихо,"), ("Петуунегр", "- ещё.")]},
    ]
    text = format_report(book_id="b", chapter_id=None, candidates=[with_verb, without_verb],
                          report_rows=report_rows, applied=False, stored_rows=0)
    assert "без глагола речи и имени персонажа" in text
    assert "1 из 2" in text
    assert "⚠ без глагола/имени" in text


def test_format_report_shows_foreign_id_and_llm_error_counts_in_header():
    # Круг 4: без этой строки следующий разъезд формата ответа модели снова искать
    # зондом по сырым логам, а не глазами по отчёту.
    text = format_report(book_id="b", chapter_id=None, candidates=[], report_rows=[],
                          applied=False, stored_rows=0, stats={"foreign_ids": 41, "llm_errors": 3})
    assert "Идентификаторов отброшено как чужие: 41" in text
    assert "ошибок вызова модели: 3" in text


def test_format_report_omits_stats_line_when_stats_not_given():
    # Обратная совместимость: старые вызовы (без реального прохода) не должны
    # печатать «0 · 0» на пустом месте.
    text = format_report(book_id="b", chapter_id=None, candidates=[], report_rows=[],
                          applied=False, stored_rows=0)
    assert "Идентификаторов отброшено" not in text


class TestTheApplySwitchDemandsAnExplicitDatabase:
    """`--db` по умолчанию — боевая база. Вхолостую она открывается `mode=ro`, а с
    `--apply` — на запись: следующая книга, запущенная «по памяти», без единой опечатки
    пишет разрезы в прод. Явное имя файла — единственное, что тут отделяет одно от другого.
    """

    def test_apply_without_db_refuses(self, monkeypatch, capsys):
        import pytest

        from scripts.split_author_remarks import main

        monkeypatch.setattr(
            "sys.argv",
            ["split_author_remarks.py", "--book", "b-1", "--out", "/tmp/r.md", "--apply"],
        )

        with pytest.raises(SystemExit) as exit_info:
            main()

        assert exit_info.value.code == 2
        assert "--db" in capsys.readouterr().err

    def test_a_dry_run_still_opens_the_production_base_read_only(self, monkeypatch):
        """Холостому прогону явность не нужна: боевая база открывается `mode=ro`.

        До подключения дело здесь не доходит — `create_engine` подменён: тест про
        разбор аргументов не имеет права трогать ни боевой файл, ни модель.
        """
        import pytest
        import sqlalchemy

        from scripts.split_author_remarks import PROD_DB, main

        seen: list[str] = []

        def _stop(url, *args, **kwargs):
            seen.append(str(url))
            raise RuntimeError("дальше подключения тесту нельзя")

        monkeypatch.setattr(sqlalchemy, "create_engine", _stop)
        monkeypatch.setattr("sys.argv", ["split_author_remarks.py", "--book", "b-1", "--out", "/tmp/r.md"])

        with pytest.raises(RuntimeError):
            main()

        assert seen and PROD_DB in seen[0] and "mode=ro" in seen[0]

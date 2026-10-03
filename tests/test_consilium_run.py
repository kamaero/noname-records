"""Оркестрация консилиума на подставной модели: ни сети, ни базы, ни денег."""
import pytest

from app.pipeline.consilium_run import (
    Budget, ExistingState, StopRun, arbitrate_place, derive_verdict, plan_places, read_chapter,
)


def _echo_reader(calls):
    def ask(model, system, user, schema):
        calls.append(user)
        numbers = [line.split()[0] for line in user.split("АБЗАЦЫ:\n", 1)[1].splitlines()]
        return {"lines": [{"id": n, "speaker": "Гамук", "confidence": 0.9} for n in numbers]}
    return ask


def test_a_chapter_is_read_in_chunks_of_110_with_own_context():
    calls = []
    paragraphs = [(n, f"текст {n}") for n in range(230)]
    answers, complete = read_chapter(model="m", cast_lines=["- Гамук"], paragraphs=paragraphs,
                                     ask=_echo_reader(calls))
    assert complete and len(answers) == 230 and answers[229] == "Гамук"
    assert len(calls) == 3
    assert "КОНТЕКСТ" not in calls[0] and "#00109 → Гамук" in calls[1]


def test_answers_for_paragraphs_not_asked_are_ignored():
    def ask(model, system, user, schema):
        return {"lines": [{"id": "#00000", "speaker": "Гамук"}, "#99999 Тупуг"]}
    answers, complete = read_chapter(model="m", cast_lines=[], paragraphs=[(0, "a"), (1, "b")], ask=ask)
    assert answers == {0: "Гамук"} and complete is False  # абзац 1 без ответа


def test_a_chunk_refused_twice_leaves_the_chapter_incomplete():
    tries = []
    def ask(model, system, user, schema):
        tries.append(1)
        raise RuntimeError("503")
    answers, complete = read_chapter(model="m", cast_lines=[], paragraphs=[(0, "a")], ask=ask, pause=0)
    assert answers == {} and complete is False and len(tries) == 2


def test_checkpoint_can_stop_between_chunks():
    calls = []
    def stop():
        if calls:
            raise StopRun("stopped_by_user")
    with pytest.raises(StopRun):
        read_chapter(model="m", cast_lines=[], paragraphs=[(n, "t") for n in range(200)],
                     ask=_echo_reader(calls), checkpoint=stop)
    assert len(calls) == 1


def test_checkpoint_is_not_called_after_the_last_chunk():
    checkpoint_calls = []
    def checkpoint():
        checkpoint_calls.append(1)
        if len(checkpoint_calls) >= 2:
            raise StopRun("stopped_by_user")
    answers, complete = read_chapter(model="m", cast_lines=[], paragraphs=[(n, "t") for n in range(200)],
                                     ask=_echo_reader([]), checkpoint=checkpoint)
    assert complete and len(answers) == 200 and len(checkpoint_calls) == 1


CANON = {"Гамук": "Гамук", "гамук": "Гамук", "Тупуг": "Тупуг", "Рассказчик": "Рассказчик"}.get
TEXTS = {61: "Вошёл Тупуг.", 62: "— Идём, — сказал Гамук.", 63: "— Куда?"}


@pytest.mark.parametrize("over,verdict", [
    ({}, "change"),                                             # доказано, не как сейчас
    ({"speaker": "гамук"}, "keep_current"),                     # доказано, как сейчас (регистр)
    ({"evidence_kind": "context_only"}, "undecidable"),
    ({"evidence_quote": "сказал Гамук громко"}, "undecidable"), # нет дословно
    ({"evidence_para": 66}, "undecidable"),                     # дальше двух абзацев
    ({"speaker": "Никто"}, "undecidable"),                      # не из каста
])
def test_the_machine_derives_the_verdict(over, verdict):
    args = dict(ordinal=63, current="Тупуг", speaker="Гамук", evidence_kind="cue_named",
                evidence_para=62, evidence_quote="сказал  Гамук", texts=TEXTS,
                canon=lambda s: CANON(str(s).strip()) or "")
    args.update(over)
    if "speaker" in over and over["speaker"] == "гамук":
        args["current"] = "Гамук"
    out = derive_verdict(**args)
    assert out["verdict"] == verdict
    assert out["proven"] is (verdict != "undecidable")


def test_the_narrator_is_never_proven_by_a_quote():
    out = derive_verdict(ordinal=63, current="Гамук", speaker="Рассказчик", evidence_kind="cue_named",
                         evidence_para=62, evidence_quote="сказал Гамук", texts=TEXTS,
                         canon=lambda s: CANON(s) or "")
    assert out["verdict"] == "undecidable" and out["proven"] is False
    assert "рассказчик не доказывается цитатой" in out["note"]


def test_the_narrator_can_be_kept_as_current_when_proven():
    out = derive_verdict(ordinal=63, current="Рассказчик", speaker="Рассказчик", evidence_kind="cue_named",
                         evidence_para=62, evidence_quote="сказал Гамук", texts=TEXTS,
                         canon=lambda s: CANON(s) or "")
    assert out["verdict"] == "keep_current" and out["proven"] is True


def test_arbiter_versions_are_shuffled_the_same_way_every_time_and_unlabeled():
    seen = []
    def ask(model, system, user, schema):
        seen.append(user)
        return {"speaker": "Гамук", "evidence_kind": "cue_named", "evidence_para": 62,
                "evidence_quote": "сказал Гамук", "reason": "ремарка"}
    kwargs = dict(chapter_index=33, ordinal=63, current="Тупуг", opus="Гамук", sol="UNSURE",
                  texts=TEXTS, speakers={61: "Рассказчик", 62: "Гамук", 63: "Тупуг"},
                  races={"Гамук": "демон"}, ask=ask, canon=lambda s: CANON(s) or "", model="m")
    first, second = arbitrate_place(**kwargs), arbitrate_place(**kwargs)
    assert first["verdict"] == "change" and first["speaker"] == "Гамук" and first["reason"] == "ремарка"
    assert seen[0] == seen[1] and "UNSURE" not in seen[0]
    assert "- Гамук — демон" in seen[0] and "- Тупуг" in seen[0]


def test_an_arbiter_that_fails_leaves_the_place_without_a_verdict():
    def ask(model, system, user, schema):
        raise RuntimeError("timeout")
    out = arbitrate_place(chapter_index=33, ordinal=63, current="Тупуг", opus="Гамук", sol="Гамук",
                          texts=TEXTS, speakers={}, races={}, ask=ask,
                          canon=lambda s: CANON(s) or "", model="m")
    assert out["verdict"] == "" and out["proven"] is False


def test_an_arbiter_returning_a_non_dict_payload_leaves_the_place_without_a_verdict():
    def ask(model, system, user, schema):
        return ["x"]
    out = arbitrate_place(chapter_index=33, ordinal=63, current="Тупуг", opus="Гамук", sol="Гамук",
                          texts=TEXTS, speakers={}, races={}, ask=ask,
                          canon=lambda s: CANON(s) or "", model="m")
    assert out["verdict"] == "" and out["proven"] is False


def test_arbitrate_place_preserves_paragraph_zero_as_evidence():
    texts0 = {0: "— Идём, — сказал Гамук.", 1: "— Куда?"}
    def ask(model, system, user, schema):
        return {"speaker": "Гамук", "evidence_kind": "cue_named", "evidence_para": 0,
                "evidence_quote": "сказал Гамук", "reason": "ремарка"}
    out = arbitrate_place(chapter_index=1, ordinal=1, current="Тупуг", opus="Гамук", sol="Гамук",
                          texts=texts0, speakers={}, races={}, ask=ask,
                          canon=lambda s: CANON(s) or "", model="m")
    assert out["verdict"] == "change" and out["evidence_para"] == 0


def test_plan_places_follows_the_spec_table():
    k = lambda n: (1, n, 0, 5)
    found = {k(1): ("wrong_voice", "A", "B", "B"), k(2): ("wrong_voice", "A", "B", "B"),
             k(3): ("wrong_voice", "A", "C", "C"), k(4): ("wrong_voice", "A", "B", "B"),
             k(5): ("wrong_voice", "A", "B", "B"), k(6): ("wrong_voice", "A", "B", "B")}
    existing = {
        k(2): ExistingState("new", "wrong_voice", "A", "B", "B", True),       # тот же спор — ничего
        k(3): ExistingState("new", "wrong_voice", "A", "B", "B", True),       # спор изменился — арбитр
        k(4): ExistingState("gone", "wrong_voice", "A", "B", "B", True),      # вернулось — арбитр
        k(5): ExistingState("accepted", "wrong_voice", "A", "B", "B", True),  # решено — ничего
        k(6): ExistingState("new", "wrong_voice", "A", "B", "B", False),      # без вердикта — арбитр
        k(7): ExistingState("new", "wrong_voice", "A", "B", "B", True),       # исчезло — gone
        k(8): ExistingState("dismissed", "wrong_voice", "A", "B", "B", True), # исчезло, решено — ничего
    }
    arbitrate, gone = plan_places(found, existing)
    assert sorted(arbitrate) == [k(1), k(3), k(4), k(6)]
    assert gone == [k(7)]


def test_budget_stops_at_twice_the_estimate_and_counts_calls_without_a_balance():
    budget = Budget(estimate_rub=100.0, estimate_calls=3)
    budget.check(199.0)
    with pytest.raises(StopRun) as caught:
        budget.check(201.0)
    assert caught.value.reason == "over_budget"
    blind = Budget(estimate_rub=100.0, estimate_calls=3)
    for _ in range(6):
        blind.note_call()
    blind.check(None)
    blind.note_call()
    with pytest.raises(StopRun):
        blind.check(None)


def test_a_small_estimate_gets_a_hundred_roubles_of_headroom():
    # «потрачено» — общий баланс RouterAI; ASR тратит из него одновременно
    small = Budget(estimate_rub=10.0, estimate_calls=3)
    small.check(110.0)
    with pytest.raises(StopRun) as caught:
        small.check(111.0)
    assert caught.value.reason == "over_budget"

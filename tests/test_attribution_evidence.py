"""Доказательства приписки: на чём держится «здесь говорит вот этот»."""
from app.pipeline.attribution_evidence import (
    Evidence, find_name_in_narration, classify_span, classify_chapter,
)

NAMES = {"дгарнин": "Дгарнин", "гамук": "Гамук", "пупип": "Пупип", "едвбабак": "Едвбабак"}


def test_the_name_in_the_authors_words_is_found():
    """«— …, — сказал Дгарнин» — авторские слова называют говорящего прямо."""
    assert find_name_in_narration(["сказал Дгарнин."], NAMES) == "Дгарнин"


def test_a_name_in_a_different_case_still_counts():
    """Русский язык склоняет имена; «ответил Дгарнину» — то же имя."""
    assert find_name_in_narration(["ответил Дгарнину."], NAMES) == "Дгарнин"


def test_authors_words_without_a_name_prove_nothing():
    """«— сказал он» — самый частый случай и самый бесполезный."""
    assert find_name_in_narration(["сказал он."], NAMES) == ""


def test_an_empty_list_proves_nothing():
    assert find_name_in_narration([], NAMES) == ""


def test_a_cue_naming_two_characters_names_nobody():
    """«— сказал Гамук Дгарнину» — названы двое, и кто из них говорит, неизвестно."""
    assert find_name_in_narration(["сказал Гамук Дгарнину."], NAMES) == ""


TWINS = {"гамук": "Гамук", "гамукар": "Гамукар"}


def test_two_names_with_a_common_beginning_are_not_confused():
    """Сравнение по основе на паре «Гамук» и «Гамукар» обязано молчать, а не гадать."""
    assert find_name_in_narration(["ответил Гамукар."], TWINS) == "Гамукар"
    assert find_name_in_narration(["ответил Гамука."], TWINS) == ""


def test_a_cue_naming_the_speaker_proves_the_line():
    """Реплика и ремарка — разные отрезки одного абзаца, как в боевой разметке."""
    result = classify_span(
        speaker="Дгарнин",
        span_text="- Нас стало слишком много, ",
        narration=["- задумчиво сказал Дгарнин."],
        paragraph=2,
        names=NAMES,
    )
    assert result.kind == "cue_named"
    assert result.speaker == "Дгарнин"
    assert "сказал Дгарнин" in result.quote
    assert result.paragraph == 2


def test_an_undivided_paragraph_still_works():
    """У старых, ещё не разделённых абзацев ремарка лежит внутри самого отрезка."""
    result = classify_span(
        speaker="Дгарнин",
        span_text="- Нас стало слишком много, - задумчиво сказал Дгарнин.",
        narration=[],
        paragraph=2,
        names=NAMES,
    )
    assert result.kind == "cue_named"


def test_a_cue_naming_someone_else_does_not_prove_this_speaker():
    """Ремарка называет Пупипа, а отрезок приписан Гамуку — это не доказательство."""
    result = classify_span(
        speaker="Гамук",
        span_text="- Как делишки? ",
        narration=["- сказал Пупип."],
        paragraph=7,
        names=NAMES,
    )
    assert result.kind == "unsupported"


def test_an_unnamed_speaker_is_its_own_class():
    """«незнакомец» — разметка опознала того, кого текст не назвал."""
    result = classify_span(
        speaker="Едвбабак",
        span_text="- Из числа особо одаренных, ",
        narration=["- добавил незнакомец."],
        paragraph=41,
        names=NAMES,
    )
    assert result.kind == "unnamed_speaker"


def test_a_bare_line_without_authors_words_is_unsupported():
    result = classify_span(
        speaker="Дгарнин", span_text="- Вокруг одни проблемы.", narration=[],
        paragraph=3, names=NAMES,
    )
    assert result.kind == "unsupported"


def test_a_name_in_the_object_position_does_not_prove_the_speaker():
    """«— ответил Дгарнину Гамук»: Дгарнин здесь слушает, а не говорит."""
    result = classify_span(
        speaker="Дгарнин",
        span_text="- Нас стало слишком много, ",
        narration=["- ответил Дгарнину Гамук."],
        paragraph=2,
        names=NAMES,
    )
    assert result.kind == "unsupported"


def test_prose_beside_a_line_without_a_speech_verb_proves_nothing():
    """Имя рядом с репликой — ещё не доказательство: спека требует глагол речи.

    Отрезок рассказчика без тире — не ремарка, а проза в том же абзаце, и поблажка
    на «любой глагол» на него не распространяется.
    """
    result = classify_span(
        speaker="Дгарнин",
        span_text="- Нас стало слишком много, ",
        narration=["Дгарнин поморщился и отвернулся."],
        paragraph=2,
        names=NAMES,
    )
    assert result.kind == "unsupported"


def test_a_remark_of_the_same_paragraph_may_carry_any_verb():
    """«— покачал головой Дгарнин» — ремарка своего абзаца, и она называет говорящего.

    Русская проза вводит реплику чем угодно: кивнул, парировал, вздохнул. Замер по
    книге: строгий список глаголов речи отверг бы около половины настоящих ремарок.
    Принадлежность такой ремарки известна из вёрстки абзаца — она отделена тире от
    речи и стоит вплотную к ней, а имя в ней в именительном падеже.
    """
    result = classify_span(
        speaker="Дгарнин",
        span_text="- Нас стало слишком много, ",
        narration=["- покачал головой Дгарнин."],
        paragraph=2,
        names=NAMES,
    )
    assert result.kind == "cue_named"
    assert result.quote == "- покачал головой Дгарнин."


def test_a_proven_span_always_carries_a_quote():
    """Вердикт без проверяемой цитаты ничего не закрывает — значит, и не вердикт."""
    result = classify_span(
        speaker="Дгарнин",
        span_text="- Нас стало слишком много, ",
        narration=["- задумчиво сказал Дгарнин."],
        paragraph=2,
        names=NAMES,
    )
    assert result.kind == "cue_named"
    assert result.quote


def _para(number, text, spans):
    return {"paragraph": number, "text": text, "spans": spans}


def _span(speaker, start, end):
    return {"speaker": speaker, "start": start, "end": end}


def test_a_cue_in_the_next_paragraph_proves_the_line():
    """Реплика голая, но следующий абзац — авторские слова с именем."""
    chapter = [
        _para(1, "- Вокруг одни проблемы.", [_span("Дгарнин", 0, 23)]),
        _para(2, "Так сказал Дгарнин, глядя в стену.", [_span("Рассказчик", 0, 34)]),
    ]
    out = classify_chapter(chapter, names=NAMES)
    assert out[(1, 0, 23)].kind == "cue_adjacent"


def test_being_addressed_by_name_proves_the_answer():
    """Собеседник назвал его по имени в ответной реплике."""
    chapter = [
        _para(1, "- Я не пойду туда.", [_span("Дгарнин", 0, 18)]),
        _para(2, "- Дгарнин, ты трус, - сказал Гамук.",
              [_span("Гамук", 0, 20), _span("Рассказчик", 20, 35)]),
    ]
    out = classify_chapter(chapter, names=NAMES)
    assert out[(1, 0, 18)].kind == "address_by_name"


def test_alternation_proves_the_middle_of_a_two_person_dialogue():
    """Оба конца цепочки закреплены именами, участников ровно двое — середина доказана."""
    chapter = [
        _para(1, "- Начали, - сказал Дгарнин.",
              [_span("Дгарнин", 0, 11), _span("Рассказчик", 11, 27)]),
        _para(2, "- Ага.", [_span("Гамук", 0, 6)]),
        _para(3, "- И что дальше?", [_span("Дгарнин", 0, 15)]),
        _para(4, "- Ничего, - ответил Гамук.",
              [_span("Гамук", 0, 11), _span("Рассказчик", 11, 26)]),
    ]
    out = classify_chapter(chapter, names=NAMES)
    assert out[(2, 0, 6)].kind == "alternation"
    assert out[(3, 0, 15)].kind == "alternation"


def test_three_participants_break_alternation():
    """Третий голос в цепочке — и чередование больше ничего не доказывает."""
    chapter = [
        _para(1, "- Начали, - сказал Дгарнин.",
              [_span("Дгарнин", 0, 11), _span("Рассказчик", 11, 27)]),
        _para(2, "- Ага.", [_span("Гамук", 0, 6)]),
        _para(3, "- А я против.", [_span("Пупип", 0, 13)]),
        _para(4, "- Ничего, - ответил Гамук.",
              [_span("Гамук", 0, 11), _span("Рассказчик", 11, 26)]),
    ]
    out = classify_chapter(chapter, names=NAMES)
    assert out[(2, 0, 6)].kind == "unsupported"


def test_a_neighbour_with_its_own_line_does_not_prove_ours():
    """Ремарка соседа доказывает СОБСТВЕННУЮ реплику соседа, а не нашу.

    Живой случай главы 41: цитатой «– обрадовалась Погтда» был доказан отрезок,
    который оператор отдал Пупипу. Между репликой и ремаркой стоит чужая реплика —
    спека такое запрещает прямо.
    """
    chapter = [
        _para(1, "- Так это его слово, скажи!", [_span("Пупип", 0, 27)]),
        _para(2, "- Славно! - сказал Пупип.",
              [_span("Пупип", 0, 10), _span("Рассказчик", 10, 25)]),
    ]
    out = classify_chapter(chapter, names=NAMES)
    assert out[(1, 0, 27)].kind == "unsupported"


def test_the_cue_quote_is_a_literal_piece_of_its_paragraph():
    """Проверка слоя 3: цитата ищется в тексте названного абзаца ДОСЛОВНО.

    В абзаце два отрезка рассказчика, и склейка их через пробел даёт строку,
    которой в абзаце нет. Слой 1 не пережил бы собственной проверки.
    """
    text = "Он вздохнул. - Хватит, - сказал Дгарнин."
    chapter = [_para(1, text, [_span("Рассказчик", 0, 13), _span("Дгарнин", 13, 23),
                               _span("Рассказчик", 23, 40)])]
    out = classify_chapter(chapter, names=NAMES)
    found = out[(1, 13, 23)]
    assert found.kind == "cue_named"
    assert found.quote == "- сказал Дгарнин."
    assert found.quote in text


def test_the_address_quote_is_a_literal_piece_of_its_paragraph():
    """Речь отрезка склеена из кусков через ремарку — цитатой годится только кусок.

    Абзац не пережил разделения речи и ремарки: всё лежит одним отрезком, и разбор
    склеивает речь через пробел, выбрасывая ремарку из середины.
    """
    text = "- Идем, - она поклонилась. - Благодарю тебя, Дгарнин."
    chapter = [
        _para(1, "- Пора.", [_span("Дгарнин", 0, 7)]),
        _para(2, text, [_span("Гамук", 0, 53)]),
    ]
    out = classify_chapter(chapter, names=NAMES)
    found = out[(1, 0, 7)]
    assert found.kind == "address_by_name"
    assert found.paragraph == 2
    assert "Дгарнин" in found.quote
    assert found.quote in text


def test_a_neighbour_without_a_speech_verb_is_not_a_cue():
    """У соседнего абзаца поблажки нет: чья это ремарка, из вёрстки не видно."""
    chapter = [
        _para(1, "- Вокруг одни проблемы.", [_span("Дгарнин", 0, 23)]),
        _para(2, "Дгарнин поморщился и отвернулся.", [_span("Рассказчик", 0, 32)]),
    ]
    out = classify_chapter(chapter, names=NAMES)
    assert out[(1, 0, 23)].kind == "unsupported"


def test_a_whole_prose_paragraph_is_not_a_cue():
    """Соседний абзац прозы с упомянутым именем — не ремарка, а просто проза."""
    prose = ("А Пупип обливался холодным потом и думал о том, что будет дальше. "
             "Так он и стоял навытяжку, пока епископ вел церемонию, и ничего не "
             "сказал Пупип ни тогда, ни после, ни на следующее утро.")
    chapter = [
        _para(79, "И ничего не случилось.", [_span("Пупип", 0, 22)]),
        _para(80, prose, [_span("Рассказчик", 0, len(prose))]),
    ]
    out = classify_chapter(chapter, names=NAMES)
    assert out[(79, 0, 22)].kind == "unsupported"


def test_prose_attributed_to_a_character_is_not_proved_by_a_neighbour():
    """Наш отрезок сам должен быть произнесённой репликой, а не абзацем прозы.

    Живой случай главы 6: повествование целиком приписано Пупипу, а соседний
    короткий абзац «Ну Пупип и ответил» подпирал его — оператор же отдал этот абзац
    Рассказчику. Ремарка соседа доказывает чью-то речь; здесь речи нет.
    """
    chapter = [
        _para(165, "Они все его потребовали и обступили Пупипа.", [_span("Пупип", 0, 42)]),
        _para(166, "Ну Пупип и ответил. Даже билетик показал.", [_span("Рассказчик", 0, 40)]),
    ]
    out = classify_chapter(chapter, names=NAMES)
    assert out[(165, 0, 42)].kind == "unsupported"


def test_alternation_does_not_prove_a_label_outside_the_cast():
    """«UNSURE» — не персонаж: чередование не может доказать того, кого не назвали."""
    chapter = [
        _para(1, "- Начали, - сказал Дгарнин.",
              [_span("Дгарнин", 0, 11), _span("Рассказчик", 11, 27)]),
        _para(2, "- Что?..", [_span("UNSURE", 0, 8)]),
        _para(3, "- Ничего, - сказал Дгарнин.",
              [_span("Дгарнин", 0, 11), _span("Рассказчик", 11, 27)]),
    ]
    out = classify_chapter(chapter, names=NAMES)
    assert out[(2, 0, 8)].kind == "unsupported"


def test_an_address_inside_a_line_of_the_same_speaker_proves_nothing():
    """Сам себя по имени не окликают: это самоподтверждение, а не доказательство."""
    chapter = [
        _para(1, "- Я не пойду туда.", [_span("Дгарнин", 0, 18)]),
        _para(2, "- Дгарнин, ты трус!", [_span("Дгарнин", 0, 19)]),
    ]
    out = classify_chapter(chapter, names=NAMES)
    assert out[(1, 0, 18)].kind == "unsupported"


def test_a_name_in_the_third_person_is_not_an_address():
    """«А Дгарнин вчера был в порту» — про него, а не к нему."""
    chapter = [
        _para(1, "- Я не пойду туда.", [_span("Дгарнин", 0, 18)]),
        _para(2, "- А Дгарнин вчера был в порту.", [_span("Гамук", 0, 30)]),
    ]
    out = classify_chapter(chapter, names=NAMES)
    assert out[(1, 0, 18)].kind == "unsupported"


def test_prose_attributed_to_a_character_is_not_an_answering_line():
    """Отрезок без тире — не ответная реплика, даже если приписан персонажу."""
    chapter = [
        _para(1, "- Я не пойду туда.", [_span("Дгарнин", 0, 18)]),
        _para(2, "Дгарнин, как всегда, молчал.", [_span("Гамук", 0, 28)]),
    ]
    out = classify_chapter(chapter, names=NAMES)
    assert out[(1, 0, 18)].kind == "unsupported"


def test_alternation_needs_cue_named_at_both_ends():
    """Концы цепочки закреплены соседней ремаркой — этого спеке мало."""
    chapter = [
        _para(1, "Так говорил Дгарнин, стоя у окна.", [_span("Рассказчик", 0, 33)]),
        _para(2, "- Начали.", [_span("Дгарнин", 0, 9)]),
        _para(3, "- Ага.", [_span("Гамук", 0, 6)]),
        _para(4, "- И что дальше?", [_span("Дгарнин", 0, 15)]),
        _para(5, "- Ничего.", [_span("Гамук", 0, 9)]),
        _para(6, "Так ответил Гамук, глядя в стену.", [_span("Рассказчик", 0, 33)]),
    ]
    out = classify_chapter(chapter, names=NAMES)
    assert out[(2, 0, 9)].kind == "cue_adjacent"
    assert out[(5, 0, 9)].kind == "cue_adjacent"
    assert out[(3, 0, 6)].kind == "unsupported"
    assert out[(4, 0, 15)].kind == "unsupported"


def test_two_lines_in_a_row_by_one_character_break_alternation():
    """Участников двое, но ходы не чередуются — чередование ничего не доказывает."""
    chapter = _repeated_turn_chapter()
    out = classify_chapter(chapter, names=NAMES)
    assert out[(2, 0, 6)].kind == "unsupported"
    assert out[(3, 0, 15)].kind == "unsupported"


def _repeated_turn_chapter():
    """Цепочка, где Гамук говорит дважды подряд — через границу хода."""
    return [
        _para(1, "- Начали, - сказал Дгарнин.",
              [_span("Дгарнин", 0, 11), _span("Рассказчик", 11, 27)]),
        _para(2, "- Ага.", [_span("Гамук", 0, 6)]),
        _para(3, "- И правда ага.", [_span("Гамук", 0, 15)]),
        _para(4, "- И что дальше?", [_span("Дгарнин", 0, 15)]),
        _para(5, "- Ничего, - ответил Гамук.",
              [_span("Гамук", 0, 11), _span("Рассказчик", 11, 26)]),
    ]


def test_two_lines_in_a_row_by_one_character_are_reported():
    """Четвёртая перекрёстная проверка спеки: подряд идущие ходы одного лица."""
    from app.pipeline.attribution_evidence import consecutive_same_speaker

    found = consecutive_same_speaker(_repeated_turn_chapter())
    assert [(row["speaker"], row["paragraph"]) for row in found] == [("Гамук", 3)]

    clean = [
        _para(1, "- Начали.", [_span("Дгарнин", 0, 9)]),
        _para(2, "- Ага.", [_span("Гамук", 0, 6)]),
        _para(3, "- И что дальше?", [_span("Дгарнин", 0, 15)]),
    ]
    assert consecutive_same_speaker(clean) == []


def test_a_narrator_paragraph_ends_the_chain():
    """Абзац без реплик рвёт цепочку: после него чередование не продолжается."""
    chapter = [
        _para(1, "- Начали, - сказал Дгарнин.",
              [_span("Дгарнин", 0, 11), _span("Рассказчик", 11, 27)]),
        _para(2, "Прошёл час.", [_span("Рассказчик", 0, 11)]),
        _para(3, "- И что дальше?", [_span("Дгарнин", 0, 15)]),
    ]
    out = classify_chapter(chapter, names=NAMES)
    assert out[(3, 0, 15)].kind == "unsupported"

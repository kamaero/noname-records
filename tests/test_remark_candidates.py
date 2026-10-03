"""Кандидаты в авторские ремарки для ручной подсветки (см. `app/v2/remark_candidates.py`).

Абзацы взяты живыми из «Крыльев полумрака» — на них же замерена калибровка в
брифе задачи. Калибровка НАРОЧНО щедрая (не путать со строгим одноразовым
проходом в `scripts/split_author_remarks.py`): каждый кандидат подтверждает
человек глазами, поэтому цена ложного кандидата мала, а пропущенного — велика.
"""
from app.v2.remark_candidates import remark_candidates


def test_bdeuks_paragraph_finds_only_the_first_remark():
    # «Болота…» — тире после многоточия, кандидат подтверждается. Второй фрагмент
    # («– Это не похоже…») стоит после ТОЧКИ и продолжается с прописной — это
    # возобновлённая реплика Бдеукса, а не слова автора, поэтому отбрасывается.
    text = (
        "- Болота… - впервые за вечер отозвался Бдеукс. Ростом всего лишь с "
        "крупного пса, он все это время лениво грелся у огня, как старый кот. "
        "– Это не похоже на лёгкую прогулку."
    )
    spans = [{"start": 0, "end": len(text), "speaker": "Бдеукс"}]

    result = remark_candidates(text, spans)

    assert len(result) == 1
    start, end = result[0]
    assert text[start:end].strip() == (
        "- впервые за вечер отозвался Бдеукс. Ростом всего лишь с крупного "
        "пса, он все это время лениво грелся у огня, как старый кот."
    )


def test_petuunegr_paragraph_drops_the_resumed_reply():
    # Второй фрагмент («– Полибнугак, это уже похоже на манию.») — тоже после
    # точки с прописной, тоже возобновлённая реплика, тоже отбрасывается.
    text = "- Ну что ещё? – устало потёр пальцами виски Петуунегр. – Полибнугак, это уже похоже на манию."
    spans = [{"start": 0, "end": len(text), "speaker": "Петуунегр"}]

    result = remark_candidates(text, spans)

    assert len(result) == 1
    start, end = result[0]
    assert text[start:end].strip() == "– устало потёр пальцами виски Петуунегр."


def test_nistetozta_paragraph_keeps_both_generous_candidates():
    # После ЗАПЯТОЙ (не точки) регистр ничего не различает — щедрая калибровка
    # показывает оба фрагмента, решает человек.
    text = "- Не ходите туда, - предупредил Нистетозта, - вас там съедят!"
    spans = [{"start": 0, "end": len(text), "speaker": "Нистетозта"}]

    result = remark_candidates(text, spans)

    texts = [text[a:b].strip() for a, b in result]
    assert texts == ["- предупредил Нистетозта,", "- вас там съедят!"]


def test_narrator_only_paragraph_has_no_candidates():
    text = "Дгарнин сидел в изгибе ветвей."
    spans = [{"start": 0, "end": len(text), "speaker": "Рассказчик"}]

    assert remark_candidates(text, spans) == []


def test_paragraph_without_inner_dash_has_no_candidates():
    text = "- Всё просто отлично, правда."
    spans = [{"start": 0, "end": len(text), "speaker": "Бдеукс"}]

    assert remark_candidates(text, spans) == []


def test_already_split_paragraph_only_searches_the_character_span():
    # Абзац уже разрезан на два спана: персонаж + Рассказчик. В спане Рассказчика
    # кандидатов не ищем — он и так весь читается им.
    text = "- Нас стало слишком много, - сказал Дгарнин."
    split_at = text.index(" - сказал")
    spans = [
        {"start": 0, "end": split_at, "speaker": "Дгарнин"},
        {"start": split_at, "end": len(text), "speaker": "Рассказчик"},
    ]

    assert remark_candidates(text, spans) == []


# --- круг правок 1: тире посреди фразы («кузнецов – люди») ложно ловилось как ---
# --- граница слов автора, потому что пробел стоял в lookbehind наравне со ------
# --- знаками препинания. Живой абзац из главы 6, на котором это нашли. ----------

KARDASH_TEXT = "- Итак, мой друг, большинство кузнецов – люди женатые? – спросил Бапдиг, поднимая кружку."


def test_bapdig_paragraph_ignores_the_mid_sentence_dash():
    # «кузнецов – люди» — тире после БУКВЫ и пробела, обычная пунктуация внутри
    # фразы, а не граница слов автора: перед тире должен стоять именно знак
    # препинания, а не любой пробел. Единственный настоящий кандидат — после «?».
    spans = [{"start": 0, "end": len(KARDASH_TEXT), "speaker": "Бапдиг"}]

    result = remark_candidates(KARDASH_TEXT, spans)

    assert len(result) == 1
    start, end = result[0]
    assert KARDASH_TEXT[start:end].strip() == "– спросил Бапдиг, поднимая кружку."


def test_bapdig_paragraph_already_split_has_no_candidates():
    # Абзац уже разрезан ровно по границе настоящего кандидата: спан Бапдига
    # содержит «кузнецов – люди» — тот самый ложный, ныне отфильтрованный дефис —
    # и не должен породить кандидата; спан Рассказчика не ищется вовсе.
    split_at = KARDASH_TEXT.index("– спросил")
    spans = [
        {"start": 0, "end": split_at, "speaker": "Бапдиг"},
        {"start": split_at, "end": len(KARDASH_TEXT), "speaker": "Рассказчик"},
    ]

    assert remark_candidates(KARDASH_TEXT, spans) == []


def test_candidate_shorter_than_two_characters_is_dropped():
    # Голый дефис на стыке спанов (span персонажа кончается ровно на тире, без
    # текста после) — не текст, подсвечивать нечего.
    text = "- Нас стало слишком много, - сказал Дгарнин."
    bare_dash_end = text.index(" - сказал") + len(" -")  # спан кончается сразу после тире
    spans = [{"start": 0, "end": bare_dash_end, "speaker": "Дгарнин"}]

    assert remark_candidates(text, spans) == []


def test_letterless_candidate_is_dropped():
    # Круг правок 2: длина сама по себе ничего не доказывает — «- » (тире и пробел)
    # это те же два символа, что проходили старую проверку на минимальную длину, но
    # ни одной буквы в них нет. На всей книге таких кандидатов нашлось 15 — тире
    # стоит сразу перед концом спана (здесь — спан уже разрезан ровно так, что
    # второе внутреннее тире и «Нет.» после него ушли в другой спан). Минимальный
    # синтетический случай вместо конкретного сегмента книги — тот же механизм.
    text = "- Да. - - Нет."
    split_at = text.index(" - ") + len(" - ")  # спан кончается на «- Да. - », без единой буквы после тире
    assert text[:split_at] == "- Да. - "
    spans = [{"start": 0, "end": split_at, "speaker": "Кто-то"}]

    assert remark_candidates(text, spans) == []

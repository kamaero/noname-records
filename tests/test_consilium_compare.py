"""Сведение трёх ответов в находки: что считать спором и какого он рода."""
from app.pipeline.consilium_compare import Finding, compare

NARRATOR = "Рассказчик"


def _maps(rows):
    """rows: (глава, абзац, сейчас, opus, sol, имя_в_ремарке)."""
    current, opus, sol, named = {}, {}, {}, {}
    for chapter, ordinal, now, a, b, in_narration in rows:
        key = (chapter, ordinal)
        current[key], opus[key], sol[key], named[key] = now, a, b, in_narration
    return opus, sol, current, named


def test_full_agreement_is_not_a_finding():
    out = compare(*_maps([(1, 5, "Гамук", "Гамук", "Гамук", "")]))

    assert out == []


def test_both_readers_against_the_script_is_a_wrong_voice():
    out = compare(*_maps([(1, 5, "Дгарнин", "Тупуг", "Тупуг", "")]))

    assert [(f.kind, f.readers) for f in out] == [("wrong_voice", "Тупуг")]


def test_the_text_naming_someone_else_is_the_authors_play():
    """Ремарка прямо называет Гамука, а сценарий ставит Тупуга — это решение, не ошибка."""
    out = compare(*_maps([(56, 93, "Тупуг", "Гамук", "Гамук", "Гамук")]))

    assert [f.kind for f in out] == ["identity_play"]


def test_narrator_versus_character_is_its_own_kind():
    out = compare(*_maps([(6, 42, NARRATOR, "Пупип", "Пупип", "")]))

    assert [f.kind for f in out] == ["narrator_border"]


def test_readers_disagreeing_with_each_other_is_its_own_kind():
    out = compare(*_maps([(2, 7, "Дгарнин", "Гамук", "Пупип", "")]))

    assert [f.kind for f in out] == ["readers_split"]


def test_one_reader_unsure_and_the_other_against_the_script_is_dropped_but_counted():
    """«Не знаю» одного гасит спор второго — правило, а не недосмотр. Но оно видимо.

    Спека называет этот случай среди обязательных: один чтец сказал UNSURE, второй
    спорит со сценарием. Два голоса из трёх (сценарий и молчание) перевешивают один,
    и место не выносится человеку. Цена правила — выброшенный сигнал, поэтому счётчик
    обязателен: величину выброшенного владелец должен видеть числом, а не узнавать
    из кода.
    """
    stats: dict = {}
    out = compare(*_maps([(1, 5, "Дгарнин", "UNSURE", "Тупуг", "")]), stats=stats)

    assert out == []
    assert stats["dropped_unsure_from_one"] == 1
    assert stats["dropped_unsure_other_disputes_script"] == 1


def test_one_reader_agreeing_with_the_script_closes_the_place_but_is_counted():
    """Сценарий плюс один чтец — два голоса из трёх; расхождение второго не находка.

    Это самая массовая выемка прогона (281 место из 387), и она молчала.
    """
    stats: dict = {}
    out = compare(*_maps([(1, 5, "Дгарнин", "Дгарнин", "Тупуг", "")]), stats=stats)

    assert out == []
    assert stats["dropped_one_agrees_with_script"] == 1


def test_the_unsure_counter_does_not_fire_when_the_other_agrees_with_the_script():
    """Выемка по UNSURE считается, но «второй спорит» — только когда он и правда спорит."""
    stats: dict = {}
    out = compare(*_maps([(1, 5, "Дгарнин", "UNSURE", "Дгарнин", "")]), stats=stats)

    assert out == []
    assert stats["dropped_unsure_from_one"] == 1
    assert stats["dropped_unsure_other_disputes_script"] == 0


def test_a_reader_that_did_not_answer_is_skipped_not_counted_as_agreement():
    """Неотвеченный абзац — дыра в прогоне, а не согласие: молчание не голос."""
    opus, sol, current, named = _maps([(1, 5, "Дгарнин", "Тупуг", "Тупуг", "")])
    del sol[(1, 5)]

    assert compare(opus, sol, current, named) == []


def test_unsure_from_both_is_not_a_finding():
    """«Не знаю» обоих — не спор со сценарием, а признание трудности."""
    out = compare(*_maps([(1, 5, "Дгарнин", "UNSURE", "UNSURE", "")]))

    assert out == []


def test_one_reader_alone_is_not_a_quorum():
    """Пропуск второго чтеца не превращает мнение первого в согласие двоих.

    Самый опасный вид подмены: голос одного засчитан за двоих — и место попадает в
    находки, хотя второй чтец его не читал. Такая находка выглядит как согласие двух
    независимых читателей, которого не было.
    """
    opus, sol, current, named = _maps([(1, 5, "Дгарнин", "Тупуг", "Тупуг", "")])
    del sol[(1, 5)]

    assert compare(opus, sol, current, named) == []


def test_a_hole_in_one_paragraph_does_not_blind_the_rest():
    """Пропуск в одном абзаце не должен уносить с собой соседние: исключение точечное."""
    opus, sol, current, named = _maps([
        (1, 5, "Дгарнин", "Тупуг", "Тупуг", ""),
        (1, 9, "Гамук", "Пупип", "Пупип", ""),
    ])
    del sol[(1, 5)]

    out = compare(opus, sol, current, named)

    assert [(f.ordinal, f.kind) for f in out] == [(9, "wrong_voice")]

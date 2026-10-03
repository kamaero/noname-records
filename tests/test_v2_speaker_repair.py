"""Names that arrive as half-decoded escapes are repaired, not thrown away.

Muse Spark answers with the speaker's name escaped — `\\u041c\\u0430…` — and something
between the model and us decodes `\\u04` as a two-digit escape, so «Маркиз Пфаль»
arrives as chr(4) + "1c" + chr(4) + "30" … The name is intact, just shifted a byte:
each control character carries the high byte of the codepoint and the two characters
after it are its low byte in hex. 436 spans of one bench run came in like this and
were dropped as «каст не знает», which is a parser problem being counted against the
model.
"""
from app.v2.attribute import repair_escaped_name


def test_a_shifted_escape_is_put_back_together():
    assert repair_escaped_name("\x041c\x0430\x0440\x043a\x0438\x0437 \x041f\x0444\x0430\x043b\x044c") == "Маркиз Пфаль"
    assert repair_escaped_name("\x0421\x044d\x0440 \x0423\x043b\x044c\x0433\x0430\x043d\x0434") == "Сэр Ульганд"
    assert repair_escaped_name("\x0420\x0430\x0441\x0441\x043a\x0430\x0437\x0447\x0438\x043a") == "Рассказчик"


def test_an_ordinary_name_is_returned_unchanged():
    for name in ("Дгарнин", "Коруак (управляющий Банка Душ)", "UNSURE", ""):
        assert repair_escaped_name(name) == name


def test_a_control_character_that_decodes_to_nothing_readable_is_dropped():
    # the same run produced this: control bytes with no hex pairs behind them
    assert repair_escaped_name("\x04\x00\x03\x06\x08\r\r\x08") == ""


def test_the_parser_uses_it():
    from app.v2.attribute import parts_to_spans
    from app.v2.units import Unit

    unit = Unit(id="u1", chapter="c", ordinal=0, text="- Ну что ж, - сказал рыцарь.")
    spans = parts_to_spans(
        {"id": "u1", "speaker": "\x0421\x044d\x0440 \x0423\x043b\x044c\x0433\x0430\x043d\x0434", "confidence": 0.9},
        unit, [],
    )
    assert spans["spans"][0]["speaker"] == "Сэр Ульганд"

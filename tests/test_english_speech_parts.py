from app.services.speech_parts import speech_and_narration


def test_english_author_tag_is_not_spoken():
    assert speech_and_narration('"Hello," Alice said.', language="en") == ("Hello,", ["Alice said."])


def test_english_interrupted_dialogue_keeps_both_parts():
    assert speech_and_narration('“Hello,” Alice said, “again.”', language="en") == (
        "Hello, again.", ["Alice said,"]
    )


def test_unrecognised_english_tail_does_not_hide_possible_speech():
    text = '"Hello," she continued running.'
    assert speech_and_narration(text, language="en") == (text, [])


def test_unclosed_english_quote_is_kept_whole():
    text = '“Hello, Alice said.'
    assert speech_and_narration(text, language="en") == (text, [])


def test_english_single_quotes_and_contractions():
    assert speech_and_narration("'Don't go,' Alice said.", language="en") == (
        "Don't go,", ["Alice said."]
    )


def test_leading_english_author_tag():
    assert speech_and_narration('Alice said, "Hello."', language="en") == (
        "Hello.", ["Alice said,"]
    )

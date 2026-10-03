"""Which book does `KP` in `KP_CH04_Химера_Иванова.wav` mean?

The old resolution was `ScriptBook.title.ilike('%KP%')` — a substring search for a
Latin abbreviation inside a Cyrillic title. It matches «Крылья полумрака» never, so
the book filter silently switched itself off and the chapter was taken from whichever
book happened to have a chapter with that number.
"""
from app.services.audio_uploads import book_code_matches_title


def test_the_initials_of_a_russian_title_are_its_code():
    assert book_code_matches_title("Крылья полумрака", "КП")
    assert book_code_matches_title("Сказки волшебников 2", "СВ2")


def test_the_same_code_typed_in_latin_letters_still_matches():
    """Actors type filenames on a Latin layout: `KP_CH04_...`."""
    assert book_code_matches_title("Крылья полумрака", "KP")
    assert book_code_matches_title("Сказки волшебников 2", "SV2")


def test_a_code_belonging_to_another_book_does_not_match():
    assert not book_code_matches_title("Крылья полумрака", "SV2")
    assert not book_code_matches_title("Крылья полумрака", "ПБ")


def test_a_code_that_merely_occurs_inside_the_title_does_not_match():
    """The substring search's failure mode, in the other direction.

    «Полумракские байки» contains the letters of no code by intent; a rule built on
    containment would tie books together by accident as soon as one title's letters
    appear in another's.
    """
    assert not book_code_matches_title("Крылья полумрака", "Крылья")
    assert not book_code_matches_title("Полумракские байки", "П")


def test_an_empty_code_matches_nothing():
    # A missing code must narrow to no book, never to every book.
    assert not book_code_matches_title("Крылья полумрака", "")
    assert not book_code_matches_title("", "КП")

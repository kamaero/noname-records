"""Cutting a book into segments that keep their identity.

The whole point of v2 is that the text never changes: a model annotates it, and an
annotation points at a segment by id. That only works if the ids are stable — the same
source must always produce the same ids, and adding a chapter at the end must not
renumber anything before it.

Measured on «Крылья полумрака»: 13 278 blocks separated by blank lines, none longer
than 1033 characters, none carrying an internal newline, 60 headings of the form
«Глава N. Название».
"""
from app.v2.segmenter import Segment, find_chapter_starts, segment_chapter

CHAPTER = """Глава 1. Ничего особенного

Дгарнин сидел в изгибе ветвей, сложив крылья.

- Нас стало слишком много, - задумчиво сказал Дгарнин.
"""


class TestSegmenting:
    def test_a_blank_line_separates_paragraphs(self):
        segments = segment_chapter(CHAPTER, chapter_id="ch1")

        assert [s.text for s in segments] == [
            "Глава 1. Ничего особенного",
            "Дгарнин сидел в изгибе ветвей, сложив крылья.",
            "- Нас стало слишком много, - задумчиво сказал Дгарнин.",
        ]

    def test_the_first_line_is_a_heading_and_the_rest_are_not(self):
        segments = segment_chapter(CHAPTER, chapter_id="ch1")

        assert [s.kind for s in segments] == ["heading", "paragraph", "paragraph"]

    def test_ids_carry_the_chapter_and_the_position(self):
        segments = segment_chapter(CHAPTER, chapter_id="ch1")

        assert [s.id for s in segments] == ["ch1:00000", "ch1:00001", "ch1:00002"]
        assert [s.ordinal for s in segments] == [0, 1, 2]

    def test_offsets_address_the_text_they_came_from(self):
        for segment in segment_chapter(CHAPTER, chapter_id="ch1"):
            assert CHAPTER[segment.char_start:segment.char_end] == segment.text

    def test_nothing_but_whitespace_is_lost(self):
        segments = segment_chapter(CHAPTER, chapter_id="ch1")

        assert "".join("".join(s.text.split()) for s in segments) == "".join(CHAPTER.split())

    def test_an_empty_chapter_yields_nothing(self):
        assert segment_chapter("", chapter_id="ch1") == []
        assert segment_chapter("\n\n   \n", chapter_id="ch1") == []


class TestStability:
    def test_running_twice_gives_the_same_ids(self):
        first = segment_chapter(CHAPTER, chapter_id="ch1")
        second = segment_chapter(CHAPTER, chapter_id="ch1")

        assert [s.id for s in first] == [s.id for s in second]
        assert [s.text for s in first] == [s.text for s in second]

    def test_appending_to_the_end_leaves_earlier_ids_alone(self):
        before = segment_chapter(CHAPTER, chapter_id="ch1")
        after = segment_chapter(CHAPTER + "\nИ он ушёл.\n", chapter_id="ch1")

        assert [s.id for s in after][:len(before)] == [s.id for s in before]
        assert [s.text for s in after][:len(before)] == [s.text for s in before]

    def test_a_different_chapter_gives_different_ids_for_the_same_prose(self):
        one = segment_chapter(CHAPTER, chapter_id="ch1")
        two = segment_chapter(CHAPTER, chapter_id="ch2")

        assert {s.id for s in one}.isdisjoint({s.id for s in two})


class TestLongParagraphs:
    def test_a_paragraph_past_the_limit_is_cut_at_sentence_ends(self):
        """None of «Крылья» needs this — its longest block is 1033 characters — but a
        book that does must not hand a model one segment it cannot annotate."""
        sentences = ["Это предложение номер {}.".format(i) for i in range(40)]
        long_block = " ".join(sentences)
        segments = segment_chapter(long_block, chapter_id="ch1", max_chars=200)

        assert len(segments) > 1
        assert all(len(s.text) <= 200 for s in segments)
        assert all(s.kind == "paragraph" for s in segments)

    def test_the_pieces_still_address_the_original_text(self):
        long_block = " ".join(f"Предложение {i} тут." for i in range(40))
        for segment in segment_chapter(long_block, chapter_id="ch1", max_chars=200):
            assert long_block[segment.char_start:segment.char_end] == segment.text

    def test_a_sentence_longer_than_the_limit_is_kept_whole(self):
        """Cutting mid-sentence would hand back a fragment nobody can attribute."""
        monster = "А" * 500 + "."
        segments = segment_chapter(monster, chapter_id="ch1", max_chars=200)

        assert [s.text for s in segments] == [monster]


class TestChapterStarts:
    def test_headings_are_found_by_offset(self):
        book = "Автор\n\nГлава 1. Раз\n\nТекст.\n\nГлава 2. Два\n\nЕщё текст.\n"
        starts = find_chapter_starts(book)

        assert [book[i:i + 7] for i in starts] == ["Глава 1", "Глава 2"]

    def test_the_word_glava_inside_a_sentence_is_not_a_heading(self):
        book = "Глава 1. Раз\n\nОн поднял глаза, и глава города вышел вперёд.\n"

        assert len(find_chapter_starts(book)) == 1

    def test_a_book_without_headings_has_no_starts(self):
        assert find_chapter_starts("Просто текст без заголовков.\n") == []

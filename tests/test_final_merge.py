"""Merging N model answers into ONE valid script.

FINAL splits a chapter into parts and each part returns its own [CAST] block, so the
old `"\\n\\n".join(parts)` produced a document with N cast blocks scattered through
the body. _repair_cast_from_dialogues treats everything after the FIRST block as
body, so entries from the other blocks look like "stray cast entries" and get
deleted — and so does any real text the model wrote with the cast separator.

Measured across «Крылья полумрака» (75 final artifacts, 231 cast blocks):

    1550 «::» lines whose text is NOT in the book  -> genuine character descriptions
     725 «::» lines starting with a dialogue dash  -> replies
     303 «::» lines without a dash but IN the book -> author's narration

So 1028 lines of the author's own text were being destroyed. Neither position nor
blank lines nor the leading dash separate these reliably — but the source text does:
the model INVENTS descriptions and COPIES prose. That is the criterion used here.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.pipeline.final_merge import merge_parts, split_cast_block

SOURCE = (
    "Какое-то время в зале стояла тишина. Бароны обдумывали предложение. "
    "- Хм… так ли уж нужен нам барон-лекарь, если подумать? – уточнила Дазиптовд. "
    "- Привет! Меня зовут Оснива. - сказала маленькая девочка весело подмигнув. - Мне 120 лет."
)


def test_split_separates_the_leading_cast_block_from_the_body():
    part = "\n".join([
        "[CAST]",
        "[Дгарни́н] :: барон",
        "[Химе́ра] :: чудовище",
        "",
        "[Дгарни́н] — Первая реплика.",
    ])
    entries, body = split_cast_block(part)
    assert entries == [("Дгарни́н", "барон"), ("Химе́ра", "чудовище")]
    assert "[CAST]" not in body


def test_split_handles_a_part_with_no_cast_block():
    part = "[Дгарни́н] — Только реплика.\nИ нарратив."
    entries, body = split_cast_block(part)
    assert entries == []
    assert body == part


def test_merge_produces_exactly_one_cast_block():
    parts = [
        "[CAST]\n[Дгарни́н] :: барон\n\n[Дгарни́н] — Раз.",
        "[CAST]\n[Химе́ра] :: чудовище\n\n[Химе́ра] — Два.",
    ]
    merged, report = merge_parts(parts, SOURCE)
    assert merged.count("[CAST]") == 1
    assert merged.startswith("[CAST]")
    assert "— Раз." in merged and "— Два." in merged
    assert report["cast_blocks"] == 2


def test_duplicate_names_collapse_across_spellings():
    # Every part re-lists the same characters: 184 entries for 27 names in chapter 15.
    parts = [
        "[CAST]\n[Дгарни́н] :: барон\n\n[Дгарни́н] — Раз.",
        "[CAST]\n[Дгарнин] :: барон Полумрака\n\n[Дгарнин] — Два.",
    ]
    merged, report = merge_parts(parts, SOURCE)
    assert merged.count("] :: ") == 1
    assert report["cast_entries_merged"] == 1
    assert "барон Полумрака" in merged  # the richer description wins


def test_a_description_the_model_invented_stays_a_cast_entry():
    # Not in the source -> the model wrote it about the character, not from the book.
    parts = ["[CAST]\n[Полибнугак] :: Вспыльчивый барон в облике розового младенца\n\n[Полибнугак] — Реплика."]
    merged, report = merge_parts(parts, SOURCE)
    assert "[Полибнугак] :: Вспыльчивый барон в облике розового младенца" in merged
    assert report["replies_recovered"] == 0
    assert report["narration_recovered"] == 0


def test_a_reply_written_with_the_cast_separator_is_recovered():
    line = "[Дазиптовд] :: - Хм… так ли уж нужен нам барон-лекарь, если подумать? – уточнила Дазиптовд."
    parts = [f"[CAST]\n[Дазиптовд] :: Матерь Демонов\n{line}"]
    merged, report = merge_parts(parts, SOURCE)
    assert "[Дазиптовд] — - Хм… так ли уж нужен нам барон-лекарь" in merged
    assert report["replies_recovered"] == 1


def test_narration_loses_its_label_and_reads_as_in_the_book():
    # The narrator's actor wants prose to look exactly like the book — no label,
    # no marker, nothing set apart.
    line = "[Дгарни́н] :: Какое-то время в зале стояла тишина. Бароны обдумывали предложение."
    parts = [f"[CAST]\n[Дгарни́н] :: барон\n{line}"]
    merged, report = merge_parts(parts, SOURCE)
    assert "Какое-то время в зале стояла тишина. Бароны обдумывали предложение." in merged
    assert "[Дгарни́н] :: Какое-то время" not in merged
    assert "[Дгарни́н] — Какое-то время" not in merged
    assert report["narration_recovered"] == 1


def test_narration_inside_a_reply_is_never_split():
    """The author's example, verbatim.

    A character's line carrying the author's words inside it stays ONE line, exactly
    as in the book. Only the separator changes; the spoken text is copied byte for
    byte and never cut at the inner dashes.
    """
    spoken = "- Привет! Меня зовут Оснива. - сказала маленькая девочка весело подмигнув. - Мне 120 лет."
    parts = [f"[CAST]\n[Оснива] :: девочка\n[Оснива] :: {spoken}"]
    merged, report = merge_parts(parts, SOURCE)
    assert f"[Оснива] — {spoken}" in merged
    # One line, not three: the inner dashes must not have become new replies.
    assert merged.count("[Оснива] —") == 1
    body_line = [l for l in merged.split("\n") if l.startswith("[Оснива] —")][0]
    assert body_line.split(" — ", 1)[1] == spoken


def test_empty_and_blank_parts_are_ignored():
    merged, report = merge_parts(["", "   ", "[CAST]\n[Дгарни́н] :: барон\n\n[Дгарни́н] — Раз."], SOURCE)
    assert merged.count("[CAST]") == 1
    assert report["cast_blocks"] == 1


def test_a_single_part_round_trips_without_damage():
    part = "\n".join([
        "[CAST]",
        "[Дгарни́н] :: барон",
        "",
        "[Дгарни́н] — Реплика.",
        "Нарратив без метки.",
    ])
    merged, _ = merge_parts([part], SOURCE)
    for line in part.split("\n"):
        if line.strip():
            assert line in merged


def test_no_parts_at_all_yields_empty_output():
    merged, report = merge_parts([], SOURCE)
    assert merged == ""
    assert report["cast_blocks"] == 0


def test_without_a_source_nothing_is_reclassified():
    # No source text means no evidence; guessing would risk turning a real character
    # description into spoken narration.
    line = "[Дазиптовд] :: - Хм… так ли уж нужен нам барон-лекарь?"
    merged, report = merge_parts([f"[CAST]\n[Дазиптовд] :: Матерь\n{line}"], "")
    assert report["replies_recovered"] == 0
    assert report["narration_recovered"] == 0

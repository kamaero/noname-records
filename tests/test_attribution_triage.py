import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.pipeline.attribution_triage import (
    classify_cue, normalize_label, line_hash, triage_chapter, SuspectLine,
)


def test_normalize_label_collapses_mojibake_to_canonical_key():
    assert normalize_label("Звезди́л") == normalize_label("Звезди́l")
    assert normalize_label("Звездил") == normalize_label("Zvezdil")


def test_classify_clean_known_speaker():
    approved = {normalize_label("Звездил"), normalize_label("Пупип")}
    assert classify_cue("Звезди́л", approved) == "clean"


def test_classify_unsure_marker():
    assert classify_cue("UNSURE: кто-то из парней", set()) == "unsure_marker"


def test_classify_garbled_label_when_norm_matches_cast():
    approved = {normalize_label("Звездил")}
    assert classify_cue("Zvezdil", approved) == "garbled_label"


def test_classify_unknown_speaker_when_not_in_cast():
    assert classify_cue("Незнакомец", {normalize_label("Звездил")}) == "unknown_speaker"


def test_triage_chapter_flags_only_suspect_cue_lines():
    fountain = "\n".join([
        "[CAST]",                            # no dash → not a cue line → skipped
        "[Звезди́л] — Чистая реплика.",
        "сказал он, отхлёбывая чай.",
        "[Zvezdil] — Гарбл-реплика.",
        "[UNSURE: кто-то] — Кто это сказал?",
    ])
    approved = {normalize_label("Звездил")}
    out = triage_chapter(chapter_index=1, fountain_text=fountain, approved_norms=approved)
    assert [(s.line_index, s.signal) for s in out] == [(3, "garbled_label"), (4, "unsure_marker")]
    assert all(isinstance(s, SuspectLine) for s in out)
    assert out[0].current_speaker == "Zvezdil"
    assert out[0].line_hash == line_hash("[Zvezdil] — Гарбл-реплика.")


def test_line_hash_has_prefix_and_fixed_length():
    h = line_hash("[Звезди́л] — реплика")
    assert h.startswith("sha256:")
    assert len(h) == len("sha256:") + 16


def test_unsure_marker_leaked_into_the_dialogue_body_is_flagged():
    # Real shape from «Крылья полумрака» ch38: the speaker label is a genuine cast
    # member, so label-only triage calls the line clean, while the unresolved-speaker
    # marker sits inside the spoken text — invisible to the bench, and carried along
    # into whatever the voice actor reads.
    line = "[Химе́ра] — [UNSURE: возмо́жно Куйбу и́ли Звезди́л] Быть ку́рицей?"
    approved = {normalize_label("Химера")}
    out = triage_chapter(chapter_index=38, fountain_text=line, approved_norms=approved)
    assert [(s.line_index, s.signal) for s in out] == [(0, "unsure_in_body")]
    assert out[0].current_speaker == "Химе́ра"
    assert out[0].decision_class == "resolve_unsure"


def test_clean_line_without_a_body_marker_stays_clean():
    line = "[Химе́ра] — Быть ку́рицей?"
    approved = {normalize_label("Химера")}
    assert triage_chapter(chapter_index=38, fountain_text=line, approved_norms=approved) == []


def test_author_bracket_in_the_body_is_not_a_suspect():
    # «[цензура]» is the author's own text — 12 occurrences in the source file.
    # Flagging every bracket would drown the bench in false positives.
    line = "[Полибнуга́к] — Ина́че тебе́ [цензу́ра]."
    approved = {normalize_label("Полибнугак")}
    assert triage_chapter(chapter_index=18, fountain_text=line, approved_norms=approved) == []

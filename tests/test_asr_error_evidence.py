"""Личное обвинение опирается на два слуха и координаты своего исходника."""
from copy import deepcopy

import pytest

from app.services.asr_error_evidence import confirmed_error_items, omission_only, primary_window


def flag(expected="Он осклабился и сказал.", primary="Он оскалился и сказал.", secondary=None):
    return {"text": expected, "missing_words": ["осклабился"], "status": "partial", "confirmed": True,
            "source_windows": [{"source_audio_file_id": "take", "start": 100, "end": 110}],
            "confirmation_evidence": [{"source_audio_file_id": "take", "start": 100, "end": 110,
                "primary_text": primary, "secondary_text": secondary if secondary is not None else primary,
                "primary_words": [{"word": "Он", "start": 101, "end": 102},
                                  {"word": "оскалился", "start": 103, "end": 104},
                                  {"word": "и", "start": 105, "end": 106},
                                  {"word": "сказал", "start": 107, "end": 108}], "confirmed": True}]}


def test_confirmed_replacement_uses_source_word_time_and_basename():
    items = confirmed_error_items([flag()], source_names={"take": r"C:\recordings\take.wav"})
    assert items == [{"source_audio_file_id": "take", "filename": "take.wav", "start": 103.0,
                      "end": 104.0, "expected": "осклабился", "heard": "оскалился",
                      "context": "Он осклабился и сказал."}]


@pytest.mark.parametrize("secondary", ["Он осклабился и сказал.", "Он усмехнулся и сказал.", "Он и сказал."])
def test_second_recognizer_must_agree_on_same_spoken_replacement(secondary):
    assert confirmed_error_items([flag(secondary=secondary)]) == []


@pytest.mark.parametrize("spoken", ["Он и сказал.", "Он осклабился и тихо сказал.", ""])
def test_omissions_and_insertions_are_not_spoken_error_notices(spoken):
    assert confirmed_error_items([flag(primary=spoken)]) == []


def test_source_scope_legacy_or_malformed_proof_cannot_accuse_actor():
    source = flag()
    assert confirmed_error_items([source], allowed_source_ids={"someone-else"}) == []
    for payload in [None, {}, "broken", [None, "broken", {}, {"confirmed": True}]]:
        changed = deepcopy(source)
        changed["confirmation_evidence"] = payload
        assert confirmed_error_items([changed]) == []
    changed = deepcopy(source)
    changed["confirmation_evidence"][0]["source_audio_file_id"] = "another-take"
    assert confirmed_error_items([changed]) == []
    assert confirmed_error_items([source, source]) == confirmed_error_items([source])


def test_mixed_omission_keeps_only_independently_spoken_replacement():
    source = flag(expected="Он осклабился и тихо сказал.", primary="Он оскалился и сказал.")
    assert [(i["expected"], i["heard"]) for i in confirmed_error_items([source])] == [("осклабился", "оскалился")]


def test_english_evidence_uses_contractions_and_number_profile():
    source = flag(expected="He can't carry 21 silver keys.",
                  primary="He cannot carry twenty one golden keys.",
                  secondary="He can't carry twenty one golden keys.")
    source["missing_words"] = ["silver"]
    source["confirmation_evidence"][0]["primary_words"] = []
    items = confirmed_error_items([source], language="en")
    assert [(i["expected"], i["heard"]) for i in items] == [("silver", "golden")]
    assert omission_only("He can't carry 21 keys.", "He cannot carry keys.", language="en")


def test_primary_window_retains_original_offsets_and_language():
    transcript = {"segments": [{"words": [{"word": "21", "start": 100, "end": 101},
                                             {"word": "keys", "start": 103, "end": 104},
                                             {"word": "outside", "start": 110, "end": 111}]}]}
    window = primary_window(transcript, 100, 109, language="en")
    assert window["text"] == "twenty one keys"
    assert all(100 <= word["start"] < word["end"] <= 109 for word in window["words"])


"""Личное обвинение требует совпавших замен и координат исходника."""

import pytest

from app.services import asr_audit, asr_notice
from app.services.asr_error_evidence import confirmed_error_items, primary_window


def _proof(expected="Он осклабился и тихо сказал.", heard="Он оскалился и сказал.", secondary=None, source="a"):
    return {"text": expected, "missing_words": ["осклабился", "тихо"], "status": "partial", "confirmed": True,
            "source_windows": [{"source_audio_file_id": source, "start": 100., "end": 110.}],
            "confirmation_evidence": [{"source_audio_file_id": source, "start": 100., "end": 110.,
                 "primary_text": heard, "secondary_text": secondary if secondary is not None else heard,
                 "primary_words": [{"word": word, "start": 100. + i, "end": 100.5 + i}
                                   for i, word in enumerate(heard.split())], "confirmed": True}]}


def test_user_example_reports_substitution_not_omitted_neighbor():
    flag = _proof()
    items = confirmed_error_items([flag], source_names={"a": "/private/tree/source.wav"})
    assert items == [{"source_audio_file_id": "a", "filename": "source.wav", "start": 101., "end": 101.5,
                      "expected": "осклабился", "heard": "оскалился", "context": flag["text"]}]
    assert confirmed_error_items([flag, flag]) == confirmed_error_items([flag])


@pytest.mark.parametrize(("expected", "heard", "suspect"), [
    ("Он выбрал красный цвет", "Он выбрал красного цвет", "красный"),
    ("Он тихо сказал", "Он спокойно сказал", "тихо"),
])
def test_agreed_grammatical_forms_and_synonyms_remain_detectable(expected, heard, suspect):
    flag = _proof(expected, heard)
    flag["missing_words"] = [suspect]
    assert len(confirmed_error_items([flag])) == 1


@pytest.mark.parametrize(("heard", "secondary"), [
    ("Он и тихо сказал", "Он и тихо сказал"),
    ("Он оскалился и тихо сказал", "Он осклабился и тихо сказал"),
    ("", "Он оскалился и тихо сказал"),
    ("Он оскалился и тихо сказал", ""),
    ("Он усмехнулся сказал", "Он усмехнулся сказал"),
])
def test_omissions_disagreement_and_unequal_replace_blocks_fail_closed(heard, secondary):
    assert confirmed_error_items([_proof(heard=heard, secondary=secondary)]) == []


def test_case_punctuation_and_yo_are_equal():
    flag = _proof("Ёжик СКАЗАЛ!", "ежик сказал")
    flag["missing_words"] = ["Ёжик", "сказал"]
    assert confirmed_error_items([flag]) == []


@pytest.mark.parametrize("change", [
    lambda flag: flag.update(confirmed=False),
    lambda flag: flag.pop("confirmation_evidence"),
    lambda flag: flag["confirmation_evidence"][0].update(confirmed=False),
    lambda flag: flag["confirmation_evidence"][0].update(source_audio_file_id="other"),
    lambda flag: flag["confirmation_evidence"][0].update(start=99.),
    lambda flag: flag.update(confirmation_evidence=[None, "old"]),
    lambda flag: flag.update(confirmation_evidence="old"),
    lambda flag: flag.update(source_windows="old"),
])
def test_untrustworthy_legacy_or_mismatched_proof_never_accuses(change):
    flag = _proof()
    change(flag)
    assert confirmed_error_items([None, "old", flag]) == []


def test_invalid_word_rows_do_not_break_notice():
    flag = _proof()
    flag["confirmation_evidence"][0]["primary_words"] = [None, "old", {}]
    item = confirmed_error_items([flag])[0]
    assert (item["start"], item["end"]) == (100., 110.)


def test_actor_source_filter_excludes_other_recording():
    flags = [_proof(source="a"), _proof(source="b")]
    items = confirmed_error_items(flags, source_names={"a": "a.wav", "b": "b.wav"}, allowed_source_ids={"a"})
    assert len(items) == 1 and items[0]["filename"] == "a.wav"


def test_primary_window_never_reads_neighbor_source_or_cut_coordinates():
    transcript = {"segments": [{"words": [{"word": "сосед", "start": 99., "end": 99.5},
        {"word": "ошибка", "start": 101., "end": 101.5}, {"word": "хвост", "start": 111., "end": 112.}]}]}
    assert primary_window(transcript, 100., 110.)["text"] == "ошибка"


@pytest.mark.parametrize("heard", ["", "сказал", "он", "сказал он"])
def test_optional_tag_omission_never_pays_for_confirmation(monkeypatch, heard):
    flag = {"index": 0, "text": "- сказал он.", "missing_words": ["сказал"], "status": "partial",
            "omission_optional": True, "source_windows": [{"source_audio_file_id": "a", "start": 100., "end": 110.}]}
    transcript = {"segments": [{"text": heard, "start": 100., "end": 110.}]}
    def fail(*args, **kwargs):
        pytest.fail("Для необязательного пропуска не нужен платный вызов")
    assert asr_audit._confirm_flags(flags=[flag], source_transcripts={"a": transcript},
                                   judge=fail, confirm_transcribe=fail) == []


def test_confirmation_persists_independent_text_and_source_window(monkeypatch):
    flag = _proof()
    flag.update(index=0, status="partial", confirmed=False)
    monkeypatch.setattr(asr_audit, "_cut_window", lambda *args: None)
    transcript = {"segments": [{"text": "Он оскалился и сказал", "start": 100., "end": 110.}]}
    confirmed = asr_audit._confirm_flags(flags=[flag], source_paths={"a": "a.wav"},
        source_transcripts={"a": transcript}, judge=lambda *args: {"results": [{"index": 0, "confirmed": True, "note": "замена"}]},
        confirm_transcribe=lambda path: ({"text": "Он оскалился и сказал"}, .1))
    assert confirmed == [flag]
    assert flag["confirmation_evidence"][0]["secondary_text"] == "Он оскалился и сказал"
    assert len(confirmed_error_items(confirmed)) == 1


def _items(count=30):
    return [{"filename": "🎧" * 500, "start": 101., "end": 102., "expected": f"ожидалось{i}",
             "heard": f"услышано{i}", "context": "😀" * 8000} for i in range(count)]


def test_all_items_survive_utf16_chunks_with_repeated_headers():
    messages = asr_notice.audit_findings_messages(role="Рассказчик", chapter="Глава", book_title="Книга", items=_items())
    assert len(messages) > 1
    for number, message in enumerate(messages, 1):
        assert asr_notice._utf16_size(message) <= 3500
        assert f"Part {number}/{len(messages)}" in message
        assert "Role: Рассказчик" in message
        assert message.count("Expected:") == message.count("Heard:")
        assert "00:01:41" in message
    combined = "\n".join(messages)
    for index in range(30):
        assert combined.count(f"«ожидалось{index}»") == 1


def test_unknown_time_is_not_midnight():
    message = asr_notice.audit_findings_messages(role="Р", chapter="Г", book_title="К", items=[{
        "expected": "слово", "heard": "другое", "start": None, "end": None}])[0]
    assert "time unknown" in message and "00:00:00" not in message


def test_legacy_texts_send_nothing(monkeypatch):
    monkeypatch.setattr(asr_notice, "resolve_actor_candidates", lambda *args: pytest.fail("нет доказательств"))
    assert asr_notice.notify_audit_findings(None, actor_name="А", role="Р", chapter="Г", book_title="К",
                                          book_id="b", texts=["пропуск"]) ["notified"] is False


def test_chunk_delivery_resolves_once_and_stops_on_failure(monkeypatch):
    resolved, sent = [], []
    monkeypatch.setattr(asr_notice, "resolve_actor_candidates", lambda *args: resolved.append(1) or [("actor", "А")])
    result = asr_notice.notify_audit_findings(None, actor_name="А", role="Р", chapter="Г", book_title="К",
        book_id="b", items=_items(), send=lambda db, text, **kwargs: sent.append(kwargs) or (len(sent) < 2))
    assert len(resolved) == 1 and len(sent) == 2 and result["reason"] == "not_sent"
    assert all(row == {"chat_ids": ["actor"], "direct": True} for row in sent)


def test_ambiguous_actor_relays_only_once(monkeypatch):
    sent = []
    monkeypatch.setattr(asr_notice, "resolve_actor_candidates", lambda *args: [("a", "А"), ("b", "Б")])
    monkeypatch.setattr(asr_notice, "relay_to_owner_and_agents", lambda db, text, **kwargs: sent.append(text) or 1)
    result = asr_notice.notify_audit_findings(None, actor_name="А", role="Р", chapter="Г", book_title="К", book_id="b", items=_items())
    assert result["reason"] == "ambiguous" and len(sent) == 1


@pytest.mark.parametrize("tag", ["- сказал он.", "сказал он,", "— спокойно сказал он, —"])
def test_regular_coverage_hides_neutral_narrator_tags_only(tag):
    lines = [{"text": tag, "matched": False}, {"text": "- сказал он и выстрелил.", "matched": False}]
    notice = asr_notice.missing_lines_notice(role="Рассказчик", chapter="Г", book_title="К", lines=lines)
    assert tag not in notice and "выстрелил" in notice
    assert tag in asr_notice.missing_lines_notice(role="Герой", chapter="Г", book_title="К", lines=lines)
    assert tag in asr_notice.moved_lines_notice(role="Рассказчик", chapter="Г", book_title="К", texts=[tag])


@pytest.mark.parametrize("tag", ["said Aster.", "— she whispered, —", "сказал он,", "— спокойно сказал он, —"])
def test_public_tag_filter_preserves_language_and_explicit_narrator_identity(tag):
    language = "en" if "Aster" in tag or "she" in tag else "ru"
    lines = [{"text": tag, "matched": False}, {"text": "He closed the gate.", "matched": False}]
    notice = asr_notice.missing_lines_notice(role="Story voice", chapter="Chapter", book_title="Book",
                                             lines=lines, language=language, narrator=True)
    assert tag not in notice and "He closed the gate." in notice
    assert tag in asr_notice.missing_lines_notice(role="Narrator", chapter="Chapter", book_title="Book",
                                                  lines=lines, language=language, narrator=False)


def test_public_english_optional_partial_tag_never_pays_for_confirmation():
    source = {"index": 0, "text": "said Aster.", "status": "partial", "missing_words": ["Aster"],
              "omission_optional": True, "source_windows": [{"source_audio_file_id": "take", "start": 100, "end": 110}]}
    transcript = {"segments": [{"text": "said", "start": 100, "end": 110}]}
    def fail(*args, **kwargs):
        pytest.fail("An optional omission must not trigger a paid request")
    assert asr_audit._confirm_flags(flags=[source], source_transcripts={"take": transcript},
        language="en", judge=fail, confirm_transcribe=fail) == []


@pytest.mark.parametrize("status", ["missing", "", None, "unknown"])
def test_missing_whole_line_never_becomes_spoken_error(status):
    source = flag()
    source["status"] = status
    assert confirmed_error_items([source]) == []

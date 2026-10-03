from app.pipeline.routerai_bench import CandidateResult, evaluate_candidate, format_candidates_table


def _eval(**over):
    base = dict(
        name="x", provider="routerai", model="google/gemini-2.0-flash-001",
        content='{"name":"Иван"}', usage={"prompt_tokens": 1000, "completion_tokens": 1500},
        latency_s=2.0, error="", runaway_output_floor=58000, runaway_ratio=5.0,
    )
    base.update(over)
    return evaluate_candidate(**base)


def test_clean_json_is_ok_and_not_runaway():
    r = _eval()
    assert isinstance(r, CandidateResult)
    assert r.ok is True
    assert r.runaway is False
    assert r.ratio == 1.5


def test_empty_content_is_not_ok():
    assert _eval(content="").ok is False


def test_error_is_not_ok():
    r = _eval(error="LLM HTTP 403: region not supported", content="")
    assert r.ok is False
    assert r.error


def test_truncation_error_flags_runaway():
    r = _eval(error="LLM output truncated at max_tokens (output_tokens=64000)", content="")
    assert r.runaway is True


def test_high_output_floor_flags_runaway():
    r = _eval(usage={"prompt_tokens": 3000, "completion_tokens": 60000})
    assert r.runaway is True


def test_high_ratio_flags_runaway():
    r = _eval(usage={"prompt_tokens": 1000, "completion_tokens": 9000})
    assert r.runaway is True
    assert r.ratio == 9.0


def test_table_has_header_and_one_row_per_result():
    rows = [_eval(name="A"), _eval(name="B")]
    table = format_candidates_table(rows)
    lines = table.splitlines()
    assert lines[0].startswith("| Кандидат |")
    assert lines[1].startswith("|---")
    assert len(lines) == 4  # header + separator + 2 rows


def test_table_marks_runaway_and_json_status():
    rows = [_eval(name="bad", content="", error="LLM output truncated at max_tokens")]
    table = format_candidates_table(rows)
    assert "⚠" in table   # runaway marked
    assert "✗" in table   # json not ok

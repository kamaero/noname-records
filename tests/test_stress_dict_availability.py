"""A misconfigured stress dictionary must fail before the model runs, not after.

Chapter 15 of «Крылья полумрака» spent 102 minutes in FINAL — the model processed
the whole chapter — and then died on

    [Errno 13] Permission denied: '/home/someone/data/stress_base_dict.json'

because .env still pointed at a developer's copy that the service user could not
read. The dictionary is loaded lazily on the first word, which
is the very end of the stage, so a configuration error costs a full run.

Two things are wrong and both are fixed here:
  - Path.exists() sat outside the try. In Python 3.12 it swallows "not found" but
    re-raises PermissionError, so an unreadable path escaped as an exception while
    an unparsable one was quietly swallowed — the two failures behaved oppositely.
  - Nothing checked the dictionary before spending the model budget.
"""
import os
import stat
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from app.v2 import stress_dict as base_dict


def test_a_configured_but_unreadable_dictionary_raises_a_clear_error(tmp_path, monkeypatch):
    victim = tmp_path / "stress_base_dict.json"
    victim.write_text('{"words": {}}', encoding="utf-8")
    victim.chmod(0)
    monkeypatch.setattr(base_dict.settings, "stress_base_dict_path", str(victim), raising=False)
    base_dict.reset_cache()

    if os.geteuid() == 0:
        pytest.skip("running as root — chmod 0 does not deny root, cannot exercise EACCES")

    with pytest.raises(RuntimeError) as exc:
        base_dict.ensure_available()
    message = str(exc.value)
    assert "stress" in message.lower()
    assert str(victim) in message


def test_a_missing_dictionary_is_reported_too(tmp_path, monkeypatch):
    missing = tmp_path / "not-there.json"
    monkeypatch.setattr(base_dict.settings, "stress_base_dict_path", str(missing), raising=False)
    base_dict.reset_cache()

    with pytest.raises(RuntimeError) as exc:
        base_dict.ensure_available()
    assert str(missing) in str(exc.value)


def test_no_configured_path_is_not_an_error(monkeypatch):
    # Stress marking is optional; an empty setting means "not configured", which is a
    # choice rather than a fault.
    monkeypatch.setattr(base_dict.settings, "stress_base_dict_path", "", raising=False)
    base_dict.reset_cache()
    base_dict.ensure_available()


def test_a_readable_dictionary_passes_and_loads(tmp_path, monkeypatch):
    # The real schema: {"forms": {word: "сло́во"}, "ambiguous": {word: [...]}}.
    # A single form comes back as a string, homographs as a list.
    good = tmp_path / "stress_base_dict.json"
    good.write_text(
        '{"forms": {"кроны": "кро́ны"}, "ambiguous": {"замок": ["за́мок", "замо́к"]}}',
        encoding="utf-8",
    )
    good.chmod(stat.S_IRUSR | stat.S_IWUSR)
    monkeypatch.setattr(base_dict.settings, "stress_base_dict_path", str(good), raising=False)
    base_dict.reset_cache()

    base_dict.ensure_available()
    assert base_dict.lookup("кроны") == "кро́ны"
    assert base_dict.lookup("замок") == ["за́мок", "замо́к"]


def test_unreadable_no_longer_escapes_from_the_lazy_load(tmp_path, monkeypatch):
    """The old code let PermissionError out of Path.exists() mid-chapter.

    ensure_available is the loud path; the lazy load must stay quiet-and-empty so a
    late surprise cannot destroy a run that already cost an hour of model time.
    """
    victim = tmp_path / "stress_base_dict.json"
    victim.write_text('{"words": {}}', encoding="utf-8")
    victim.chmod(0)
    monkeypatch.setattr(base_dict.settings, "stress_base_dict_path", str(victim), raising=False)
    base_dict.reset_cache()

    if os.geteuid() == 0:
        pytest.skip("running as root — chmod 0 does not deny root, cannot exercise EACCES")

    assert base_dict.lookup("что-угодно") is None

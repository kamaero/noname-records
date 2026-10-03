"""Build `app/v2/common_words_ru.txt` — the Russian word forms common enough to need no stress mark.

«Только редкие» in the reader shows a stress mark only on a word a reader in full flow
may get wrong. Frequency is what decides: a form met at least once per million words
(wordfreq's zipf ≥ 3) is one the reader already says right. The list comes from
wordfreq (https://github.com/rspeer/wordfreq, data CC BY-SA 4.0), which is not a
dependency of the app — run this from any environment that has it:

    uv venv /tmp/wf && VIRTUAL_ENV=/tmp/wf uv pip install wordfreq
    /tmp/wf/bin/python scripts/v2/build_common_words.py
"""
from __future__ import annotations

import pathlib
import re

from wordfreq import iter_wordlist, zipf_frequency

THRESHOLD = 3.0
TARGET = pathlib.Path(__file__).resolve().parents[2] / "app" / "v2" / "common_words_ru.txt"
WORD = re.compile(r"^[а-яё]+(?:-[а-яё]+)*$")


def main() -> int:
    words = []
    for word in iter_wordlist("ru", "large"):
        if zipf_frequency(word, "ru") < THRESHOLD:
            break  # the list is ordered by frequency
        if WORD.match(word):
            words.append(word)
    header = f"# wordfreq ru, zipf >= {THRESHOLD}; CC BY-SA 4.0; built by scripts/v2/build_common_words.py\n"
    TARGET.write_text(header + "\n".join(sorted(set(words))) + "\n", encoding="utf-8")
    print(f"{TARGET}: {len(set(words))} words")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

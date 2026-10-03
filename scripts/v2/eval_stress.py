"""How good are the dictionaries and RUAccent, measured against the old pipeline's marks.

There is no human-verified stress reference for «Крылья полумрака». What exists is
the old pipeline's `script_chapters.fountain_text`: ~179k inline marks placed by
the dictionary and an LLM, unverified. The v1 columns left the database with v1
(2026-09-29); the text lives on in `data/stress_eval/polumrak_v1_fountain.json`,
exported from the June backup that still had the v1 book. It is used as a *weak* reference: a word
counts only if it occurs at least three times and every occurrence carries the mark
on the same vowel — consistency is the closest thing to confidence the data offers.
A disagreement is therefore a question for a human, not an error; the 20 most
frequent ones are printed with a sentence each so the human can answer it.

Run with a venv that has ruaccent (see requirements-stress.txt):

    python scripts/v2/eval_stress.py                 # defaults: the exported v1 reference
    python scripts/v2/eval_stress.py --skip-ruaccent  # dictionaries and author list only
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import pathlib
import random
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.v2.stress import (  # noqa: E402
    ACUTE,
    Resolver,
    author_map_from_entries,
    cyrillic_runs,
    default_dict_lookup,
    find_words,
    is_vowel,
    vowel_count,
)

REPO = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_REFERENCE = REPO / "data" / "stress_eval" / "polumrak_v1_fountain.json"
DEFAULT_AUTHOR_JSON = REPO / "data" / "author_stress" / "polumrak.json"

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?…])\s+")
_MAX_SENTENCE = 400


def load_chapter_texts(reference_path: str) -> tuple[str, list[str]]:
    """(book label, fountain text of every chapter in reading order) from the exported reference."""
    with open(reference_path, encoding="utf-8") as handle:
        data = json.load(handle)
    chapters = sorted(data.get("chapters") or [], key=lambda item: int(item.get("chapter_index") or 0))
    label = f"{data.get('title', '')} ({data.get('book_id', '')})"
    return label, [str(item.get("fountain_text") or "") for item in chapters]


def sentences_of(text: str) -> list[str]:
    """Prose sentences of a fountain chapter; the cast block (`::` lines) is not prose."""
    out: list[str] = []
    for line in text.split("\n"):
        line = line.strip()
        if not line or " :: " in line or line == "CAST" or line == "[CAST]":
            continue
        out.extend(s for s in _SENTENCE_SPLIT_RE.split(line) if s)
    return out


def strip_marks(text: str) -> str:
    return text.replace(ACUTE, "").replace("̀", "")


def marked_word_offset(token: str) -> int | None:
    """Offset of the stressed vowel in the token with its mark removed, or None."""
    pos = token.find(ACUTE)
    if pos <= 0:
        return None
    clean_pos = pos - 1
    return clean_pos if is_vowel(token[clean_pos]) else None


class Corpus:
    """Every multi-vowel word occurrence of the book, with the old pipeline's mark and a sentence."""

    def __init__(self) -> None:
        self.occurrences: dict[str, list[int | None]] = collections.defaultdict(list)
        self.capitalised: dict[str, list[bool]] = collections.defaultdict(list)
        self.sample_sentence: dict[str, tuple[str, int]] = {}
        # (clean sentence, [(key, word_index, ref_offset)]) — for homographs in context
        self.sentences: list[tuple[str, list[tuple[str, int, int | None]]]] = []

    def add_sentence(self, marked: str) -> None:
        clean = strip_marks(marked)
        if len(clean) > _MAX_SENTENCE:
            return
        tokens = [marked[s:e] for s, e in cyrillic_runs(marked)]
        words = [t for t in tokens if vowel_count(strip_marks(t)) >= 2]
        if len(words) != len(find_words(clean)):
            return  # a run the mark split oddly; skip rather than misalign
        entries: list[tuple[str, int, int | None]] = []
        for index, token in enumerate(words):
            plain = strip_marks(token)
            key = plain.lower()
            offset = marked_word_offset(token)
            self.occurrences[key].append(offset)
            self.capitalised[key].append(plain[0].isupper())
            self.sample_sentence.setdefault(key, (clean, index))
            entries.append((key, index, offset))
        self.sentences.append((clean, entries))

    def weak_reference(self, min_count: int = 3) -> dict[str, int]:
        ref: dict[str, int] = {}
        for key, offsets in self.occurrences.items():
            if len(offsets) < min_count or any(o is None for o in offsets):
                continue
            if len(set(offsets)) == 1:
                ref[key] = offsets[0]
        return ref

    def proper_names(self, min_count: int = 3) -> list[str]:
        """Words capitalised in every occurrence and unknown to the dictionaries."""
        names = []
        for key, flags in self.capitalised.items():
            if len(flags) >= min_count and all(flags) and default_dict_lookup(key) is None:
                names.append(key)
        return names


def form(word: str, offset: int | None) -> str:
    if offset is None or not (0 <= offset < len(word)):
        return word + "(?)"
    return word[: offset + 1] + ACUTE + word[offset + 1 :]


def pct(n: int, d: int) -> str:
    return f"{100.0 * n / d:5.1f}%" if d else "   n/a"


class RuaccentRunner:
    def __init__(self, accent) -> None:
        self.accent = accent
        self.cache: dict[str, dict[int, int]] = {}
        self.seconds = 0.0
        self.words = 0
        self.failures = 0

    def answers(self, sentence: str) -> dict[int, int]:
        from app.v2.stress_ruaccent import align_plus_marks

        if sentence in self.cache:
            return self.cache[sentence]
        started = time.perf_counter()
        try:
            marked = self.accent.process_all(sentence)
            got = align_plus_marks(sentence, marked)
        except Exception:
            self.failures += 1
            got = {}
        self.seconds += time.perf_counter() - started
        self.words += len(cyrillic_runs(sentence))
        self.cache[sentence] = got
        return got


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference", default=str(DEFAULT_REFERENCE))
    parser.add_argument("--author-json", default=str(DEFAULT_AUTHOR_JSON))
    parser.add_argument("--sample", type=int, default=2000)
    parser.add_argument("--homograph-cap", type=int, default=300)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--skip-ruaccent", action="store_true")
    args = parser.parse_args()

    book_label, chapters = load_chapter_texts(args.reference)
    corpus = Corpus()
    for text in chapters:
        for sentence in sentences_of(text):
            corpus.add_sentence(sentence)
    ref = corpus.weak_reference()
    total_marks = sum(1 for offsets in corpus.occurrences.values() for o in offsets if o is not None)
    print(f"book {book_label}: {len(chapters)} chapters, {len(corpus.sentences)} sentences, "
          f"{sum(len(v) for v in corpus.occurrences.values())} word occurrences (≥2 vowels), "
          f"{len(corpus.occurrences)} distinct words, {total_marks} inline marks")
    print(f"weak reference (≥3 occurrences, one consistent vowel): {len(ref)} words")

    author = {}
    if os.path.exists(args.author_json):
        author = author_map_from_entries(json.load(open(args.author_json, encoding="utf-8"))["entries"])
    resolver = Resolver.default(author=author)

    rnd = random.Random(args.seed)
    sampled = sorted(ref)
    if len(sampled) > args.sample:
        sampled = rnd.sample(sampled, args.sample)
    sampled.sort()

    # (a) dictionaries against the reference
    by_layer = {"author": [], "rule": [], "dict": [], "homograph": [], "oov": []}
    dict_agree = 0
    dict_disagree: list[tuple[str, int, int]] = []
    for word in sampled:
        if resolver.author_offset(word) is not None:
            by_layer["author"].append(word)
            continue
        if "ё" in word:
            by_layer["rule"].append(word)
            continue
        found = default_dict_lookup(word)
        if isinstance(found, int):
            by_layer["dict"].append(word)
            if found == ref[word]:
                dict_agree += 1
            else:
                dict_disagree.append((word, ref[word], found))
        elif isinstance(found, list):
            by_layer["homograph"].append(word)
        else:
            by_layer["oov"].append(word)
    n = len(sampled)
    print()
    print(f"sample: {n} reference words (seed {args.seed})")
    print(f"  author list        {len(by_layer['author']):5d}  {pct(len(by_layer['author']), n)}")
    print(f"  ё rule             {len(by_layer['rule']):5d}  {pct(len(by_layer['rule']), n)}")
    print(f"  dict unambiguous   {len(by_layer['dict']):5d}  {pct(len(by_layer['dict']), n)}   agree with reference {dict_agree}/{len(by_layer['dict'])} = {pct(dict_agree, len(by_layer['dict']))}")
    print(f"  dict homograph     {len(by_layer['homograph']):5d}  {pct(len(by_layer['homograph']), n)}")
    print(f"  out of dictionary  {len(by_layer['oov']):5d}  {pct(len(by_layer['oov']), n)}")

    # (c) author list against the book's proper names
    names = corpus.proper_names()
    covered = [w for w in names if resolver.author_offset(w) is not None]
    exact = [w for w in names if w in author]
    print()
    print(f"proper names in book (capitalised everywhere, ≥3 occurrences, not in dictionaries): {len(names)}")
    print(f"  covered by author list: {len(covered)} ({len(exact)} exact, {len(covered) - len(exact)} by stem) = {pct(len(covered), len(names))}")
    print(f"  author list words present in book: {sum(1 for w in author if w in corpus.occurrences)}/{len(author)}")
    uncovered = sorted((w for w in names if w not in covered), key=lambda w: -len(corpus.occurrences[w]))
    print("  most frequent uncovered names: " + ", ".join(f"{w}×{len(corpus.occurrences[w])}" for w in uncovered[:15]))

    if args.skip_ruaccent:
        _print_disagreements("dictionary vs reference", dict_disagree, corpus, limit=20)
        return 0

    from app.v2.stress_ruaccent import get_ruaccent

    started = time.perf_counter()
    accent = get_ruaccent()
    if accent is None:
        print("\nruaccent is not installed in this venv; stopping after the dictionary part")
        _print_disagreements("dictionary vs reference", dict_disagree, corpus, limit=20)
        return 1
    print(f"\nruaccent loaded in {time.perf_counter() - started:.1f} s")
    runner = RuaccentRunner(accent)

    # (b) RUAccent against the reference, per layer
    ru_stats = {layer: {"answered": 0, "agree": 0, "total": 0} for layer in ("dict", "oov", "homograph", "author")}
    ru_disagree: list[tuple[str, int, int]] = []
    for layer in ("dict", "oov", "homograph", "author"):
        for word in by_layer[layer]:
            sentence, index = corpus.sample_sentence[word]
            got = runner.answers(sentence).get(index)
            stat = ru_stats[layer]
            stat["total"] += 1
            if got is None:
                continue
            stat["answered"] += 1
            if got == ref[word]:
                stat["agree"] += 1
            else:
                ru_disagree.append((word, ref[word], got))
    print()
    print("RUAccent vs reference (one sentence per word):")
    for layer, label in (("dict", "in-dict words"), ("oov", "out-of-dict words"), ("homograph", "dict homographs"), ("author", "author-list names")):
        s = ru_stats[layer]
        print(f"  {label:18s} {s['total']:5d}  answered {s['answered']:5d} ({pct(s['answered'], s['total'])})  agree {s['agree']:5d}/{s['answered']} = {pct(s['agree'], s['answered'])}")

    # homographs in context: every occurrence the old pipeline marked, up to the cap
    homograph_words = {w for w in corpus.occurrences if isinstance(default_dict_lookup(w), list)}
    checked = agreed = answered = 0
    seen_sentences = 0
    homo_disagree: collections.Counter = collections.Counter()
    homo_example: dict[tuple[str, int, int], str] = {}
    for sentence, entries in corpus.sentences:
        marked_homographs = [(k, i, o) for k, i, o in entries if k in homograph_words and o is not None]
        if not marked_homographs:
            continue
        seen_sentences += 1
        if seen_sentences > args.homograph_cap:
            break
        got_all = runner.answers(sentence)
        for key, index, ref_offset in marked_homographs:
            checked += 1
            got = got_all.get(index)
            if got is None:
                continue
            answered += 1
            if got == ref_offset:
                agreed += 1
            else:
                homo_disagree[(key, ref_offset, got)] += 1
                homo_example.setdefault((key, ref_offset, got), sentence)
    print(f"  homographs in ctx  {checked:5d} occurrences in {min(seen_sentences, args.homograph_cap)} sentences  answered {answered} ({pct(answered, checked)})  agree with old pipeline {agreed}/{answered} = {pct(agreed, answered)}")
    print(f"  failures: {runner.failures}")

    # (d) speed
    print(f"\nRUAccent time: {runner.seconds:.1f} s for {runner.words} words in {len(runner.cache)} sentences = {1000 * runner.seconds / max(runner.words, 1):.2f} s per 1000 words")

    # sizes: what is left for an LLM
    distinct = sorted(corpus.occurrences)
    layer_of: dict[str, str] = {}
    for word in distinct:
        if resolver.author_offset(word) is not None:
            layer_of[word] = "author"
        elif "ё" in word:
            layer_of[word] = "rule"
        else:
            found = default_dict_lookup(word)
            layer_of[word] = "dict" if isinstance(found, int) else "homograph" if isinstance(found, list) else "oov"
    counts = collections.Counter(layer_of.values())
    oov = [w for w in distinct if layer_of[w] == "oov"]
    started = time.perf_counter()
    unanswered: list[str] = []
    chunk = 100
    for i in range(0, len(oov), chunk):
        words = oov[i : i + chunk]
        text = " ".join(words)
        got = runner.answers(text)
        unanswered.extend(w for j, w in enumerate(words) if j not in got)
    oov_seconds = time.perf_counter() - started
    print()
    print(f"distinct words in book (≥2 vowels): {len(distinct)}")
    print(f"  author {counts['author']}, ё-rule {counts['rule']}, dict {counts['dict']}, homograph→context {counts['homograph']}, out-of-dict→context {counts['oov']}")
    print(f"  out-of-dict words RUAccent leaves unanswered (bare-word pass, {oov_seconds:.0f} s): {len(unanswered)} "
          f"= LLM residue {pct(len(unanswered), len(distinct))} of distinct words, "
          f"{sum(len(corpus.occurrences[w]) for w in unanswered)} occurrences")
    unanswered.sort(key=lambda w: -len(corpus.occurrences[w]))
    print("  most frequent unanswered: " + ", ".join(f"{w}×{len(corpus.occurrences[w])}" for w in unanswered[:25]))

    _print_disagreements("dictionary vs reference", dict_disagree, corpus, limit=10)
    _print_disagreements("RUAccent vs reference", ru_disagree, corpus, limit=20)
    if homo_disagree:
        print("\nhomographs in context: RUAccent vs old pipeline, most frequent disagreements")
        for (key, ref_offset, got), count in homo_disagree.most_common(10):
            print(f"  ×{count:3d}  old {form(key, ref_offset)}  →  ruaccent {form(key, got)}   | {homo_example[(key, ref_offset, got)][:160]}")
    return 0


def _print_disagreements(title: str, items: list[tuple[str, int, int]], corpus: Corpus, *, limit: int) -> None:
    if not items:
        print(f"\n{title}: none")
        return
    items = sorted(items, key=lambda t: -len(corpus.occurrences[t[0]]))
    print(f"\n{title}: {len(items)} disagreements, {min(limit, len(items))} most frequent (count = occurrences in book)")
    for word, ref_offset, got in items[:limit]:
        sentence, _ = corpus.sample_sentence[word]
        print(f"  ×{len(corpus.occurrences[word]):4d}  ref {form(word, ref_offset):22s} other {form(word, got):22s} | {sentence[:150]}")


if __name__ == "__main__":
    raise SystemExit(main())

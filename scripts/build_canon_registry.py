"""K1: build the canon character registry from the Belozerov corpus.

Runs the council (per-window recall gemini-3.5-flash + arbiter deepseek-v4-pro)
over each narrative book in data/corpus/, reconciles cross-book into a
registry (conservative — see canon_registry.py), and writes data/canon_kb.sqlite
+ a build manifest. The lore reference ('Справочные материалы') is SKIPPED here
(it is world-lore, not a character roster — it feeds the K2 vector layer).

Isolated: writes ONLY data/canon_kb.sqlite. Boevая noname.db is not touched.

Запуск:  .venv/bin/python scripts/build_canon_registry.py
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
import sqlite3
import sys
import time

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from app.pipeline.canon_registry import reconcile_book_into_registry  # noqa: E402
from app.pipeline.council import run_council_charext_chunked  # noqa: E402
from app.pipeline.llm_client import _resolve_provider, call_chat  # noqa: E402
from app.pipeline.text_splitter import _split_text_parts  # noqa: E402

CORPUS_DIR = "data/corpus"
DB_PATH = os.path.join(_REPO_ROOT, "data", "canon_kb.sqlite")
WINDOW_CHARS = 40000
SCHEMA_VERSION = "canon-1"
RECALLER = ("gemini-3.5-flash", "routerai", "google/gemini-3.5-flash")
ARBITER = ("deepseek-v4-pro", "deepseek", "deepseek-v4-pro")
LORE_SKIP = "Справочные материалы"  # not a character roster -> K2 vector layer


def _make_run_fn(usage: dict):
    def run_fn(provider, model, system, user, schema):
        api_key, base_url, mode = _resolve_provider(provider)
        if not api_key:
            raise RuntimeError(f"no API key for provider {provider}")
        out = call_chat(base_url, api_key, model, system, user, mode=mode, force_json=True, json_schema=schema)
        u = out.get("usage", {}) or {}
        usage["prompt"] += int(u.get("prompt_tokens") or 0)
        usage["completion"] += int(u.get("completion_tokens") or 0)
        content = str(out.get("content") or "").strip()
        return json.loads(content) if content else {"characters": []}
    return run_fn


def _corpus_hash(paths: list[str]) -> str:
    h = hashlib.sha256()
    for p in sorted(paths):
        h.update(f"{os.path.basename(p)}:{os.path.getsize(p)}".encode())
    return h.hexdigest()[:16]


def _init_db(con):
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS canon_character (
            id INTEGER PRIMARY KEY,
            canonical TEXT, aliases_json TEXT, source_books_json TEXT,
            appears_count INTEGER, confidence REAL, merge_status TEXT,
            review_required INTEGER, authority_source TEXT
        );
        CREATE TABLE IF NOT EXISTS canon_build_manifest (
            id INTEGER PRIMARY KEY, built_at TEXT, corpus_hash TEXT, books_json TEXT,
            recaller TEXT, arbiter TEXT, window_chars INTEGER, schema_version TEXT,
            council_usage_json TEXT, registry_size INTEGER, review_required_count INTEGER
        );
        """
    )


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="store_true", help="actually run the PAID build (default: dry-run plan only)")
    args = parser.parse_args()

    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    books = sorted(glob.glob(os.path.join(CORPUS_DIR, "*.txt")))
    books = [b for b in books if LORE_SKIP not in os.path.basename(b)]

    if not args.run:
        print(f"DRY-RUN (no paid calls). Pass --run to execute.\n{len(books)} narrative books, window={WINDOW_CHARS} chars:")
        total_win = 0
        for b in books:
            text = open(b, encoding="utf-8").read()
            w = len(_split_text_parts(text, max_chars=WINDOW_CHARS))
            total_win += w
            print(f"  {os.path.basename(b)[:44]:44} {len(text)//1024:5}KB -> {w:3} windows")
        print(f"TOTAL ~{total_win} recaller calls + {len(books)} arbiter calls. Lore skipped: {LORE_SKIP}")
        return

    print(f"K1 build: {len(books)} narrative books (lore '{LORE_SKIP}' skipped) -> {DB_PATH}")

    usage = {"prompt": 0, "completion": 0}
    run_fn = _make_run_fn(usage)
    registry: list = []
    t0 = time.time()

    for bi, path in enumerate(books, start=1):
        name = os.path.splitext(os.path.basename(path))[0]
        text = open(path, encoding="utf-8").read()
        windows = _split_text_parts(text, max_chars=WINDOW_CHARS)
        chapters = list(enumerate(windows, start=1))
        bt = time.time()
        result = run_council_charext_chunked(chapters=chapters, recaller=RECALLER, arbiter=ARBITER, run_fn=run_fn)
        cast = result["arbiter"].characters or result["participants"][0].characters
        before = len(registry)
        registry, decisions = reconcile_book_into_registry(registry, cast, name)
        merged = sum(1 for d in decisions if d[0] == "same_person")
        flagged = sum(1 for d in decisions if d[0] == "possible_same")
        print(
            f"  [{bi}/{len(books)}] {name[:34]:34} win={len(windows):3} cast={len(cast):3} "
            f"-> reg {before}->{len(registry)} (same={merged} flagged={flagged}) {time.time()-bt:.0f}s"
        )

    con = sqlite3.connect(DB_PATH)
    try:
        _init_db(con)
        con.execute("DELETE FROM canon_character")
        for e in registry:
            con.execute(
                "INSERT INTO canon_character(canonical,aliases_json,source_books_json,appears_count,confidence,merge_status,review_required,authority_source)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (e.canonical, json.dumps(e.aliases, ensure_ascii=False), json.dumps(e.source_books, ensure_ascii=False),
                 e.appears_count, e.confidence, e.merge_status, int(e.review_required), e.authority_source),
            )
        review_n = sum(1 for e in registry if e.review_required)
        con.execute(
            "INSERT INTO canon_build_manifest(built_at,corpus_hash,books_json,recaller,arbiter,window_chars,schema_version,council_usage_json,registry_size,review_required_count)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            (time.strftime("%Y-%m-%d %H:%M:%S"), _corpus_hash(books), json.dumps([os.path.basename(b) for b in books], ensure_ascii=False),
             RECALLER[2], ARBITER[2], WINDOW_CHARS, SCHEMA_VERSION, json.dumps(usage), len(registry), review_n),
        )
        con.commit()
    finally:
        con.close()

    print(
        f"\nDONE in {time.time()-t0:.0f}s | registry={len(registry)} | review_required={review_n} | "
        f"council tokens: prompt={usage['prompt']:,} completion={usage['completion']:,}"
    )


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Import pronunciation dictionary from one or more orfoepic PDFs.

Supported formats:
  - Свиридова (Аделант, 2014)     — words inline with U+0301 accent
  - Орфоэпический словарь РЛЯ    — UPPERCASE headword + lowercase forms,
                                    PDF artifact: U+0301 followed by a space
                                    before the next Cyrillic letter

Quality filters:
  - U+0301 must immediately follow a Cyrillic vowel
  - Plain word must be 4+ characters
  - Plain word must be all-Cyrillic (no digits / Latin)
  - Common prepositions / particles are skipped

Usage:
  # Single PDF → new dict
  python scripts/import_pronunciation_pdf.py --pdf data/dicts/uchitel_4.pdf

  # Multiple PDFs → merge, prioritising first file on conflicts
  python scripts/import_pronunciation_pdf.py \\
      --pdf data/dicts/uchitel_4.pdf \\
      --pdf data/dicts/orfoepicheskij_slovar.pdf \\
      --out app/pronunciation_dict.json

  # Add a new PDF on top of the existing dict
  python scripts/import_pronunciation_pdf.py \\
      --pdf data/dicts/orfoepicheskij_slovar.pdf \\
      --merge app/pronunciation_dict.json \\
      --out app/pronunciation_dict.json
"""
import argparse
import json
import re
from pathlib import Path

from pypdf import PdfReader


ACCENT = "\u0301"       # combining acute (primary stress)
GRAVE  = "\u0300"       # combining grave (secondary stress — strip it)
VOWELS = set("аеёиоуыэюяАЕЁИОУЫЭЮЯ")

STOPWORDS = {
    "а", "без", "бы", "в", "во", "вот", "все", "да", "даже", "для",
    "до", "её", "если", "есть", "ещё", "же", "за", "здесь", "и", "из",
    "или", "им", "их", "к", "как", "когда", "кто", "ли", "лишь",
    "между", "меня", "мне", "может", "мой", "мы", "на", "над", "нам",
    "нас", "не", "него", "нее", "неё", "нет", "ни", "но", "ну", "о",
    "об", "один", "она", "они", "оно", "он", "от", "очень", "по",
    "под", "при", "про", "раз", "с", "сам", "себя", "со", "так",
    "такой", "там", "тебя", "тем", "то", "только", "тут", "ты",
    "у", "уже", "хоть", "чем", "что", "чтоб", "чтобы", "эта",
    "это", "этот", "я",
}


def strip_accents(word: str) -> str:
    return word.replace(ACCENT, "").replace(GRAVE, "")


def normalize_page_text(text: str) -> str:
    """
    Fix the PDF-extraction artefact common in academic orfoepic dicts:
    U+0301 is separated from the following letter by a space.
    Example: 'авиамоде́ ль' → 'авиамоде́ль'
    Also strip secondary-stress graves.
    """
    text = text.replace(GRAVE, "")
    text = re.sub(rf"{ACCENT}\s+([А-ЯЁа-яё])", rf"{ACCENT}\1", text)
    return text


def accent_is_valid(token: str) -> bool:
    """Every U+0301 in the token must directly follow a Cyrillic vowel."""
    chars = list(token)
    for i, ch in enumerate(chars):
        if ch == ACCENT:
            if i == 0 or chars[i - 1] not in VOWELS:
                return False
    return True


def extract_candidates(text: str) -> list[str]:
    """Extract all Cyrillic tokens that contain at least one U+0301."""
    return re.findall(
        rf"[А-Яа-яЁё][А-Яа-яЁё{ACCENT}-]*{ACCENT}[А-Яа-яЁё{ACCENT}-]*",
        text,
    )


def build_dict_from_pdf(pdf_path: Path) -> tuple[dict[str, set[str]], dict[str, int]]:
    """
    Parse one PDF and return (bucket, stats).
    bucket: plain_word → set of stressed variants
    stats:  counters for skipped/accepted entries
    """
    reader = PdfReader(str(pdf_path))
    bucket: dict[str, set[str]] = {}
    stats = dict(bad_accent=0, too_short=0, stopword=0, bad_chars=0, accepted=0)

    for page in reader.pages:
        raw = page.extract_text() or ""
        text = normalize_page_text(raw)

        for token in extract_candidates(text):
            plain = strip_accents(token).lower().strip("-")
            stressed = token.lower().strip("-")

            if not plain or not stressed:
                continue
            if not accent_is_valid(stressed):
                stats["bad_accent"] += 1
                continue
            if len(plain) < 4:
                stats["too_short"] += 1
                continue
            if plain in STOPWORDS:
                stats["stopword"] += 1
                continue
            if not re.fullmatch(r"[а-яёА-ЯЁ][а-яёА-ЯЁ-]*", plain):
                stats["bad_chars"] += 1
                continue

            bucket.setdefault(plain, set()).add(stressed)
            stats["accepted"] += 1

    return bucket, stats


def bucket_to_dict(bucket: dict[str, set[str]]) -> dict[str, str | list[str]]:
    out: dict[str, str | list[str]] = {}
    for plain, variants in sorted(bucket.items()):
        vals = sorted(variants)
        out[plain] = vals[0] if len(vals) == 1 else vals
    return out


def load_existing(path: Path) -> dict[str, set[str]]:
    """Load an existing pronunciation JSON into bucket format."""
    bucket: dict[str, set[str]] = {}
    if not path.exists():
        return bucket
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return bucket
    for k, v in data.items():
        key = str(k).strip().lower()
        if not key:
            continue
        if isinstance(v, str):
            bucket.setdefault(key, set()).add(v.strip())
        elif isinstance(v, list):
            for item in v:
                s = str(item).strip()
                if s:
                    bucket.setdefault(key, set()).add(s)
    return bucket


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import pronunciation dictionary from PDF(s)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--pdf", action="append", required=True, metavar="PDF",
        help="Path to a source PDF (repeat to merge multiple)",
    )
    parser.add_argument(
        "--out", default="app/pronunciation_dict.json",
        help="Output JSON path (default: app/pronunciation_dict.json)",
    )
    parser.add_argument(
        "--merge", metavar="EXISTING_JSON",
        help="Path to an existing dict JSON to merge new data into",
    )
    args = parser.parse_args()

    # Start from existing dict if --merge supplied
    merged_bucket: dict[str, set[str]] = {}
    if args.merge:
        merged_bucket = load_existing(Path(args.merge))
        print(f"Loaded existing dict: {len(merged_bucket)} entries from {args.merge}")

    # Parse each PDF and merge (later PDFs fill gaps, not override)
    for pdf_path_str in args.pdf:
        pdf_path = Path(pdf_path_str)
        print(f"\nParsing {pdf_path.name} …")
        bucket, stats = build_dict_from_pdf(pdf_path)
        print(
            f"  accepted={stats['accepted']}  bad_accent={stats['bad_accent']}  "
            f"too_short={stats['too_short']}  stopword={stats['stopword']}  "
            f"bad_chars={stats['bad_chars']}"
        )
        new_words = 0
        for plain, variants in bucket.items():
            if plain not in merged_bucket:
                merged_bucket[plain] = variants
                new_words += 1
            else:
                # Merge variant sets
                merged_bucket[plain].update(variants)
        print(f"  new words added: {new_words}")

    out_dict = bucket_to_dict(merged_bucket)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out_dict, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nDone: {len(out_dict)} total entries → {out_path}")


if __name__ == "__main__":
    main()

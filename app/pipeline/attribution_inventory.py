"""Опись книги: на чём держится каждая реплика и что осталось без опоры.

Читает только на чтение. Действующей считается версия АБЗАЦА — берётся через
`app/v2/reader.effective_attributions`, а не своим запросом: правило одно на систему,
и вторая его копия однажды разъедется с первой.
"""
from __future__ import annotations

from app.models import Character, ScriptChapter
from app.pipeline.attribution_evidence import (
    NARRATOR, PROVEN_KINDS, classify_chapter, consecutive_same_speaker,
)
from app.pipeline.attribution_resolve import build_cast_index
from app.pipeline.attribution_triage import normalize_label
from app.v2.reader import effective_attributions
from app.v2.store import load_chapter_segments

KINDS = ("cue_named", "cue_adjacent", "address_by_name", "alternation",
         "unnamed_speaker", "unsupported")
DOUBTFUL_KINDS = ("unsupported", "unnamed_speaker")


def _cast(db, book_id: str) -> list[Character]:
    return db.query(Character).filter(Character.book_id == book_id).all()


def _chapter_paragraphs(db, chapter_id: str) -> list[dict]:
    segments = load_chapter_segments(db, chapter_id=chapter_id)
    rows = effective_attributions(db, [segment.id for segment in segments])
    paragraphs = []
    for segment in segments:
        spans = [{"speaker": row.speaker, "start": row.span_start, "end": row.span_end}
                 for row in rows.get(segment.id, [])]
        paragraphs.append({"paragraph": segment.ordinal, "text": segment.text,
                           "spans": spans})
    return paragraphs


def build_inventory(db, *, book_id: str) -> dict:
    cast = _cast(db, book_id)
    names = build_cast_index([(row.name, [a.strip() for a in (row.aliases or "").split(",")
                                          if a.strip()]) for row in cast])
    appears = {row.name.strip(): {int(p) for p in str(row.appears_in or "").split(",")
                                  if p.strip().isdigit()} for row in cast}

    totals = dict.fromkeys(KINDS, 0)
    chapters_out, doubtful, proven = [], [], []
    outside, no_list, not_in_cast, twice_in_a_row = [], [], [], []

    chapters = (db.query(ScriptChapter)
                .filter(ScriptChapter.book_id == book_id)
                .order_by(ScriptChapter.chapter_index.asc()).all())
    for chapter in chapters:
        index = int(chapter.chapter_index or 0)
        paragraphs = _chapter_paragraphs(db, str(chapter.id))
        evidence = classify_chapter(paragraphs, names=names)
        counts = dict.fromkeys(KINDS, 0)
        by_paragraph = {int(p["paragraph"]): p for p in paragraphs}
        # Ярлык берётся из разметки, а не из вердикта: у доказанных отрезков вердикт
        # несёт каноничное имя, и сравнение с кастом шло бы по разным величинам —
        # каноничное имя у одних и сырой ярлык у других.
        labels = {(int(p["paragraph"]), int(s["start"]), int(s["end"])):
                  str(s.get("speaker") or "").strip()
                  for p in paragraphs for s in p["spans"]}
        for row in consecutive_same_speaker(paragraphs):
            twice_in_a_row.append({"chapter_index": index, **row})
        for (paragraph, start, end), found in evidence.items():
            counts[found.kind] += 1
            totals[found.kind] += 1
            label = labels.get((paragraph, start, end), "")
            text = str(by_paragraph[paragraph]["text"])[start:end]
            if found.kind in DOUBTFUL_KINDS:
                doubtful.append({"chapter_index": index, "paragraph": paragraph,
                                 "speaker": label, "text": text[:200],
                                 "kind": found.kind})
            if found.kind in PROVEN_KINDS:
                # Цитата и абзац, откуда она взята, — единственное, чем доказанную
                # реплику можно перепроверить руками, не читая книгу целиком.
                proven.append({"chapter_index": index, "paragraph": paragraph,
                               "speaker": label, "kind": found.kind,
                               "text": text[:200], "quote": found.quote,
                               "evidence_paragraph": int(found.paragraph or 0)})
            if label == NARRATOR:
                continue
            canonical = names.get(normalize_label(label), "")
            row = {"speaker": label, "chapter_index": index, "paragraph": paragraph}
            if not canonical or canonical == "<AMBIGUOUS>":
                not_in_cast.append(row)
            elif not appears.get(canonical):
                no_list.append(row)
            elif index not in appears[canonical]:
                outside.append(row)
        chapters_out.append({"chapter_index": index,
                             "title": str(chapter.chapter_title or ""),
                             "replicas": sum(counts.values()), "counts": counts})

    return {"book_id": book_id, "totals": totals, "chapters": chapters_out,
            "cross_checks": {"outside_appears_in": outside, "no_cast_chapters": no_list,
                             "label_not_in_cast": not_in_cast,
                             "same_speaker_twice": twice_in_a_row},
            "proven": proven, "doubtful": doubtful}


def render_inventory(inventory: dict) -> str:
    """Сводка для человека: сколько доказано, сколько под вопросом, что именно."""
    totals = inventory["totals"]
    proven = sum(totals[kind] for kind in PROVEN_KINDS)
    doubtful = totals["unsupported"]
    guessed = totals["unnamed_speaker"]
    all_replicas = proven + doubtful + guessed
    share = (proven / all_replicas * 100) if all_replicas else 0.0
    lines = [
        f"реплик персонажей: {all_replicas}",
        f"  доказано текстом: {proven} ({share:.1f} %)",
        *(f"    {kind}: {totals[kind]}" for kind in PROVEN_KINDS),
        f"  под вопросом: {doubtful}",
        f"  домыслено (неназванный говорящий): {guessed}",
        "",
        "перекрёстные проверки:",
        f"  говорят вне своих глав: {len(inventory['cross_checks']['outside_appears_in'])}",
        f"  персонаж без списка глав: {len(inventory['cross_checks']['no_cast_chapters'])}",
        f"  ярлык вне каста: {len(inventory['cross_checks']['label_not_in_cast'])}",
        f"  две реплики подряд за одним лицом: "
        f"{len(inventory['cross_checks'].get('same_speaker_twice') or [])}",
        "",
        "главы с наибольшим остатком:",
    ]
    worst = sorted(inventory["chapters"],
                   key=lambda c: -(c["counts"]["unsupported"] + c["counts"]["unnamed_speaker"]))
    for chapter in worst[:10]:
        left = chapter["counts"]["unsupported"] + chapter["counts"]["unnamed_speaker"]
        lines.append(f"  гл.{chapter['chapter_index']:>3} {chapter['title'][:34]:34} "
                     f"реплик {chapter['replicas']:>4}  под вопросом {left:>4}")
    speakers = {}
    for row in inventory["cross_checks"]["outside_appears_in"]:
        speakers[row["speaker"]] = speakers.get(row["speaker"], 0) + 1
    if speakers:
        lines += ["", "кто говорит вне своих глав:"]
        for name, count in sorted(speakers.items(), key=lambda kv: -kv[1])[:10]:
            lines.append(f"  {name[:24]:24} {count}")
    # Несколько доказательств целиком: сводка без цитат просит верить ей на слово,
    # а вся затея ровно в том, чтобы верить не приходилось.
    examples = inventory.get("proven") or []
    if examples:
        lines += ["", "как выглядит доказательство (первые пять):"]
        for row in examples[:5]:
            lines.append(f"  гл.{row['chapter_index']} абз.{row['paragraph']} "
                         f"{row['kind']} — {row['speaker']}")
            lines.append(f"      реплика: {row['text'][:70]}")
            lines.append(f"      цитата (абз.{row['evidence_paragraph']}): {row['quote'][:70]}")
    return "\n".join(lines)

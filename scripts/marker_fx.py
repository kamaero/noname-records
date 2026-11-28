#!/usr/bin/env python3
"""План и приём работают рядом с базой, рендер — на машине с установленным Lumina."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save(path, data):
    path = Path(path)
    with tempfile.NamedTemporaryFile(dir=path.parent, mode="w", encoding="utf-8", delete=False) as tmp:
        json.dump(data, tmp, ensure_ascii=False, indent=2)
    os.replace(tmp.name, path)


def command(args, script, options, codes=(0,)):
    run = subprocess.run([args.node, str(Path(args.skill).resolve() / "scripts" / script), *options],
                         cwd=args.skill, capture_output=True, timeout=180)
    if run.returncode not in codes:
        raise ValueError(f"{script}: exit {run.returncode}")
    return json.loads(run.stdout)


def choose(args, row):
    for query in row.get("queries") or [row["description"]]:
        if not query or query.startswith("-"):
            continue
        choice = command(args, "choose.mjs", [query, "--clean"], (0, 2))
        cues = choice.get("cues") or []
        if choice.get("decision") == "single-cue" and len(cues) == 1 and not choice.get("warnings"):
            cue = cues[0]
            if not any(cue.get(key) for key in ("editorial", "unmatchedWords", "review")):
                return cue["id"]
    return None


def render(args):
    plan = load(args.plan)
    root = Path(args.out).resolve()
    root.mkdir(parents=True, exist_ok=True)
    result = {"chapter": plan["chapter"], "effects": [], "unresolved": []}
    for row in plan["markers"]:
        marker = str(UUID(row["marker"]))
        recipe = row.get("recipe") or choose(args, row)
        if not recipe:
            result["unresolved"].append(marker)
            continue
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,99}", recipe):
            raise ValueError("invalid_recipe")
        with tempfile.TemporaryDirectory(dir=root) as scratch:
            render_id = "fx-" + recipe
            # Для ручного выбора допустим составной рецепт (например, обрушение крыши).
            isolation = [] if row.get("recipe") else ["--clean"]
            report = command(args, "remix.mjs", [recipe, "--id", render_id, "--format", "wav", *isolation, "--dest", scratch])
            path = Path(report["file"]).resolve()
            if (not path.is_relative_to(scratch) or not report.get("pass") or report.get("format") != "wav"
                    or report.get("id") != render_id or hashlib.sha256(path.read_bytes()).hexdigest() != report["sha256"]):
                raise ValueError("invalid_render")
            filename = report["sha256"] + ".wav"
            os.replace(path, root / filename)
        result["effects"].append({"marker": marker, "input_sha256": row["input_sha256"],
            "recipe": recipe, "generator": "Lumina SFX", "sha256": report["sha256"],
            "credits": report["credits"]})
    save(root / "effects.json", result)
    print(f"Rendered {len(result['effects'])}, unresolved {len(result['unresolved'])}")


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    modes = cli.add_subparsers(dest="mode", required=True)
    plan = modes.add_parser("plan")
    plan.add_argument("--chapter", required=True)
    plan.add_argument("--out", required=True)
    rend = modes.add_parser("render")
    rend.add_argument("--plan", required=True)
    rend.add_argument("--skill", required=True)
    rend.add_argument("--node", default="node")
    rend.add_argument("--out", required=True)
    ingest = modes.add_parser("import")
    ingest.add_argument("--chapter", required=True)
    ingest.add_argument("--manifest", required=True)
    ingest.add_argument("--apply", action="store_true")
    args = cli.parse_args()
    if args.mode == "render":
        return render(args)
    from sqlalchemy import text
    from app.db import SessionLocal
    from app.services import marker_effects
    if args.mode == "plan":
        with SessionLocal() as db:
            save(args.out, marker_effects.plan(db, args.chapter))
        return
    manifest = Path(args.manifest)
    result = load(manifest)
    if result["chapter"] != args.chapter:
        raise ValueError("wrong_chapter")
    for entry in result["effects"]:
        sha = entry["sha256"]
        if not re.fullmatch(r"[a-f0-9]{64}", sha):
            raise ValueError("invalid_sha256")
        data = (manifest.parent / (sha + ".wav")).read_bytes()
        with SessionLocal() as db:
            if args.apply:
                db.execute(text("BEGIN IMMEDIATE"))
            status = marker_effects.register(db, args.chapter, entry, data, apply=args.apply)
            if args.apply:
                db.commit()
            print(entry["marker"], entry["recipe"], status)


if __name__ == "__main__":
    main()

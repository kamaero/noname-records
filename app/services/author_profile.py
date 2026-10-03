from __future__ import annotations

import json
import re
import unicodedata

from app.models import Author, AuthorCharacter, AuthorPronunciation


def normalize_name(value: str) -> str:
    """Fold a name or term for matching across the profile.

    Combining marks are stripped: the book writes «Дгарни́н» with a stress mark and the
    profile holds «Дгарнин» without one, and they are the same character. Without this
    every stressed name in a book looks new to the profile — which is exactly what the
    221 unlinked rows of «Крылья полумрака» were.

    Only the acute and grave accents are dropped, NOT every combining mark: in NFD the
    letter «й» decomposes to «и» + breve and «ё» to «е» + diaeresis, and stripping those
    would turn «Тайн» into «таин». They are letters in Russian, not letters with a mark.
    """
    folded = unicodedata.normalize("NFD", (value or "").strip())
    folded = folded.replace("́", "").replace("̀", "")
    folded = unicodedata.normalize("NFC", folded)
    return re.sub(r"\s+", " ", folded).casefold()


def _load_list(raw: str) -> list[str]:
    try:
        data = json.loads(raw or "[]")
        return [str(x) for x in data] if isinstance(data, list) else []
    except (TypeError, ValueError):
        return []


def get_or_create_author(db, slug: str, name: str) -> Author:
    slug = (slug or "").strip().lower()
    author = db.query(Author).filter(Author.slug == slug).first()
    if author:
        return author
    author = Author(name=name or slug, slug=slug)
    db.add(author)
    db.flush()
    return author


def find_character(db, author_id: str, name: str) -> AuthorCharacter | None:
    key = normalize_name(name)
    if not key:
        return None
    rows = db.query(AuthorCharacter).filter(AuthorCharacter.author_id == author_id).all()
    for ch in rows:
        if normalize_name(ch.canonical_name) == key:
            return ch
    for ch in rows:
        if any(normalize_name(a) == key for a in _load_list(ch.aliases)):
            return ch
    return None


def upsert_character(db, author_id: str, *, canonical_name: str, aliases: list[str],
                     description: str = "", source_topic: str = "",
                     status: str = "unconfirmed") -> tuple[AuthorCharacter, bool]:
    existing = find_character(db, author_id, canonical_name)
    if existing is None:
        ch = AuthorCharacter(
            author_id=author_id, canonical_name=canonical_name.strip(),
            aliases=json.dumps(list(dict.fromkeys(aliases)), ensure_ascii=False),
            description=description or "", source_topic=source_topic or "", status=status,
        )
        db.add(ch)
        db.flush()
        return ch, True
    merged = list(dict.fromkeys(_load_list(existing.aliases) + list(aliases)))
    existing.aliases = json.dumps(merged, ensure_ascii=False)
    if description and not existing.description:
        existing.description = description
    if source_topic and not existing.source_topic:
        existing.source_topic = source_topic
    if status == "confirmed":
        existing.status = "confirmed"
    return existing, False


def set_actor(ch: AuthorCharacter, actor_name: str) -> str:
    actor_name = (actor_name or "").strip()
    if not actor_name:
        return "noop"
    if not ch.actor_name:
        ch.actor_name = actor_name
        return "set"
    return "noop" if ch.actor_name == actor_name else "conflict"


def set_color(ch: AuthorCharacter, color: str) -> str:
    color = (color or "").strip()
    if not color:
        return "noop"
    if not ch.reply_color:
        ch.reply_color = color
        return "set"
    return "noop" if ch.reply_color.lower() == color.lower() else "conflict"


def upsert_pronunciation(db, author_id: str, term: str, stressed: str,
                         variants: list[str], source: str = "compendium",
                         *, overwrite: bool = True) -> tuple[AuthorPronunciation, bool]:
    """Add a term to the author's list, or bring the existing row up to date.

    Returns (row, created). Until 2026-09 an existing term was returned untouched, so
    a correction saved from the editor never reached the profile: the first spelling
    of a stress won forever. Now a different `stressed` replaces the old one (and the
    variants and source follow), because the newest deliberate save is the truth.

    `overwrite=False` keeps the old behaviour for the one caller that must not let a
    book override the profile — `sync_book_to_author_profile`, whose rule is that the
    profile wins and the book only fills blanks.
    """
    key = normalize_name(term)
    rows = db.query(AuthorPronunciation).filter(AuthorPronunciation.author_id == author_id).all()
    for pr in rows:
        if normalize_name(pr.term) == key:
            if overwrite:
                _refresh_pronunciation(pr, stressed=stressed, variants=variants, source=source)
            return pr, False
    pr = AuthorPronunciation(
        author_id=author_id, term=term.strip(), stressed=stressed.strip(),
        variants=json.dumps(list(variants), ensure_ascii=False), source=source,
    )
    db.add(pr)
    db.flush()
    return pr, True


def _refresh_pronunciation(pr: AuthorPronunciation, *, stressed: str, variants: list[str], source: str) -> bool:
    """Update the row's stressed form / variants / source when they differ. Returns whether it did."""
    changed = False
    new_stressed = (stressed or "").strip()
    if new_stressed and new_stressed != (pr.stressed or "").strip():
        pr.stressed = new_stressed
        changed = True
    new_variants = json.dumps(list(variants or []), ensure_ascii=False)
    if variants and new_variants != (pr.variants or "[]"):
        pr.variants = new_variants
        changed = True
    if changed and source and source != pr.source:
        pr.source = source
    return changed


def _parse_pronunciation_notes(notes: str) -> list[tuple[str, str]]:
    """Parse the book's stress notes: one `слово=сло́во` per line.

    This is the format append_stress_term writes when the author saves a stress in the
    validation editor, forms and all («дгарнин», «дгарнина», «дгарнинам»...).
    """
    out: list[tuple[str, str]] = []
    for line in (notes or "").splitlines():
        term, sep, stressed = line.partition("=")
        term, stressed = term.strip(), stressed.strip()
        if sep and term and stressed:
            out.append((term, stressed))
    return out


def sync_book_to_author_profile(db, book) -> dict:
    """Lift a book's characters and stress marks up into the author's profile.

    The profile has only ever handed data DOWN — its roster seeds char_extraction —
    while everything the author settles inside a book stayed there. So the profile sat
    at 62 colours for 1741 characters, no pronunciations at all, and
    characters.author_character_id was empty on every row despite existing in the
    schema. The second book by the same author had nothing to inherit.

    Conflict rule: THE PROFILE WINS. A colour or actor already agreed there is the
    through-line across books and a single book may not overwrite it; the book only
    fills blanks. set_colour/set_actor already return "conflict" rather than writing,
    so this function reports those instead of resolving them — a real disagreement is
    for a human to look at, not for a sync to silently pick a side on.

    Idempotent: safe to run on every char-map approval and every stress save.
    """
    report = {
        "skipped": "",
        "characters_seen": 0,
        "characters_created": 0,
        "characters_linked": 0,
        "colours_set": 0,
        "actors_set": 0,
        "colour_conflicts": 0,
        "actor_conflicts": 0,
        "pronunciations_added": 0,
    }
    author_id = str(getattr(book, "author_id", "") or "")
    if not author_id:
        report["skipped"] = "no_author"
        return report

    from app.models import Character  # local import: avoids a cycle at module load

    characters = db.query(Character).filter(Character.book_id == book.id).all()
    for ch in characters:
        name = (ch.name or "").strip()
        if not name:
            continue
        report["characters_seen"] += 1
        target = find_character(db, author_id, name)
        if target is None:
            target, created = upsert_character(
                db, author_id, canonical_name=name, aliases=_load_list(ch.aliases),
                description=(ch.temperament or ""), source_topic=f"book:{book.id}",
            )
            if created:
                report["characters_created"] += 1

        if str(ch.author_character_id or "") != str(target.id):
            ch.author_character_id = str(target.id)
            report["characters_linked"] += 1

        outcome = set_color(target, ch.character_color or "")
        if outcome == "set":
            report["colours_set"] += 1
        elif outcome == "conflict":
            report["colour_conflicts"] += 1

        outcome = set_actor(target, ch.actor_name or "")
        if outcome == "set":
            report["actors_set"] += 1
        elif outcome == "conflict":
            report["actor_conflicts"] += 1

    for term, stressed in _parse_pronunciation_notes(getattr(book, "pronunciation_notes", "")):
        _, created = upsert_pronunciation(db, author_id, term, stressed, [], source="book", overwrite=False)
        if created:
            report["pronunciations_added"] += 1

    return report


def apply_author_profile_to_book(db, book, *, overwrite: bool = False) -> dict:
    """Apply the author's settled profile down onto a book. Mirror of the sync.

    Together the two close the loop: a book's decisions rise into the profile, and the
    author's next book starts with them already in place — the same colour for Дгарнин
    in every book, the same stress marks, the same actor.

    The direction rule is the opposite of the sync's and deliberately so. Here the
    profile is the source, so it fills the book — but only blanks. A colour already on
    a book's character was put there by somebody, and running this later must not undo
    their work. That asymmetry is what makes the pair safe to run repeatedly: each side
    only ever fills what the other left empty.

    `overwrite` says whether the book's existing values may be replaced. Default no,
    which protects real decisions. Pass True when the book's colours were generated
    rather than chosen — «Крылья полумрака» carries 221 palette colours nobody picked,
    and without this the through-line would never reach the author's first book here.
    The caller states which case it is; this function does not try to guess, because
    the palette is not reliably distinguishable after the fact.

    Idempotent.
    """
    report = {
        "skipped": "",
        "characters_seen": 0,
        "characters_linked": 0,
        "colours_applied": 0,
        "actors_applied": 0,
        "pronunciations_applied": 0,
    }
    author_id = str(getattr(book, "author_id", "") or "")
    if not author_id:
        report["skipped"] = "no_author"
        return report

    from app.models import Character  # local import: avoids a cycle at module load

    for ch in db.query(Character).filter(Character.book_id == book.id).all():
        name = (ch.name or "").strip()
        if not name:
            continue
        report["characters_seen"] += 1
        target = find_character(db, author_id, name)
        if target is None:
            continue

        if str(ch.author_character_id or "") != str(target.id):
            ch.author_character_id = str(target.id)
            report["characters_linked"] += 1

        colour = (target.reply_color or "").strip()
        if colour and (overwrite or not (ch.character_color or "").strip()) \
                and ch.character_color != colour:
            ch.character_color = colour
            # The text colour was derived from the old background; let it be
            # recomputed against the new one rather than left mismatched.
            ch.character_text_color = ""
            report["colours_applied"] += 1

        actor = (target.actor_name or "").strip()
        if actor and (overwrite or not (ch.actor_name or "").strip()) \
                and ch.actor_name != actor:
            ch.actor_name = actor
            report["actors_applied"] += 1

    if report["actors_applied"]:
        # Актёр, пришедший из профиля, — такое же назначение, как поставленное руками:
        # без пересборки «Мои роли» диктора молчат о новой книге, пока кто-нибудь не
        # тронет её каст (так было с СВ3 и СВ4 30.09).
        from app.services.casting import rebuild_assignments  # local import: avoids a cycle

        db.flush()
        rebuild_assignments(db, book.id)

    existing ={normalize_name(term) for term, _ in
                _parse_pronunciation_notes(getattr(book, "pronunciation_notes", ""))}
    added: list[str] = []
    for pr in db.query(AuthorPronunciation).filter(
            AuthorPronunciation.author_id == author_id).all():
        term = (pr.term or "").strip()
        stressed = (pr.stressed or "").strip()
        if not term or not stressed:
            continue
        if normalize_name(term) in existing:
            continue
        existing.add(normalize_name(term))
        added.append(f"{term}={stressed}")
    if added:
        current = (getattr(book, "pronunciation_notes", "") or "").rstrip("\n")
        book.pronunciation_notes = ("\n".join([current] + added) if current
                                    else "\n".join(added)) + "\n"
        report["pronunciations_applied"] = len(added)

    return report

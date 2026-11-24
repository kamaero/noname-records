"""Покрытие сценария принятыми дублями для студии и сводки ASR."""
from __future__ import annotations

from collections import Counter, defaultdict

from sqlalchemy import func

from app.models import AudioFile, Character, ScriptBook, ScriptChapter
from app.services.chapter_delivery import _chapter_label
from app.services.role_deadlines import appears_in_chapters, same_person
from app.services.role_identity import NARRATOR_REF, RoleIdentityError, book_roles
from app.v2.cast_ops import is_placeholder_name, parse_actor_name, role_chapter_map
from app.v2.models import V2Segment
from app.v2.reader import NARRATOR, _narrator_actor, effective_attributions

def _typed_chapters(db, book_id, identities):
    """Ссылка из разметки сильнее видимого имени, которое могло измениться."""
    rows = db.query(V2Segment.id, ScriptChapter.chapter_index).join(
        ScriptChapter, ScriptChapter.id == V2Segment.chapter_id
    ).filter(V2Segment.book_id == book_id).all()
    if not rows:
        return None

    marked = defaultdict(set)
    attrs = effective_attributions(db, [sid for sid, _ in rows])
    for sid, index in rows:
        for attr in attrs.get(sid, []):
            try:
                ref = getattr(attr, "role_ref", "") or identities.ref(attr.speaker)
            except RoleIdentityError:
                continue
            marked[ref].add(index)
    return marked


def book_recording_progress(db, book_id: str) -> dict | None:
    book = db.get(ScriptBook, book_id)
    if book is None:
        return None
    identities = book_roles(db, book_id)
    chapters = db.query(ScriptChapter).filter_by(book_id=book_id).all()
    labels = Counter(_chapter_label(chapter) for chapter in chapters)
    characters = [row for row in db.query(Character).filter_by(book_id=book_id).all()
                  if identities.kind(row.name) in {"character", "system_narrator"} and not is_placeholder_name(row.name)]
    narrator = next((row for row in characters if identities.is_narrator(row.name)), None)
    speakers = [row for row in characters if not identities.is_narrator(row.name)]
    marked = _typed_chapters(db, book_id, identities) if identities.typed else role_chapter_map(db, book_id)

    takes = defaultdict(list)
    files = db.query(AudioFile).filter(AudioFile.kind == "take", func.trim(AudioFile.chapter).in_(labels))
    for audio in files:
        # У публичной схемы дубль привязан по названию главы. Одно название
        # двух глав не позволяет доказать, к какой из них относится файл.
        if labels[audio.chapter.strip()] != 1 or not identities.owns_audio(audio):
            continue
        if identities.typed:
            key = audio.role_ref
        else:
            key = NARRATOR if identities.is_narrator(audio.role) else audio.role
        takes[key].append(audio)

    roles = []
    for character in [narrator, *speakers]:
        is_narrator = character is narrator
        role_id = str(character.id) if character is not None else ""
        name = NARRATOR if character is None else character.name
        raw_actor = str(character.actor_name or "") if character is not None else ""
        if is_narrator and not raw_actor.strip():
            raw_actor = _narrator_actor(db, book_id, characters)
        actor, tentative = parse_actor_name(raw_actor)
        if identities.typed:
            key = NARRATOR_REF if is_narrator else identities.character_ref(character)
            marked_key = key
        else:
            key = NARRATOR if is_narrator else name
            marked_key = role_id
        wanted = marked.get(marked_key, set()) if marked is not None else appears_in_chapters(character)
        required = [chapter for chapter in chapters if chapter.chapter_index in wanted]
        if not required:
            continue
        recorded = {
            audio.chapter.strip() for audio in takes[key]
            if actor and not tentative and same_person(audio.actor_name, actor)
        }
        missing = sorted(chapter.chapter_index for chapter in required if _chapter_label(chapter) not in recorded)
        roles.append({"character_id": role_id, "name": name, "actor_name": raw_actor,
                      "recorded_chapters": len(required) - len(missing), "total_chapters": len(required),
                      "missing_chapters": missing, "complete": not missing})
    character_rows = [row for row in roles if row["character_id"] and not identities.is_narrator(row["name"])]
    return {
        "roles": roles,
        "summary": {
            "chapters_total": len(chapters),
            "chapters_released": sum(chapter.released_at is not None for chapter in chapters),
            "narrator_recorded_chapters": next((row["recorded_chapters"] for row in roles
                                                if identities.is_narrator(row["name"])), 0),
            "characters_recorded": sum(row["complete"] for row in character_rows),
            "characters_total": len(character_rows),
        },
    }

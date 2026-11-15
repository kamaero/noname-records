"""Полная пересъёмка рассказчика исключает прежний звук, сохраняя историю."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime


def order_key(audio):
    return (audio.uploaded_at or datetime.min, str(getattr(audio, "correction_source_id", "") or audio.id))


def eligible_takes(db, chapter, files, *, identities=None, intents=None):
    from app.services.role_identity import chapter_roles
    identities = identities or chapter_roles(db, chapter.id)
    files = list(files)
    owned = [item for item in files if identities.owns_audio(item)]
    narrators = [item for item in owned if identities.is_narrator(item.role, getattr(item, "role_ref", ""))]
    full = [item for item in narrators if getattr(item, "correction_mode", "") == "full"]
    baseline = max(full, key=order_key, default=None)
    intent = max(full_intents(db, chapter, identities=identities, rows=intents), key=lambda row: (row.created_at, row.id), default=None)
    if intent is not None and (baseline is None or (intent.created_at, intent.id) > order_key(baseline)):
        return [item for item in files if item not in narrators]
    if baseline is None:
        return files
    allowed = {str(baseline.id)} | {str(item.id) for item in narrators
        if getattr(item, "correction_mode", "") == "patch" and order_key(item) > order_key(baseline)}
    return [item for item in files if item not in narrators or str(item.id) in allowed]


def chapter_takes(db, chapter):
    if chapter is None:
        return []
    from app.models import AudioFile
    from app.services.role_identity import chapter_roles
    identities = chapter_roles(db, chapter.id)
    label = str(chapter.chapter_title or "").strip() or f"Глава {chapter.chapter_index}"
    files = db.query(AudioFile).filter(AudioFile.kind == "take", AudioFile.chapter == label).all()
    files = [item for item in files if identities.owns_audio(item)]
    return sorted(eligible_takes(db, chapter, files), key=order_key)


def clean_alignment(payload, allowed_source_ids):
    if not any(line.get("source_audio_file_id") and str(line["source_audio_file_id"]) not in allowed_source_ids
               for line in payload.get("lines") or []):
        return payload
    result = deepcopy(payload)
    for line in result.get("lines") or []:
        source = str(line.get("source_audio_file_id") or "")
        if source and source not in allowed_source_ids:
            line.update(matched=False, takes=[], start=None, end=None, score=0)
            line.pop("source_audio_file_id", None)
    lines = result.get("lines") or []
    result["total"] = len(lines)
    result["missing"] = [line.get("index", index) for index, line in enumerate(lines) if not line.get("matched")]
    result["matched"] = sum(bool(line.get("matched")) for line in lines)
    result["coverage"] = result["matched"] / len(lines) if lines else 0.
    return result


def resolve_mode(db, *, book_code, role, chapter, filename, mode="", kind="take"):
    from app.services.audio_uploads import fix_number, extract_filename_chapter_index
    from app.services.role_identity import audio_role_identity, book_roles
    if mode not in ("", "full", "patch"):
        raise ValueError("invalid_correction_mode")
    book_id, ref = audio_role_identity(db, book_code, role)
    if not book_id:
        from app.models import ScriptBook
        from app.services.audio_uploads import book_code_matches_title
        books = [book for book in db.query(ScriptBook).all() if book_code_matches_title(book.title, book_code)]
        book_id = str(books[0].id) if len(books) == 1 else ""
    narrator = bool(book_id and book_roles(db, book_id).is_narrator(role, ref))
    if not narrator or kind != "take":
        if mode:
            raise ValueError("correction_mode_requires_narrator")
        return ""
    mode = mode or ("full" if fix_number(filename) is not None else "")
    if mode == "full":
        from app.models import ScriptChapter
        import re
        named = extract_filename_chapter_index(filename)
        # Диапазон или несколько глав не становятся одной главой по первому числу.
        chapter_tokens = re.findall(r"(?:^|[\s_\-.])(?:глава|chapter|ch|c(?=[№#]))[\s№#.\-_]*([0-9]{1,3})(?![0-9])", filename, flags=re.I)
        # Список «Ch04,05» и «Ch04_05» столь же неоднозначен, как диапазон.
        # Смотрим только сразу после номера главы: цифры книги и дубля допустимы.
        ranged = re.search(
            r"(?:^|[\s_\-.])(?:глава|chapter|ch|c(?=[№#]))[\s№#.\-_]*[0-9]{1,3}(?![0-9])"
            r"(?:[\s_]*[-–—+,;&]+[\s_]*|[\s_]+(?:(?:and|и)[\s_]+)?|[\s_]*(?:and|и)[\s_]*)[0-9]",
            filename, flags=re.I,
        )
        whole = re.search(r"(?:^|[\s_.-])(?:all|whole|entire|все|весь|целиком)(?:$|[\s_.-])", filename, flags=re.I)
        if not named or len(chapter_tokens) != 1 or ranged or whole:
            raise ValueError("full_correction_requires_named_chapter")
        chapters = db.query(ScriptChapter).filter(ScriptChapter.book_id == book_id).all()
        selected = [row for row in chapters if str(chapter).strip() in (
            {str(row.chapter_title).strip()} if str(row.chapter_title or "").strip() else
            {f"Глава {row.chapter_index}", f"Chapter {row.chapter_index}"})]
        if len(selected) != 1 or selected[0].chapter_index != named or selected[0].status != "published":
            raise ValueError("full_correction_requires_published_chapter")
    return mode


def allowed_sources(db, chapter, files, audio, *, identities=None):
    from app.services.role_identity import chapter_roles
    identities = identities or chapter_roles(db, chapter.id)
    if identities.is_narrator(audio.role, getattr(audio, "role_ref", "")) and any(
            getattr(item, "correction_mode", "") == "full" for item in files):
        files = [item for item in files if identities.is_narrator(item.role, getattr(item, "role_ref", ""))]
    return {str(item.id) for item in files}


def full_intents(db, chapter, *, identities=None, rows=None):
    from app.models import BotUpload, Character
    from app.services.audio_uploads import extract_filename_chapter_index
    from app.services.role_identity import chapter_roles
    identities = identities or chapter_roles(db, chapter.id)
    if rows is None:
        rows = db.query(BotUpload).filter(BotUpload.book_id == chapter.book_id,
            BotUpload.purpose == "take", BotUpload.correction_mode == "full", BotUpload.state != "cancelled").all()
    def narrator(row):
        if not identities.typed:
            return identities.is_narrator(row.role_name)
        character = identities.by_id.get(row.character_id)
        return character is not None and identities.is_narrator(character.name, identities.character_ref(character))
    return [row for row in rows if str(row.book_id) == str(chapter.book_id)
        and extract_filename_chapter_index(row.file_name) == chapter.chapter_index and narrator(row)]

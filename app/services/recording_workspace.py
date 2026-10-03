from __future__ import annotations

from app.models import AudioFile, Character, ScriptBook, ScriptChapter
from app.services.audio_uploads import TAKE, derive_book_code
from app.services.character_colors import build_character_style_maps
from app.services.recording import resolve_recording_my_roles
from app.time_utils import iso_utc


def build_recording_workspace_payload(
    db,
    *,
    book_id: str = "",
    chapter_id: str = "",
    user_id: str = "",
    user_name: str = "",
) -> dict:
    """Everything the recording screen needs to open on a role, in one answer.

    Without the cast, the actor's own roles, the line counts and the author's colours
    the screen has to guess: it built its role list out of files that had already been
    uploaded, which is empty for every actor on their first day.
    """
    context = build_recording_workspace_context(
        db,
        selected_book_id=book_id,
        selected_chapter_id=chapter_id,
        default_actor_name=user_name,
        user_id=user_id,
        user_name=user_name,
    )
    selected_chapter = context.get("selected_chapter")
    book = db.get(ScriptBook, selected_chapter.book_id) if selected_chapter else None
    line_counts = context.get("role_line_counts") or {}
    colors = context.get("cast_color_map") or {}
    text_colors = context.get("cast_text_color_map") or {}
    actors = context.get("cast_actor_map") or {}

    return {
        "ok": True,
        "selected_book": {
            "id": str(book.id),
            "title": str(book.title or ""),
            "display_title": str(book.display_title or book.title or ""),
            # Derived, not typed: the actor should never have to know this string, and
            # a wrong one files the audio where the DAW export will not find it.
            "code": derive_book_code(book.title or ""),
        } if book else None,
        "selected_chapter": {
            "id": str(selected_chapter.id),
            "chapter_index": int(selected_chapter.chapter_index or 0),
            "chapter_title": str(selected_chapter.chapter_title or ""),
        } if selected_chapter else None,
        "chapters": [
            {
                "id": str(chapter.id),
                "chapter_index": int(chapter.chapter_index or 0),
                "chapter_title": str(chapter.chapter_title or ""),
            }
            for chapter in context.get("chapters") or []
        ],
        "my_roles": context.get("my_roles") or [],
        "cast": [
            {
                "name": name,
                "lines": int(line_counts.get(name, 0)),
                "color": str(colors.get(name, "") or ""),
                "text_color": str(text_colors.get(name, "") or ""),
                "actor_name": str(actors.get(name, "") or ""),
            }
            for name in context.get("cast_names") or []
        ],
        "recent_files": _recent_files_view(context.get("recent_files_current_chapter") or []),
    }


def _select_recording_chapter(chapters: list[ScriptChapter], selected_chapter_id: str) -> ScriptChapter | None:
    selected_chapter = next((ch for ch in chapters if ch.id == selected_chapter_id), None) if selected_chapter_id else None
    if selected_chapter:
        return selected_chapter
    return chapters[0] if chapters else None


def _recent_files_for_selected_chapter(recent_files: list[AudioFile], selected_chapter: ScriptChapter | None) -> list[AudioFile]:
    if not selected_chapter:
        return []
    chapter_label = (selected_chapter.chapter_title or "").strip()
    # проба и трек эмбиента не принадлежат главе диктора; в главе без названия проба
    # иначе всплыла бы здесь, а эмбиент лежит в папке главы под её же именем
    return [
        item for item in recent_files
        if (item.chapter or "").strip() == chapter_label and str(getattr(item, "kind", TAKE) or TAKE) == TAKE
    ][:20]


def _recent_files_view(items: list[AudioFile]) -> list[dict[str, str]]:
    return [
        {
            # Без id кнопке «удалить» нечего было бы удалять — экран не может
            # адресовать запись без него. Первым ключом, чтобы это было видно сразу.
            "id": item.id,
            "role": item.role,
            "chapter": item.chapter,
            "canonical_filename": item.canonical_filename,
            "actor_name": item.actor_name,
            "uploaded_at": iso_utc(item.uploaded_at) or "",
            # Нужна экрану для подтверждения удаления — оно называет цену словами,
            # а не спрашивает голое «удалить файл?».
            "duration_seconds": item.duration_seconds,
        }
        for item in items
    ]


def _cast_actor_map(characters: list[Character]) -> dict[str, str]:
    return {
        (item.name or "").strip(): (item.actor_name or "").strip()
        for item in characters
        if (item.name or "").strip()
    }


def build_recording_workspace_context(
    db,
    *,
    selected_book_id: str = "",
    selected_chapter_id: str = "",
    default_actor_name: str = "",
    user_id: str = "",
    user_name: str = "",
    selected_role: str = "",
) -> dict:
    """Главы, каст с числом реплик и цветами, свои роли и свежие файлы — для экрана записи.

    Роли берутся из разметки v2 (`chapter_role_counts`). Раньше здесь же разбирался
    текст v1 и на каждый запрос считались очередь незаписанных ролей по всем главам,
    чеклист и заметки — ответ ручки ничего из этого не отдавал.
    """
    from app.v2.cast_ops import chapter_role_counts

    chapters_query = db.query(ScriptChapter).filter(ScriptChapter.status == "published")
    if str(selected_book_id or "").strip():
        chapters_query = chapters_query.filter(ScriptChapter.book_id == selected_book_id.strip())
    chapters = chapters_query.order_by(ScriptChapter.chapter_index.asc()).limit(500).all()
    selected_chapter = _select_recording_chapter(chapters, selected_chapter_id)
    # Только дубли: пробы и треки эмбиента главе диктора не принадлежат.
    recent_files = (db.query(AudioFile).filter(AudioFile.kind == TAKE)
                    .order_by(AudioFile.uploaded_at.desc()).limit(80).all())
    role_line_counts = chapter_role_counts(db, selected_chapter.id) if selected_chapter is not None else {}
    cast_names = sorted(role_line_counts, key=lambda name: (-role_line_counts[name], name.lower()))
    my_roles = resolve_recording_my_roles(
        db,
        selected_chapter.book_id if selected_chapter else "",
        user_name or default_actor_name,
        role_line_counts,
    )
    characters = db.query(Character).filter(Character.book_id == selected_chapter.book_id).all() if selected_chapter else []
    cast_color_map, cast_text_color_map, _weights, _styles = build_character_style_maps(characters, cast_names)
    return {
        "chapters": chapters,
        "selected_chapter": selected_chapter,
        "cast_names": cast_names,
        "cast_actor_map": _cast_actor_map(characters),
        "cast_color_map": cast_color_map,
        "cast_text_color_map": cast_text_color_map,
        "role_line_counts": role_line_counts,
        "my_roles": my_roles,
        "recent_files": recent_files,
        "recent_files_current_chapter": _recent_files_for_selected_chapter(recent_files, selected_chapter),
    }

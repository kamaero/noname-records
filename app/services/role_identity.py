"""Явные роли новых книг: имя персонажа не превращает его в рассказчика.

Старые книги сохраняют историческое сворачивание narrator/Автор. Режим хранится
отдельно от языка: смена языка не должна задним числом менять смысл аудиофайлов.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property

from app.services.author_profile import normalize_name
from app.services.shared_runtime import NARRATOR_DISPLAY_NAME, is_narrator_name

LEGACY = "legacy"
TYPED = "typed_v1"
NARRATOR_REF = "narrator:system"
CHARACTER_PREFIX = "character:"


class RoleIdentityError(ValueError):
    def __init__(self, code: str = "ambiguous_role_identity"):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class BookRoles:
    typed: bool
    characters: tuple
    _book: object | None = None
    _legacy_code: str = ""
    _legacy_code_unique: bool = False

    def is_narrator(self, name: str, role_ref: str = "") -> bool:
        if not self.typed:
            return is_narrator_name(name)
        if role_ref:
            return role_ref == NARRATOR_REF
        return str(name or "").strip() == NARRATOR_DISPLAY_NAME

    @cached_property
    def by_id(self) -> dict:
        return {str(row.id): row for row in self.characters}

    @cached_property
    def labels(self) -> dict[str, set[str]]:
        from app.v2.cast_ops import split_aliases
        labels: dict[str, set[str]] = {}
        for row in self.characters:
            if self.is_narrator(row.name):
                continue
            for name in [row.name, *split_aliases(row.aliases)]:
                key = normalize_name(name)
                if key:
                    labels.setdefault(key, set()).add(str(row.id))
        return labels

    def ref(self, name: str) -> str:
        if not self.typed:
            return ""
        if self.is_narrator(name):
            return NARRATOR_REF
        ids = self.labels.get(normalize_name(name), set())
        if len(ids) > 1:
            raise RoleIdentityError()
        return CHARACTER_PREFIX + next(iter(ids)) if ids else ""

    def known_ref(self, ref: str) -> bool:
        return ref == NARRATOR_REF or (ref.startswith(CHARACTER_PREFIX) and ref[len(CHARACTER_PREFIX):] in self.by_id)

    def name(self, role_ref: str, fallback: str) -> str:
        if not self.typed or not role_ref:
            return str(fallback or "").strip()
        if role_ref == NARRATOR_REF:
            return NARRATOR_DISPLAY_NAME
        if role_ref.startswith(CHARACTER_PREFIX):
            wanted = role_ref[len(CHARACTER_PREFIX):]
            row = self.by_id.get(wanted)
            if row is not None:
                return str(row.name or "").strip()
        raise RoleIdentityError("unknown_role_identity")

    def same(self, left: str, right: str, *, left_ref: str = "", right_ref: str = "") -> bool:
        if self.typed:
            a, b = left_ref or self.ref(left), right_ref or self.ref(right)
            if a or b:
                return bool(a and b and a == b)
        if self.is_narrator(left) or self.is_narrator(right):
            return self.is_narrator(left) and self.is_narrator(right)
        return normalize_name(left) == normalize_name(right)

    def owns_audio(self, audio) -> bool:
        if self._book is None:
            return False
        owner = str(getattr(audio, "role_book_id", "") or "").strip()
        if owner:
            return owner == str(self._book.id)
        if self.typed or not self._legacy_code_unique:
            return False
        # Старый код годится только при единственной книге-владельце. Явный ID
        # выше всегда сильнее подписи: переименование книги не теряет её записи.
        from app.services.audio_naming import book_token
        return bool(self._legacy_code and book_token(str(getattr(audio, "book_code", "") or "")) == self._legacy_code)

    def character_ref(self, character) -> str:
        if not self.typed:
            return ""
        if self._book is None or str(character.book_id) != str(self._book.id):
            raise RoleIdentityError("unknown_role_identity")
        ref = NARRATOR_REF if self.is_narrator(character.name) else CHARACTER_PREFIX + str(character.id)
        if not self.known_ref(ref):
            raise RoleIdentityError("unknown_role_identity")
        return ref

    def matches_character_audio(self, audio, character) -> bool:
        if not self.owns_audio(audio):
            return False
        if self.typed:
            # ID переживает переименование и совпадение видимых имён. Без ref
            # нельзя приписывать запись персонажу только по строковой подписи.
            return str(getattr(audio, "role_ref", "") or "") == self.character_ref(character)
        return str(audio.role or "") == str(character.name or "")

    def kind(self, name: str, role_ref: str = "") -> str:
        if not str(name or "").strip() or str(name).upper().startswith("UNSURE"):
            return "unassigned"
        return "system_narrator" if self.is_narrator(name, role_ref) else "character"


def book_roles(db, book_id: str) -> BookRoles:
    from app.models import Character, ScriptBook
    books = db.query(ScriptBook).all()
    book = next((item for item in books if str(item.id) == str(book_id or "")), None)
    typed = getattr(book, "role_identity_mode", LEGACY) == TYPED
    characters = tuple(db.query(Character).filter(Character.book_id == str(book_id or "")).all()) if typed else ()
    from app.services.audio_naming import book_token
    from app.services.audio_uploads import derive_book_code
    code = book_token(derive_book_code(str(getattr(book, "title", "") or ""))) if book is not None else ""
    unique = False
    if not typed and code:
        unique = sum(book_token(derive_book_code(str(title or ""))) == code
                     for title in (item.title for item in books)) == 1
    return BookRoles(typed, characters, book, code, unique)


def chapter_roles(db, chapter_id: str) -> BookRoles:
    from app.models import ScriptChapter
    chapter = db.get(ScriptChapter, str(chapter_id or ""))
    return book_roles(db, chapter.book_id) if chapter is not None else BookRoles(False, ())


def audio_role_identity(db, book_code: str, role: str) -> tuple[str, str]:
    from app.models import ScriptBook
    from app.services.audio_uploads import book_code_matches_title
    books = [book for book in db.query(ScriptBook.id, ScriptBook.title, ScriptBook.role_identity_mode).all()
             if book_code_matches_title(book.title, book_code)]
    typed = [book for book in books if getattr(book, "role_identity_mode", LEGACY) == TYPED]
    if not typed:
        return "", ""
    if len(books) != 1:
        raise RoleIdentityError("ambiguous_book_code")
    ref = book_roles(db, typed[0].id).ref(role)
    if not ref:
        raise RoleIdentityError("unknown_role_identity")
    return str(typed[0].id), ref


def audio_role_ref(db, book_code: str, role: str) -> str:
    return audio_role_identity(db, book_code, role)[1]


def legacy_language_conflicts(db, book) -> list[str]:
    """Перед сменой профиля не угадываем смысл старых английских алиасов."""
    if getattr(book, "role_identity_mode", LEGACY) == TYPED:
        return []
    from app.models import AudioFile, Character
    from app.services.audio_uploads import book_code_matches_title
    from app.v2.models import V2Attribution, V2Segment
    from app.v2.cast_ops import split_aliases
    characters = db.query(Character).filter(Character.book_id == book.id).all()
    names = {str(row.name or "") for row in characters}
    names.update(alias for row in characters for alias in split_aliases(row.aliases))
    names.update(str(row[0] or "") for row in db.query(V2Attribution.speaker).join(
        V2Segment, V2Segment.id == V2Attribution.segment_id).filter(V2Segment.book_id == book.id).distinct())
    names.update(str(role or "") for role, code in db.query(AudioFile.role, AudioFile.book_code).distinct()
                 if book_code_matches_title(book.title, code))
    return sorted(name for name in names if normalize_name(name) == "narrator")


def job_role(db, job) -> str:
    from app.models import AudioFile
    identities = chapter_roles(db, job.chapter_id)
    ref = str(getattr(job, "role_ref", "") or "")
    if identities.typed and not ref and getattr(job, "audio_file_id", ""):
        audio = db.get(AudioFile, job.audio_file_id)
        ref = str(getattr(audio, "role_ref", "") or "")
    return identities.name(ref, job.expected_role)

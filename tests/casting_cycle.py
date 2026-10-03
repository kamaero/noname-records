"""Цикл одного автора из трёх книг, в каждой — Куйбу Дегатти, связанный с профилем."""
import uuid

from app.models import AudioFile, Author, AuthorCharacter, Character, ScriptBook
from app.services.audio_uploads import derive_book_code
from app.time_utils import utcnow_naive

TITLES = ("Сказки волшебников 1", "Сказки волшебников 2", "Крылья Полумрака")


def build_cycle(db, *, author_id="auth-1", other_author_book=False):
    author = Author(id=author_id, slug="belozerov", name="Белозёров")
    db.add(author)
    profile = AuthorCharacter(id="ac-kuybu", author_id=author_id, canonical_name="Куйбу Дегатти",
                              aliases="[]", status="confirmed")
    db.add(profile)
    books, kuybu = [], []
    for index, title in enumerate(TITLES):
        book = ScriptBook(id=f"b{index + 1}", title=title, source_filename="x.txt", source_format="txt",
                          author_id=author_id, created_at=utcnow_naive())
        db.add(book)
        character = Character(id=f"c{index + 1}", book_id=book.id, name="Куйбу Дегатти",
                              author_character_id=profile.id)
        db.add(character)
        books.append(book)
        kuybu.append(character)
    if other_author_book:
        # Чужая книга с тем же author_character_id — переноситься туда нельзя.
        stranger = ScriptBook(id="b-other", title="Чужая книга", source_filename="y.txt", source_format="txt",
                              author_id="auth-2", created_at=utcnow_naive())
        db.add(stranger)
        db.add(Character(id="c-other", book_id="b-other", name="Куйбу Дегатти", author_character_id=profile.id))
    db.commit()
    return {"author": author, "profile": profile, "books": books, "kuybu": kuybu}


def record_take(db, book, role, actor):
    """Дубль роли в книге — после него роль считается записанной."""
    db.add(AudioFile(id=str(uuid.uuid4()), book_code=derive_book_code(book.title), chapter="Глава 1",
                     role=role, actor_name=actor, kind="take", original_filename="a.wav",
                     canonical_filename="a.wav", stored_key=f"k/{uuid.uuid4()}.wav",
                     mime_type="audio/wav", size_bytes=1))
    db.commit()

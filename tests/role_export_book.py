"""Книга для тестов сценария роли: одна глава, где есть всё, что встречается в жизни.

Реплика с авторской ремаркой внутри, две реплики подряд, ответ партнёру, редкое слово
с ударением, частое с ударением словаря и частое с ударением человека. Имена выдуманы.
"""
import uuid

from app.models import Character, ScriptBook, ScriptChapter
from app.v2.models import V2Attribution, V2Segment, V2StressMark

BOOK = "book-rx"
CHAPTER = "ch-3"
TEXTS = [
    "И тогда дверь открылась.",
    "— Я не знала, — сказала она, — что ты здесь.",
    "— Прости, карлица задержала.",
    "— Ты опоздала.",
    "— Знаю.",
]


def _say(db, ordinal: int, speaker: str, start: int = 0, end: int | None = None) -> None:
    text = TEXTS[ordinal]
    db.add(V2Attribution(id=str(uuid.uuid4()), segment_id=f"{CHAPTER}:{ordinal:05d}",
                         span_start=start, span_end=len(text) if end is None else end,
                         speaker=speaker, confidence=0.9, source="llm", version=1))


def _stress(db, ordinal: int, word: str, vowel: int, source: str) -> None:
    start = TEXTS[ordinal].index(word)
    db.add(V2StressMark(id=str(uuid.uuid4()), segment_id=f"{CHAPTER}:{ordinal:05d}",
                        word_start=start, word_end=start + len(word), vowel_offset=vowel,
                        source=source, version=1))


def build_book(db, *, status: str = "published") -> None:
    db.add(ScriptBook(id=BOOK, title="Тихая гавань", display_title="Тихая гавань",
                      source_filename="t.txt", source_format="txt"))
    db.add(Character(id="c-mira", book_id=BOOK, name="Мира", actor_name="Анна Тестова",
                     race="человек", temperament="вспыльчивая", operator_note="говорит быстро"))
    db.add(Character(id="c-yarosh", book_id=BOOK, name="Ярош", actor_name="Пётр Пробный?"))
    db.add(Character(id="c-narr", book_id=BOOK, name="Рассказчик"))
    db.add(ScriptChapter(id=CHAPTER, book_id=BOOK, chapter_index=3, chapter_title="Глава 3. Тайна",
                         status=status))
    for ordinal, text in enumerate(TEXTS):
        db.add(V2Segment(id=f"{CHAPTER}:{ordinal:05d}", book_id=BOOK, chapter_id=CHAPTER,
                         ordinal=ordinal, kind="paragraph", text=text, char_start=0, char_end=len(text)))
    remark = TEXTS[1].index("— сказала")
    rest = TEXTS[1].index("— что")
    _say(db, 0, "Рассказчик")
    _say(db, 1, "Мира", 0, remark)
    _say(db, 1, "Рассказчик", remark, rest)
    _say(db, 1, "Мира", rest)
    _say(db, 2, "Мира")
    _say(db, 3, "Ярош")
    _say(db, 4, "Мира")
    _stress(db, 2, "карлица", 4, "dict")      # редкое — стоит
    _stress(db, 2, "задержала", 6, "dict")    # частое от словаря — не стоит
    _stress(db, 2, "Прости", 5, "operator")   # частое, но поставил человек — стоит
    db.commit()

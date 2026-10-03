"""Две роли одного актёра, которые разговаривают друг с другом.

На живой книге таких пар семь, и худшая — в одном абзаце: реплику говорит
Инвнехлизад, ремарку в том же абзаце Рассказчик, и оба назначены на одного
актёра. Держать это в голове на двухстах ролях нельзя, а слышно в готовом
спектакле.
"""
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Character, ScriptBook, ScriptChapter
from app.v2.models import V2Attribution, V2Segment
from app.v2.role_intersections import role_intersections

BOOK = "book-x"


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with sessionmaker(bind=engine)() as session:
        session.add(ScriptBook(id=BOOK, title="Кн", source_filename="k.txt", source_format="txt"))
        for index in (1, 2):
            session.add(ScriptChapter(id=f"ch-{index}", book_id=BOOK, chapter_index=index,
                                      chapter_title=f"Глава {index}", status="published"))
        yield session


def _role(db, name: str, actor: str, race: str = "") -> None:
    db.add(Character(id=str(uuid.uuid4()), book_id=BOOK, name=name, actor_name=actor, race=race))


def _say(db, chapter: str, ordinal: int, speaker: str) -> None:
    text = f"Реплика {ordinal}."
    seg = f"{chapter}:{ordinal:05d}"
    if db.get(V2Segment, seg) is None:
        db.add(V2Segment(id=seg, book_id=BOOK, chapter_id=chapter, ordinal=ordinal,
                         kind="paragraph", text=text, char_start=0, char_end=len(text)))
        db.flush()
    db.add(V2Attribution(id=str(uuid.uuid4()), segment_id=seg, span_start=0, span_end=len(text),
                         speaker=speaker, confidence=0.9, source="llm", version=1))


def test_two_roles_of_one_actor_in_the_same_paragraph_are_a_dialogue(db):
    """Ноль абзацев — это один и тот же абзац: актёр отвечает сам себе внутри
    одной фразы."""
    _role(db, "Гамук", "Гончаров Иван", race="фархеррим")
    _role(db, "Тупуг", "Гончаров Иван", race="фархеррим (высший демон)")
    _say(db, "ch-1", 0, "Гамук")
    _say(db, "ch-1", 0, "Тупуг")
    db.commit()

    found = role_intersections(db, BOOK)

    assert len(found) == 1
    assert found[0]["distance"] == 0
    assert found[0]["severity"] == "dialogue"
    assert found[0]["chapter"] == 1


def test_the_same_race_is_the_worst_and_is_recognised_through_the_qualifier(db):
    """Худшее, что бывает: диалог двух ролей ОДНОЙ расы одним актёром — два
    демона или два человека звучат похоже. Раса записана свободным текстом с
    уточнением в скобках, и «фархеррим» и «фархеррим (высший демон)» — одна раса."""
    _role(db, "Гамук", "Гончаров Иван", race="фархеррим")
    _role(db, "Тупуг", "Гончаров Иван", race="фархеррим (высший демон)")
    _say(db, "ch-1", 0, "Гамук")
    _say(db, "ch-1", 1, "Тупуг")
    db.commit()

    assert role_intersections(db, BOOK)[0]["same_race"] is True


def test_a_feminine_form_is_the_same_race(db):
    """«Демон» и «демоница», «фархеррим» и «фархерримка» — одна раса. Ошибаться
    надо в сторону предупреждения: лишний флажок стоит одного клика «так задумано»,
    пропущенный слышен в готовом спектакле."""
    _role(db, "Он", "Актёр", race="демон")
    _role(db, "Она", "Актёр", race="демоница")
    _say(db, "ch-1", 0, "Он")
    _say(db, "ch-1", 1, "Она")
    db.commit()

    assert role_intersections(db, BOOK)[0]["same_race"] is True


def test_different_races_are_not_the_worst_case(db):
    """Разные расы разводятся обработкой голоса — pitch shifter, chorus, reverb.
    Предупреждение остаётся, но это уже не худший случай."""
    _role(db, "Сатухух", "Сомов Роман", race="фархеррим")
    _role(db, "Куйбу Дегатти", "Сомов Роман", race="человек (воин)")
    _say(db, "ch-1", 0, "Сатухух")
    _say(db, "ch-1", 1, "Куйбу Дегатти")
    db.commit()

    found = role_intersections(db, BOOK)[0]
    assert found["severity"] == "dialogue"
    assert found["same_race"] is False


def test_a_role_without_a_race_never_counts_as_the_same(db):
    """Пустая раса — это «не знаем», а не «совпало». Раса не заполнена у многих
    ролей, и приписывать им совпадение значило бы врать."""
    _role(db, "Гамук", "Остапович Евгений", race="")
    _role(db, "Инвнехлизад", "Остапович Евгений", race="хилакток")
    _say(db, "ch-1", 0, "Гамук")
    _say(db, "ch-1", 0, "Инвнехлизад")
    db.commit()

    assert role_intersections(db, BOOK)[0]["same_race"] is False


def test_roles_far_apart_in_one_chapter_are_only_a_chapter_overlap(db):
    """Одна глава, но врозь — владелец назвал это «с натяжкой ок»."""
    _role(db, "Кимиуб", "Румянцев Константин")
    _role(db, "Снилендукахл", "Румянцев Константин")
    _say(db, "ch-1", 0, "Кимиуб")
    _say(db, "ch-1", 40, "Снилендукахл")
    db.commit()

    assert role_intersections(db, BOOK)[0]["severity"] == "chapter"


def test_roles_in_different_chapters_do_not_intersect(db):
    """Роли, разнесённые по книге, — не проблема, и показывать их нельзя:
    иначе список предупреждений станет шумом и его перестанут читать."""
    _role(db, "Дасда", "Филатов Павел")
    _role(db, "Пазузу", "Филатов Павел")
    _say(db, "ch-1", 0, "Дасда")
    _say(db, "ch-2", 0, "Пазузу")
    db.commit()

    assert role_intersections(db, BOOK) == []


def test_roles_of_different_actors_never_intersect(db):
    """Пересечение — свойство актёра, а не ролей: два персонажа в одном абзаце
    это обычный диалог, ради которого спектакль и делается."""
    _role(db, "Гамук", "Гончаров Иван")
    _role(db, "Бдеукс", "Зотов Сергей")
    _say(db, "ch-1", 0, "Гамук")
    _say(db, "ch-1", 0, "Бдеукс")
    db.commit()

    assert role_intersections(db, BOOK) == []


def test_an_unassigned_role_cannot_intersect(db):
    """Свободная роль ещё ничья: предупреждать не о чем."""
    _role(db, "Гамук", "")
    _role(db, "Тупуг", "")
    _say(db, "ch-1", 0, "Гамук")
    _say(db, "ch-1", 0, "Тупуг")
    db.commit()

    assert role_intersections(db, BOOK) == []


def test_the_closest_meeting_wins_and_every_shared_chapter_is_listed(db):
    """Степень задаёт самая близкая встреча — по ней принимают решение.
    Остальные главы нужны, чтобы человек понимал масштаб, а не один эпизод."""
    _role(db, "Гамук", "Гончаров Иван")
    _role(db, "Тупуг", "Гончаров Иван")
    _say(db, "ch-1", 0, "Гамук")
    _say(db, "ch-1", 30, "Тупуг")
    _say(db, "ch-2", 5, "Гамук")
    _say(db, "ch-2", 6, "Тупуг")
    db.commit()

    found = role_intersections(db, BOOK)[0]

    assert found["distance"] == 1 and found["chapter"] == 2
    assert found["chapters"] == [1, 2]


def test_talking_to_each_other_outranks_sharing_a_race(db):
    """Порядок задаёт близость, а раса — вес внутри неё.

    Страшен прежде всего диалог: актёр отвечает сам себе, и это слышно. Одна
    раса делает диалог хуже, но сама по себе, у ролей в разных концах главы, не
    превращает «с натяжкой ок» в срочное. Сортировать расой поверх расстояния
    значит поднять наверх то, что режиссёр разбирать не станет, и утопить то,
    ради чего он открыл экран."""
    _role(db, "Далёкий А", "Актёр Раса", race="барон")
    _role(db, "Далёкий Б", "Актёр Раса", race="барон")
    _say(db, "ch-1", 0, "Далёкий А")
    _say(db, "ch-1", 40, "Далёкий Б")

    _role(db, "Близкий А", "Актёр Диалог", race="человек")
    _role(db, "Близкий Б", "Актёр Диалог", race="демон")
    _say(db, "ch-2", 0, "Близкий А")
    _say(db, "ch-2", 1, "Близкий Б")
    db.commit()

    found = role_intersections(db, BOOK)

    assert found[0]["severity"] == "dialogue", "диалог обязан быть выше общей расы"
    assert found[0]["same_race"] is False
    assert found[1]["same_race"] is True and found[1]["severity"] == "chapter"


def test_within_one_severity_the_same_race_comes_first(db):
    """А вот внутри диалога раса решает: два демона звучат похоже, и разбирать
    такую пару надо раньше, чем демона с человеком."""
    _role(db, "Похожий А", "Актёр Похожие", race="демон")
    _role(db, "Похожий Б", "Актёр Похожие", race="демоница")
    _say(db, "ch-1", 0, "Похожий А")
    _say(db, "ch-1", 1, "Похожий Б")

    _role(db, "Разный А", "Актёр Разные", race="человек")
    _role(db, "Разный Б", "Актёр Разные", race="дракон")
    _say(db, "ch-2", 0, "Разный А")
    _say(db, "ch-2", 1, "Разный Б")
    db.commit()

    found = role_intersections(db, BOOK)

    assert found[0]["same_race"] is True
    assert found[1]["same_race"] is False


def test_an_acknowledged_pair_stops_warning_but_stays_visible(db):
    """Спросить один раз. Спрятать совсем нельзя: через полгода причина важнее
    самого решения, и её должно быть где посмотреть."""
    from app.v2.role_intersections import acknowledge_pair

    _role(db, "Гамук", "Гончаров Иван")
    _role(db, "Тупуг", "Гончаров Иван")
    _say(db, "ch-1", 0, "Гамук")
    _say(db, "ch-1", 1, "Тупуг")
    db.commit()

    acknowledge_pair(db, book_id=BOOK, role_a="Тупуг", role_b="Гамук",
                     reason="близнецы, голоса одинаковые намеренно",
                     actor_uid="u1", actor_name="Режиссёр")
    db.commit()

    found = role_intersections(db, BOOK)[0]
    assert found["acknowledged"] is True
    assert "близнецы" in found["reason"]
    assert found["severity"] == "dialogue", "степень не меняется: решение не отменяет арифметику"


def test_the_pair_is_the_same_whichever_way_round_it_is_named(db):
    """Пара ненаправленная. Иначе одно решение записалось бы дважды, и второй
    раз человека спросили бы о том, что он уже решил."""
    from app.v2.role_intersections import acknowledge_pair

    _role(db, "Гамук", "Гончаров Иван")
    _role(db, "Тупуг", "Гончаров Иван")
    _say(db, "ch-1", 0, "Гамук")
    _say(db, "ch-1", 1, "Тупуг")
    db.commit()
    acknowledge_pair(db, book_id=BOOK, role_a="Гамук", role_b="Тупуг", reason="близнецы",
                     actor_uid="u1", actor_name="Р")
    db.commit()

    acknowledge_pair(db, book_id=BOOK, role_a="Тупуг", role_b="Гамук", reason="они же",
                     actor_uid="u1", actor_name="Р")
    db.commit()

    from app.models import RolePairNote
    assert db.query(RolePairNote).filter(RolePairNote.book_id == BOOK).count() == 1


def test_forgetting_a_pair_brings_the_warning_back(db):
    from app.v2.role_intersections import acknowledge_pair, forget_pair

    _role(db, "Гамук", "Гончаров Иван")
    _role(db, "Тупуг", "Гончаров Иван")
    _say(db, "ch-1", 0, "Гамук")
    _say(db, "ch-1", 1, "Тупуг")
    db.commit()
    acknowledge_pair(db, book_id=BOOK, role_a="Гамук", role_b="Тупуг", reason="близнецы",
                     actor_uid="u1", actor_name="Р")
    db.commit()

    forget_pair(db, book_id=BOOK, role_a="Гамук", role_b="Тупуг")
    db.commit()

    assert role_intersections(db, BOOK)[0]["acknowledged"] is False


def test_an_acknowledged_pair_says_who_decided_and_when(db):
    """Через полгода причина важнее решения, а «кто решил» важнее причины: карту
    правят двое — автор и режиссёр, — и у них разные основания."""
    from app.v2.role_intersections import acknowledge_pair

    _role(db, "Гамук", "Гончаров Иван", race="фархеррим")
    _role(db, "Тупуг", "Гончаров Иван", race="фархеррим")
    _say(db, "ch-1", 0, "Гамук")
    _say(db, "ch-1", 1, "Тупуг")
    db.commit()
    acknowledge_pair(db, book_id=BOOK, role_a="Гамук", role_b="Тупуг", reason="близнецы",
                     actor_uid="u1", actor_name="Режиссёр")
    db.commit()

    found = role_intersections(db, BOOK)[0]

    assert found["acknowledged_by"] == "Режиссёр"
    assert found["acknowledged_at"]


def test_a_tentative_assignment_is_still_found(db):
    """Хвостовой «?» — нотация владельца для предварительного назначения, а не
    другой актёр. «Гамук — Иван?» и «Тупуг — Иван» обязаны схлопнуться в одного
    актёра для пересечений — иначе пара с предварительным назначением тихо
    исчезает из списка."""
    _role(db, "Гамук", "Гончаров Иван?")
    _role(db, "Тупуг", "Гончаров Иван")
    _say(db, "ch-1", 0, "Гамук")
    _say(db, "ch-1", 1, "Тупуг")
    db.commit()

    found = role_intersections(db, BOOK)

    assert len(found) == 1
    assert found[0]["actor"] == "Гончаров Иван", "хвостовой «?» — не часть имени актёра"
    assert {found[0]["role_a"], found[0]["role_b"]} == {"Гамук", "Тупуг"}


def test_question_marks_alone_leave_the_role_free(db):
    """«???» без имени — живая пометка «ещё не решил», а не актёр по имени
    «???». Такая роль свободна, и подставлять её в пересечения нельзя: иначе
    любые две ничьи роли с такой пометкой стали бы фантомной парой."""
    _role(db, "Бапдиг", "???")
    _role(db, "Гамук", "???")
    _say(db, "ch-1", 0, "Бапдиг")
    _say(db, "ch-1", 1, "Гамук")
    db.commit()

    assert role_intersections(db, BOOK) == []


def test_a_speaker_spelled_in_different_case_still_folds_into_the_role(db):
    """Свёртка написания у пересечений обязана быть той же, что строит таблицу
    карты: спикер в разметке, отличающийся от карты только регистром, — та же
    роль, а не невидимый для пересечений двойник."""
    _role(db, "Гамук", "Гончаров Иван")
    _role(db, "Тупуг", "Гончаров Иван")
    _say(db, "ch-1", 0, "ГАМУК")
    _say(db, "ch-1", 1, "Тупуг")
    db.commit()

    found = role_intersections(db, BOOK)

    assert len(found) == 1
    assert {found[0]["role_a"], found[0]["role_b"]} == {"Гамук", "Тупуг"}, \
        "спикер сложился в написание из карты, а не остался отдельной строкой"


def test_a_pair_reports_the_chapters_each_role_actually_speaks_in(db):
    """«Заявлено 46 глав, размечено 45» не говорит, КАКУЮ главу искать. Режиссёру
    нужен номер: расхождение — сигнал о тексте, и по нему идут смотреть."""
    _role(db, "Гамук", "Гончаров Иван")
    _role(db, "Тупуг", "Гончаров Иван")
    _say(db, "ch-1", 0, "Гамук")
    _say(db, "ch-2", 0, "Гамук")
    _say(db, "ch-2", 1, "Тупуг")
    db.commit()

    found = role_intersections(db, BOOK)[0]

    assert found["chapters"] == [2], "общая глава у пары только вторая"


def test_the_narrator_never_makes_a_pair(db):
    """Повествование и речь — разные регистры: один актёр разводит их голосом.
    Рассказчик присутствует в каждой главе, и держать его в расчёте значит
    заваливать режиссёра парами, каждую из которых он всё равно отменит."""
    db.add(Character(id="c-nar", book_id=BOOK, name="Рассказчик", actor_name="Евгений"))
    db.add(Character(id="c-yan", book_id=BOOK, name="Инвнехлизад", actor_name="Евгений"))
    db.add(Character(id="c-tak", book_id=BOOK, name="Гамук", actor_name="Евгений"))
    _say(db, "ch-1", 0, "Рассказчик")
    _say(db, "ch-1", 1, "Инвнехлизад")
    _say(db, "ch-1", 2, "Гамук")
    db.commit()

    pairs = role_intersections(db, BOOK)

    # пара двух обычных ролей одного актёра осталась
    assert {(row["role_a"], row["role_b"]) for row in pairs} == {("Гамук", "Инвнехлизад")}
    # и никакого «признано по умолчанию» больше нет
    assert "acknowledged_default" not in pairs[0]

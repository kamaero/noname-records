"""Таблица поверх двух источников сразу.

Спикер в атрибуции — строка, а не ссылка на карту, поэтому таблицы разъезжаются
молча и в обе стороны: имя может говорить, не существуя в карте, и существовать
в карте, ни разу не заговорив. Показывать надо оба случая.
"""
import json
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import AudioFile, Character, ScriptBook, ScriptChapter
from app.v2.character_map import (
    CharacterMapError,
    adopt_speaker,
    character_map,
    delete_character,
    merge_speaker,
    rename_role,
)
from app.v2.models import V2Attribution, V2Segment

BOOK = "book-map"


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with sessionmaker(bind=engine)() as session:
        session.add(ScriptBook(id=BOOK, title="Крылья полумрака",
                               source_filename="k.txt", source_format="txt"))
        for index in (1, 2):
            session.add(ScriptChapter(id=f"ch-{index}", book_id=BOOK, chapter_index=index,
                                      chapter_title=f"Глава {index}", status="published"))
        yield session


def _say(db, chapter: str, ordinal: int, speaker: str) -> None:
    text = f"Реплика {ordinal}."
    seg = f"{chapter}:{ordinal:05d}"
    db.add(V2Segment(id=seg, book_id=BOOK, chapter_id=chapter, ordinal=ordinal,
                     kind="paragraph", text=text, char_start=0, char_end=len(text)))
    db.add(V2Attribution(id=str(uuid.uuid4()), segment_id=seg, span_start=0,
                         span_end=len(text), speaker=speaker, confidence=0.9,
                         source="llm", version=1))


def test_a_name_that_speaks_without_a_cast_row_is_still_a_row(db):
    """«Дракон» с тридцатью пятью репликами не существует для системы — и это
    ровно то, чего не видно в касте сегодня."""
    _say(db, "ch-1", 0, "Дракон")
    db.commit()

    rows = {row["name"]: row for row in character_map(db, BOOK)}

    assert rows["Дракон"]["in_markup"] is True
    assert rows["Дракон"]["in_cast"] is False
    assert rows["Дракон"]["lines"] == 1


def test_a_cast_row_that_never_speaks_is_a_row_too(db):
    """Фантом извлечения: удалить его безопасно, и таблица должна это показать."""
    db.add(Character(id="c1", book_id=BOOK, name="Жена Песгуоза"))
    db.commit()

    rows = {row["name"]: row for row in character_map(db, BOOK)}

    assert rows["Жена Песгуоза"]["in_cast"] is True
    assert rows["Жена Песгуоза"]["in_markup"] is False
    assert rows["Жена Песгуоза"]["lines"] == 0


def test_lines_and_chapters_are_counted_over_the_effective_version(db):
    """Считать все версии значило бы считать и отменённые правки."""
    _say(db, "ch-1", 0, "Гамук")
    _say(db, "ch-2", 1, "Гамук")
    db.add(V2Attribution(id=str(uuid.uuid4()), segment_id="ch-1:00000", span_start=0,
                         span_end=9, speaker="Бдеукс", confidence=1.0,
                         source="operator", version=2))
    db.commit()

    rows = {row["name"]: row for row in character_map(db, BOOK)}

    assert rows["Гамук"]["lines"] == 1 and rows["Гамук"]["chapters"] == 1
    assert rows["Бдеукс"]["lines"] == 1


def test_a_placeholder_is_shown_but_marked(db):
    """`UNSURE` держит 268 реплик на живой книге. Прятать его — врать о работе,
    предлагать над ним действия — звать чинить карту вместо разметки."""
    _say(db, "ch-1", 0, "UNSURE")
    db.commit()

    rows = {row["name"]: row for row in character_map(db, BOOK)}

    assert rows["UNSURE"]["is_placeholder"] is True


def test_a_role_with_recordings_is_flagged(db):
    """Предупреждение перед слиянием: дубли диктора останутся под старым именем."""
    _say(db, "ch-1", 0, "Гамук")
    db.add(AudioFile(book_code="KP", original_filename="a.wav", stored_key="KP/Chapter01/a.wav",
                     mime_type="audio/wav", size_bytes=1, chapter="Глава 1", role="Гамук"))
    db.commit()

    rows = {row["name"]: row for row in character_map(db, BOOK)}

    assert rows["Гамук"]["has_audio"] is True


def test_a_cast_member_speaking_under_a_different_case_is_not_shown_silent(db):
    """Живой дефект: «Похотливый жрец» в карте и «Похотливый Жрец» в разметке —
    одно и то же имя, просто с другим регистром буквы. Точное сравнение строк их
    не связывает, и автор видит настоящего говорящего персонажа как молчащего
    фантома — ровно та ошибка, ради которой отлова которой затевалась таблица."""
    db.add(Character(id="c1", book_id=BOOK, name="Похотливый жрец"))
    _say(db, "ch-1", 0, "Похотливый Жрец")
    db.commit()

    rows = {row["name"]: row for row in character_map(db, BOOK)}

    assert "Похотливый жрец" in rows
    assert rows["Похотливый жрец"]["in_cast"] is True
    assert rows["Похотливый жрец"]["in_markup"] is True
    assert rows["Похотливый жрец"]["lines"] == 1
    # Строка одна на персонажа, не две под разным регистром.
    assert "Похотливый Жрец" not in rows


def test_a_speaking_alias_folds_onto_its_cast_row(db):
    """Алиас — то же имя персонажа с точки зрения разметки: «Сомнамбула» это
    «Гамук», а не отдельный говорящий."""
    db.add(Character(id="c1", book_id=BOOK, name="Гамук", aliases="Сомнамбула"))
    _say(db, "ch-1", 0, "Сомнамбула")
    db.commit()

    rows = {row["name"]: row for row in character_map(db, BOOK)}

    assert rows["Гамук"]["in_markup"] is True
    assert rows["Гамук"]["lines"] == 1
    assert "Сомнамбула" not in rows


def test_names_differing_by_a_letter_stay_two_separate_rows(db):
    """«Жена Ветциога» в разметке и «Жена Песгуоза» в карте — опечатка в одну
    букву в корне, а не регистр. Это разные имена, и склеивать их нельзя: именно
    ради разбора таких случаев вся таблица и затевалась."""
    db.add(Character(id="c1", book_id=BOOK, name="Жена Песгуоза"))
    _say(db, "ch-1", 0, "Жена Ветциога")
    db.commit()

    rows = {row["name"]: row for row in character_map(db, BOOK)}

    assert rows["Жена Песгуоза"]["in_cast"] is True
    assert rows["Жена Песгуоза"]["in_markup"] is False
    assert rows["Жена Песгуоза"]["lines"] == 0
    assert rows["Жена Ветциога"]["in_cast"] is False
    assert rows["Жена Ветциога"]["in_markup"] is True
    assert rows["Жена Ветциога"]["lines"] == 1


def test_an_audio_role_differing_only_by_case_flags_the_cast_row(db):
    """Та же проба, но записана на роль в другом регистре: связать с персонажем
    карты надо и здесь, а не только для реплик."""
    db.add(Character(id="c1", book_id=BOOK, name="Похотливый жрец"))
    db.add(AudioFile(book_code="KP", original_filename="a.wav", stored_key="KP/Chapter01/a.wav",
                     mime_type="audio/wav", size_bytes=1, chapter="Глава 1",
                     role="Похотливый Жрец"))
    db.commit()

    rows = {row["name"]: row for row in character_map(db, BOOK)}

    assert rows["Похотливый жрец"]["has_audio"] is True
    assert "Похотливый Жрец" not in rows


def test_a_recording_with_no_character_and_no_lines_is_a_row_of_its_own(db):
    """Четвёртый случай, которого нет в §3 спеки: запись есть, а персонажа и реплик
    нет вовсе. Так выглядит проба, осиротевшая после слияния ролей.

    Строку надо показать — иначе запись не видна нигде, — но по трём признакам она
    отличима от молчащего персонажа карты, и это важно: у молчащего есть что удалять,
    а тут удалять нечего, разбираться надо с файлом. Экран обязан их различать,
    поэтому комбинация закреплена тестом, а не оставлена на догадливость фронта."""
    db.add(AudioFile(book_code="KP", original_filename="a.wav", stored_key="KP/Chapter01/a.wav",
                     mime_type="audio/wav", size_bytes=1, chapter="Глава 1", role="Дракон"))
    db.commit()

    row = {r["name"]: r for r in character_map(db, BOOK)}["Дракон"]

    assert row["in_cast"] is False
    assert row["in_markup"] is False
    assert row["has_audio"] is True
    assert row["lines"] == 0


def test_the_map_is_closed_to_a_dictor(monkeypatch):
    """Таблица живёт в хабе книги, за той же границей, что и «Подготовка»."""
    import app.v2.api as api

    monkeypatch.setattr(api, "_is_authenticated", lambda request: True)
    monkeypatch.setattr(api, "_can_edit", lambda request: False)

    assert api.api_v2_character_map(object(), BOOK).status_code == 403


def test_a_phantom_is_deleted(db):
    db.add(Character(id="c1", book_id=BOOK, name="Жена Песгуоза"))
    db.commit()

    delete_character(db, BOOK, "Жена Песгуоза")
    db.commit()

    assert db.query(Character).filter(Character.book_id == BOOK).count() == 0


def test_a_speaking_character_is_not_deleted(db):
    """Удалить строку карты у говорящего имени значит оставить его реплики висеть
    на том, чего в карте нет: разметка не изменится, а рассогласование появится."""
    db.add(Character(id="c1", book_id=BOOK, name="Гамук"))
    _say(db, "ch-1", 0, "Гамук")
    db.commit()

    with pytest.raises(CharacterMapError) as raised:
        delete_character(db, BOOK, "Гамук")
    assert raised.value.code == "speaks"


def test_a_character_with_recordings_is_not_deleted(db):
    db.add(Character(id="c1", book_id=BOOK, name="Гамук"))
    db.add(AudioFile(book_code="KP", original_filename="a.wav", stored_key="KP/Chapter01/a.wav",
                     mime_type="audio/wav", size_bytes=1, chapter="Глава 1", role="Гамук"))
    db.commit()

    with pytest.raises(CharacterMapError) as raised:
        delete_character(db, BOOK, "Гамук")
    assert raised.value.code == "has_audio"


def test_a_speaking_name_can_be_taken_into_the_cast(db):
    """«Дракон» говорит тридцать пять раз, а в карте его нет. Завести — значит
    узаконить то, что уже размечено, ничего в тексте не трогая."""
    _say(db, "ch-1", 0, "Дракон")
    db.commit()

    adopt_speaker(db, BOOK, "Дракон")
    db.commit()

    row = {r["name"]: r for r in character_map(db, BOOK)}["Дракон"]
    assert row["in_cast"] is True and row["lines"] == 1


def test_adopting_twice_changes_nothing(db):
    _say(db, "ch-1", 0, "Дракон")
    db.commit()
    adopt_speaker(db, BOOK, "Дракон")
    db.commit()

    adopt_speaker(db, BOOK, "Дракон")
    db.commit()

    assert db.query(Character).filter(Character.book_id == BOOK).count() == 1


def test_a_placeholder_is_never_taken_into_the_cast(db):
    """`UNSURE` — это «модель не уверена», а не персонаж. Заведя его в карте,
    мы получим роль, которую кто-то однажды раздаст диктору."""
    _say(db, "ch-1", 0, "UNSURE")
    db.commit()

    with pytest.raises(CharacterMapError) as raised:
        adopt_speaker(db, BOOK, "UNSURE")
    assert raised.value.code == "placeholder"


def test_a_broken_body_is_a_plain_refusal_not_a_crash(monkeypatch):
    """Кривой JSON должен давать внятный отказ, как у соседних ручек файла.
    Без этой проверки `body` приходит `None`, и обращение к нему роняет запрос
    пятисоткой — автор увидит «что-то сломалось» вместо «тело запроса кривое»."""
    import asyncio

    import app.v2.api as api

    monkeypatch.setattr(api, "_is_authenticated", lambda request: True)
    monkeypatch.setattr(api, "_can_edit", lambda request: True)

    class _Broken:
        async def json(self):
            raise ValueError("не json")

    for handler in (api.api_v2_character_map_delete, api.api_v2_character_map_adopt):
        response = asyncio.run(handler(_Broken(), BOOK))
        assert response.status_code == 400


def test_merging_moves_the_lines_and_keeps_the_old_version(db):
    """Слияние пишет НОВУЮ версию, а не правит на месте: версии — единственный
    откат, который остаётся у автора после правки, задевшей шестьдесят глав."""
    from app.v2.models import V2Attribution

    db.add(Character(id="c1", book_id=BOOK, name="Дгарни́н"))
    db.add(Character(id="c2", book_id=BOOK, name="Дгарнин"))
    _say(db, "ch-1", 0, "Дгарни́н")
    _say(db, "ch-2", 1, "Дгарнин")
    db.commit()

    merge_speaker(db, book_id=BOOK, source="Дгарни́н", target="Дгарнин", actor_uid="u1")
    db.commit()

    rows = {row["name"]: row for row in character_map(db, BOOK)}
    assert "Дгарни́н" not in rows
    assert rows["Дгарнин"]["lines"] == 2
    assert db.query(V2Attribution).filter(V2Attribution.speaker == "Дгарни́н").count() == 1


def test_merging_keeps_every_other_span_of_the_touched_segment(db):
    """Сегмент переписывается целиком. Возьми только спаны сливаемого имени — и
    соседние реплики исчезнут вместе с новой версией, молча."""
    db.add(Character(id="c1", book_id=BOOK, name="Дракон"))
    db.add(Character(id="c2", book_id=BOOK, name="Имисудс"))
    text = "Реплика 0."
    db.add(V2Segment(id="ch-1:00000", book_id=BOOK, chapter_id="ch-1", ordinal=0,
                     kind="paragraph", text=text, char_start=0, char_end=len(text)))
    db.add(V2Attribution(id=str(uuid.uuid4()), segment_id="ch-1:00000", span_start=0,
                         span_end=5, speaker="Дракон", confidence=0.9, source="llm", version=1))
    db.add(V2Attribution(id=str(uuid.uuid4()), segment_id="ch-1:00000", span_start=5,
                         span_end=len(text), speaker="Рассказчик", confidence=0.9,
                         source="llm", version=1))
    db.commit()

    merge_speaker(db, book_id=BOOK, source="Дракон", target="Имисудс", actor_uid="u1")
    db.commit()

    rows = {row["name"]: row for row in character_map(db, BOOK)}
    assert rows["Имисудс"]["lines"] == 1
    assert rows["Рассказчик"]["lines"] == 1, "соседний спан пропал вместе с новой версией"


def test_merging_finds_the_lines_however_the_markup_spells_them(db):
    """Строка таблицы носит написание из карты, а разметка хранит своё. Сличать
    надо по свёрнутой форме, иначе слияние молча не заметит вариант регистра
    и оставит реплики на исходном имени."""
    db.add(Character(id="c1", book_id=BOOK, name="Похотливый жрец"))
    db.add(Character(id="c2", book_id=BOOK, name="Жрец"))
    _say(db, "ch-1", 0, "Похотливый Жрец")
    db.commit()

    merge_speaker(db, book_id=BOOK, source="Похотливый жрец", target="Жрец", actor_uid="u1")
    db.commit()

    rows = {row["name"]: row for row in character_map(db, BOOK)}
    assert rows["Жрец"]["lines"] == 1
    assert "Похотливый жрец" not in rows


def test_merging_into_a_name_that_does_not_exist_is_refused(db):
    db.add(Character(id="c1", book_id=BOOK, name="Дгарнин"))
    _say(db, "ch-1", 0, "Дгарнин")
    db.commit()

    with pytest.raises(CharacterMapError) as raised:
        merge_speaker(db, book_id=BOOK, source="Дгарнин", target="Кого-нет", actor_uid="u1")
    assert raised.value.code == "not_found"


def test_a_placeholder_is_never_merged(db):
    """Разбор `UNSURE` — работа с разметкой, а не с картой: слить его в персонажа
    значит объявить уверенность, которой не было."""
    db.add(Character(id="c1", book_id=BOOK, name="Гамук"))
    _say(db, "ch-1", 0, "UNSURE")
    _say(db, "ch-2", 1, "Гамук")
    db.commit()

    with pytest.raises(CharacterMapError) as raised:
        merge_speaker(db, book_id=BOOK, source="UNSURE", target="Гамук", actor_uid="u1")
    assert raised.value.code == "placeholder"


def test_merging_a_name_into_itself_is_refused(db):
    db.add(Character(id="c1", book_id=BOOK, name="Гамук"))
    _say(db, "ch-1", 0, "Гамук")
    db.commit()

    with pytest.raises(CharacterMapError) as raised:
        merge_speaker(db, book_id=BOOK, source="Гамук", target="Гамук", actor_uid="u1")
    assert raised.value.code == "same_name"


def test_merging_into_a_name_that_is_not_in_the_cast_is_refused(db):
    """Слить персонажа в имя, которого в карте нет, — значит сделать хуже: строка
    карты исчезнет, а реплики окажутся на имени, о котором система не знает.

    Правильный порядок из двух обратимых шагов: сначала завести имя в карте,
    потом сливать. Одним необратимым движением этого делать нельзя."""
    db.add(Character(id="c1", book_id=BOOK, name="Дгарнин"))
    _say(db, "ch-1", 0, "Дгарнин")
    _say(db, "ch-2", 1, "Пришелец")
    db.commit()

    with pytest.raises(CharacterMapError) as raised:
        merge_speaker(db, book_id=BOOK, source="Дгарнин", target="Пришелец", actor_uid="u1")
    assert raised.value.code == "target_not_in_cast"

    rows = {row["name"]: row for row in character_map(db, BOOK)}
    assert rows["Дгарнин"]["in_cast"] is True, "персонаж не должен пострадать от отказа"
    assert rows["Дгарнин"]["lines"] == 1


def test_merging_one_out_of_cast_spelling_leaves_the_other_alone(db):
    """«Дракон» и «ДРАКОН» вне карты — таблица показывает их двумя строками
    (`normalize_name` свернуло бы их в одну), и слияние одной не должно задеть
    другую: предпросмотр обязан совпадать с тем, что переедет на самом деле."""
    db.add(Character(id="c1", book_id=BOOK, name="Имисудс"))
    _say(db, "ch-1", 0, "Дракон")
    _say(db, "ch-2", 1, "ДРАКОН")
    db.commit()

    rows_before = {row["name"]: row for row in character_map(db, BOOK)}
    assert rows_before["Дракон"]["lines"] == 1
    assert rows_before["ДРАКОН"]["lines"] == 1

    merge_speaker(db, book_id=BOOK, source="Дракон", target="Имисудс", actor_uid="u1")
    db.commit()

    rows = {row["name"]: row for row in character_map(db, BOOK)}
    assert rows["Имисудс"]["lines"] == 1, "переехала одна реплика, лист обещал одну"
    assert rows["ДРАКОН"]["lines"] == 1, "другое написание вне карты должно остаться нетронутым"
    assert "Дракон" not in rows


def test_merging_a_silent_cast_row_is_refused(db):
    """`delete_character` отказывает молчащему имени с дублями; `merge_speaker`
    с молчащим источником удаляет строку карты, перенеся ноль спанов — то же
    удаление, только в обход тех же проверок. Отказ обязателен."""
    db.add(Character(id="c1", book_id=BOOK, name="Жена Песгуоза"))
    db.add(Character(id="c2", book_id=BOOK, name="Другой"))
    db.commit()

    with pytest.raises(CharacterMapError) as raised:
        merge_speaker(db, book_id=BOOK, source="Жена Песгуоза", target="Другой", actor_uid="u1")
    assert raised.value.code == "nothing_to_merge"

    assert db.query(Character).filter(Character.name == "Жена Песгуоза").count() == 1, \
        "отказ обязан ничего не менять"


def test_adopting_a_narrator_synonym_is_refused(db):
    """«От автора» — синоним рассказчика в разметке, не второй персонаж.
    Заведи его в карте — и резолвер бюджета сможет выбрать рассказчиком
    новорождённого пустого вместо настоящего «Рассказчика»."""
    _say(db, "ch-1", 0, "От автора")
    db.commit()

    with pytest.raises(CharacterMapError) as raised:
        adopt_speaker(db, BOOK, "От автора")
    assert raised.value.code == "narrator_is_not_a_role"

    assert db.query(Character).filter(Character.book_id == BOOK).count() == 0


def test_merging_the_narrator_as_source_is_refused(db):
    """`adopt_speaker` и `rename_role` уже отбивают рассказчика — у слияния
    цена ошибки выше их обоих (тысячи реплик), а защиты не было вовсе."""
    db.add(Character(id="c1", book_id=BOOK, name="Гамук"))
    _say(db, "ch-1", 0, "Рассказчик")
    db.commit()

    with pytest.raises(CharacterMapError) as raised:
        merge_speaker(db, book_id=BOOK, source="Рассказчик", target="Гамук", actor_uid="u1")
    assert raised.value.code == "narrator_is_not_a_role"

    assert db.query(Character).filter(Character.book_id == BOOK).count() == 1, \
        "отказ обязан ничего не менять"


def test_merging_into_the_narrator_as_target_is_refused(db):
    """Та же защита в обратную сторону: слить говорящую роль в рассказчика
    отдало бы её реплики рассказчику молча, тем же путём, что запрещён
    у `rename_role`."""
    db.add(Character(id="c1", book_id=BOOK, name="Гамук"))
    _say(db, "ch-1", 0, "Гамук")
    _say(db, "ch-2", 1, "Рассказчик")
    db.commit()

    with pytest.raises(CharacterMapError) as raised:
        merge_speaker(db, book_id=BOOK, source="Гамук", target="Рассказчик", actor_uid="u1")
    assert raised.value.code == "narrator_is_not_a_role"

    rows = {row["name"]: row for row in character_map(db, BOOK)}
    assert rows["Гамук"]["lines"] == 1, "отказ обязан ничего не менять"


def test_deleting_a_phantom_records_what_was_lost(db):
    """Версии атрибуций не хранят диктора, цвет, алиасы, связь с каноном автора,
    расу, возраст, темперамент, заявленные главы — удалить строку карты значит
    потерять их без следа, если журнал вмешательств не запишет содержимое
    строки, а не только «удалили X»."""
    from app.models import OperatorIntervention

    db.add(Character(id="c1", book_id=BOOK, name="Жена Песгуоза", actor_name="Иванова",
                     character_color="#ff0000", aliases="Жена, Песгуоза",
                     author_character_id="canon-42", operator_note="проверено",
                     race="фархеррим", age="220 лет", temperament="Вспыльчивая",
                     appears_in="1,4,7"))
    db.commit()

    delete_character(db, BOOK, "Жена Песгуоза", actor_uid="u1", actor_name="Оператор")
    db.commit()

    row = db.query(OperatorIntervention).filter(
        OperatorIntervention.action_type == "v2_delete_character").one()
    import json
    payload = json.loads(row.payload_json)
    assert payload["name"] == "Жена Песгуоза"
    assert payload["actor_name"] == "Иванова"
    assert payload["character_color"] == "#ff0000"
    assert payload["aliases"] == "Жена, Песгуоза"
    assert payload["author_character_id"] == "canon-42"
    assert payload["race"] == "фархеррим", "расу вписывает человек — версиям атрибуций нечем её восстановить"
    assert payload["age"] == "220 лет"
    assert payload["temperament"] == "Вспыльчивая"
    assert payload["appears_in"] == "1,4,7"


def test_merging_records_what_the_source_row_carried(db):
    """Слияние тоже стирает строку карты источника — журнал обязан запомнить,
    что на ней было, так же, как при удалении. Раса, возраст и темперамент —
    поля, которые заполняет человек, и версии атрибуций их не хранят вовсе:
    слияние роли без них в журнале уносит эту работу безвозвратно."""
    from app.models import OperatorIntervention

    db.add(Character(id="c1", book_id=BOOK, name="Дгарни́н", actor_name="Петров",
                     character_color="#00ff00", author_character_id="canon-7",
                     race="фархеррим", age="40 лет", temperament="Хладнокровный",
                     appears_in="1,2"))
    db.add(Character(id="c2", book_id=BOOK, name="Дгарнин"))
    _say(db, "ch-1", 0, "Дгарни́н")
    db.commit()

    merge_speaker(db, book_id=BOOK, source="Дгарни́н", target="Дгарнин", actor_uid="u1")
    db.commit()

    row = db.query(OperatorIntervention).filter(
        OperatorIntervention.action_type == "v2_merge_character").one()
    import json
    payload = json.loads(row.payload_json)
    assert payload["actor_name"] == "Петров"
    assert payload["character_color"] == "#00ff00"
    assert payload["race"] == "фархеррим"
    assert payload["age"] == "40 лет"
    assert payload["temperament"] == "Хладнокровный"
    assert payload["appears_in"] == "1,2"
    assert payload["author_character_id"] == "canon-7"


def test_merging_reports_lines_moved_not_segments_touched(db):
    """Тост после слияния обязан считать то же, что обещал лист: реплики, а не
    абзацы. Один абзац может нести две реплики одного говорящего — тогда
    сегментов меньше, чем реплик, и K абзацев всегда меньше N реплик."""
    db.add(Character(id="c1", book_id=BOOK, name="Дракон"))
    db.add(Character(id="c2", book_id=BOOK, name="Имисудс"))
    text = "Реплика 0. Реплика 1."
    db.add(V2Segment(id="ch-1:00000", book_id=BOOK, chapter_id="ch-1", ordinal=0,
                     kind="paragraph", text=text, char_start=0, char_end=len(text)))
    db.add(V2Attribution(id=str(uuid.uuid4()), segment_id="ch-1:00000", span_start=0,
                         span_end=11, speaker="Дракон", confidence=0.9, source="llm", version=1))
    db.add(V2Attribution(id=str(uuid.uuid4()), segment_id="ch-1:00000", span_start=11,
                         span_end=len(text), speaker="Дракон", confidence=0.9, source="llm", version=1))
    db.commit()

    result = merge_speaker(db, book_id=BOOK, source="Дракон", target="Имисудс", actor_uid="u1")
    db.commit()

    assert result["segments"] == 1
    assert result["lines"] == 2


def test_the_journal_keeps_the_manual_rate_too(db):
    """Журнал возвращает голос и канон — обязан возвращать и деньги.

    Ручная ставка не версионируется, и строка, восстановленная по журналу без
    неё, тихо съедет на тарифный план студии: расхождение всплывёт в смете, а не
    на экране. На живой книге такие ставки стоят у трёх персонажей."""
    from app.models import OperatorIntervention

    db.add(Character(id="c1", book_id=BOOK, name="Молчун", actor_name="Иван",
                     manual_rate_rub_per_min=42.5, manual_fixed_rub=1000.0))
    db.commit()

    delete_character(db, BOOK, "Молчун", actor_uid="u1")
    db.commit()

    logged = db.query(OperatorIntervention).all()[-1]
    payload = json.loads(logged.payload_json) if isinstance(logged.payload_json, str) else logged.payload_json
    lost = payload.get("lost") or payload
    assert lost["manual_rate_rub_per_min"] == 42.5
    assert lost["manual_fixed_rub"] == 1000.0


def test_a_role_carries_an_age_and_the_book_remembers_the_check(db):
    """Возраст — единственное поле, которого в базе не было. Отметка проверки
    живёт у книги, а не у строки: режиссёр судит по ней, можно ли верить карте
    целиком, а не отдельному персонажу."""
    from app.models import ScriptBook
    from app.time_utils import utcnow_naive

    db.add(Character(id="c1", book_id=BOOK, name="Гамук", age="около сорока"))
    book = db.get(ScriptBook, BOOK)
    book.cast_checked_at = utcnow_naive()
    book.cast_checked_by = "Александр Белозёров"
    db.commit()

    assert db.query(Character).filter(Character.name == "Гамук").one().age == "около сорока"
    assert db.get(ScriptBook, BOOK).cast_checked_by == "Александр Белозёров"


def test_the_row_carries_what_the_director_reads(db):
    """Шпаргалка: раса, возраст, характер, голос. Характеристика голоса живёт в
    поле `operator_note` — оно так названо исторически, а заполнено кастинговыми
    заданиями вроде «спокойный, звучный голос профессионального оратора»."""
    db.add(Character(id="c1", book_id=BOOK, name="Дгарнин", race="демон", age="древний",
                     temperament="Холоден, расчётлив и вездесущ",
                     operator_note="спокойный, звучный голос профессионального оратора",
                     aliases="Пресвитер", appears_in="1,2,7"))
    _say(db, "ch-1", 0, "Дгарнин")
    db.commit()

    row = {r["name"]: r for r in character_map(db, BOOK)}["Дгарнин"]

    assert row["race"] == "демон" and row["age"] == "древний"
    assert "Холоден" in row["temperament"]
    assert "оратора" in row["voice"]
    assert row["aliases"] == "Пресвитер"
    assert row["claimed_chapters"] == [1, 2, 7]
    assert row["chapters"] == 1, "посчитанное по разметке — главное число"


def test_a_row_that_exists_only_in_the_markup_has_empty_fields_not_missing_ones(db):
    """У имени без строки карты полей нет, но ключи должны быть: экран не обязан
    гадать, чего ждать от строки."""
    _say(db, "ch-1", 0, "Дракон")
    db.commit()

    row = {r["name"]: r for r in character_map(db, BOOK)}["Дракон"]

    assert row["race"] == "" and row["voice"] == "" and row["claimed_chapters"] == []


def test_editing_a_role_touches_only_the_named_fields(db):
    """Правка одного поля не должна затирать соседние: автор и режиссёр правят
    одну строку с разных сторон и в разное время."""
    from app.v2.character_map import update_role

    db.add(Character(id="c1", book_id=BOOK, name="Гамук", race="демон",
                     temperament="Мечтательный", actor_name="Гончаров Иван"))
    db.commit()

    update_role(db, BOOK, "Гамук", {"age": "молод"})
    db.commit()

    row = db.query(Character).filter(Character.name == "Гамук").one()
    assert row.age == "молод"
    assert row.race == "демон" and row.temperament == "Мечтательный"
    assert row.actor_name == "Гончаров Иван"


def test_a_trailing_question_mark_means_a_tentative_cast(db):
    """«Химера — Натали Ким?» — так владелец уже пишет. Система понимает его
    нотацию вместо того, чтобы навязывать переключатель."""
    from app.v2.character_map import update_role

    db.add(Character(id="c1", book_id=BOOK, name="Химера"))
    _say(db, "ch-1", 0, "Химера")
    db.commit()

    update_role(db, BOOK, "Химера", {"actor_name": "Натали Ким?"})
    db.commit()

    row = {r["name"]: r for r in character_map(db, BOOK)}["Химера"]
    assert row["actor_name"] == "Натали Ким"
    assert row["actor_tentative"] is True


def test_editing_a_role_that_is_not_in_the_cast_is_refused(db):
    """Править нечего: строки карты нет. Сначала «завести в карте»."""
    from app.v2.character_map import CharacterMapError, update_role

    _say(db, "ch-1", 0, "Дракон")
    db.commit()

    with pytest.raises(CharacterMapError) as raised:
        update_role(db, BOOK, "Дракон", {"race": "дракон"})
    assert raised.value.code == "not_in_cast"


def test_question_marks_alone_mean_undecided_not_tentative(db):
    """«???» в поле актёра — живая пометка из книги: «ещё не решил». Такая роль
    свободна, а не назначена предварительно.

    Разбор хвостового знака вопроса не должен превращать её в «предварительно
    назначена никому»: на экране это нарисовало бы пометку без имени, а в списке
    предварительных — пустую строку, которую не с кем сверить."""
    db.add(Character(id="c1", book_id=BOOK, name="Бапдиг", actor_name="???"))
    _say(db, "ch-1", 0, "Бапдиг")
    db.commit()

    row = {r["name"]: r for r in character_map(db, BOOK)}["Бапдиг"]

    assert row["actor_name"] == ""
    assert row["actor_tentative"] is False, "предварительно назначить некому — роль свободна"


def test_the_row_names_the_chapters_it_actually_speaks_in(db):
    """«Заявлено три главы, размечено две» не говорит, какую искать. Расхождение —
    сигнал о тексте, и по нему идут смотреть глазами: значит нужен номер, а не
    разница чисел."""
    db.add(Character(id="c1", book_id=BOOK, name="Гамук", appears_in="1,2,7"))
    _say(db, "ch-1", 0, "Гамук")
    _say(db, "ch-2", 1, "Гамук")
    db.commit()

    row = {r["name"]: r for r in character_map(db, BOOK)}["Гамук"]

    assert row["chapter_numbers"] == [1, 2]
    assert row["claimed_chapters"] == [1, 2, 7]
    assert row["chapters"] == 2


def test_a_row_says_whether_the_canon_knows_this_character(db, monkeypatch):
    """Автор проверяет по этой карте работу модели, и главный его вопрос —
    «этого персонажа я писал или модель придумала?». Ответ считается сверкой с
    корпусом автора; без него карта показывает имя, но не говорит, откуда оно.

    Корпус может быть не подключён — тогда честный ответ «не знаем», а не «новый»:
    объявить всех выдумкой из-за отсутствия справочника хуже, чем промолчать."""
    import app.v2.character_map as cm

    db.add(Character(id="c1", book_id=BOOK, name="Дгарнин"))
    db.add(Character(id="c2", book_id=BOOK, name="Выдумка"))
    db.commit()

    monkeypatch.setattr(cm, "_canon_known_names", lambda db, book_id: {"дгарнин"})
    rows = {r["name"]: r for r in character_map(db, BOOK)}
    assert rows["Дгарнин"]["canon_status"] == "known"
    assert rows["Выдумка"]["canon_status"] == "new"

    monkeypatch.setattr(cm, "_canon_known_names", lambda db, book_id: None)
    rows = {r["name"]: r for r in character_map(db, BOOK)}
    assert rows["Дгарнин"]["canon_status"] == ""


def test_row_carries_the_cast_row_id_so_money_can_be_merged_in(db):
    """Смета и карта сводятся по идентификатору, а не по имени: имя — это как раз
    то, что на экране чинят, и сводить по нему значит терять строку при опечатке."""
    db.add(Character(id="char-gamuk", book_id=BOOK, name="Гамук", actor_name="Иван"))
    _say(db, "ch-1", 0, "Гамук")
    _say(db, "ch-1", 1, "Дракон")
    db.commit()

    rows = {row["name"]: row for row in character_map(db, BOOK)}

    assert rows["Гамук"]["character_id"] == "char-gamuk"
    # «Дракон» говорит, но строки карты у него нет — сводить не с чем, и это честно
    assert rows["Дракон"]["character_id"] == ""


def test_renaming_a_silent_role_is_quiet(db):
    """У роли нет реплик — переименование меняет только строку карты."""
    db.add(Character(id="char-1", book_id=BOOK, name="Жена Песгуоза", race="человек"))
    db.commit()

    result = rename_role(db, book_id=BOOK, name="Жена Песгуоза",
                         new_name="Жена Ветциога", actor_uid="u1")
    db.commit()

    assert result["lines"] == 0
    assert db.get(Character, "char-1").name == "Жена Ветциога"


def test_renaming_a_speaking_role_without_consent_is_refused(db):
    """Молчаливое переименование говорящей роли и создало исходную беду: карта
    говорит одно, разметка другое, и обе выглядят исправными по отдельности."""
    db.add(Character(id="char-1", book_id=BOOK, name="Жена Ветциога"))
    _say(db, "ch-1", 0, "Жена Ветциога")
    db.commit()

    with pytest.raises(CharacterMapError) as caught:
        rename_role(db, book_id=BOOK, name="Жена Ветциога",
                    new_name="Жена Песгуоза", actor_uid="u1")
    assert caught.value.code == "needs_consent"


def test_renaming_a_speaking_role_moves_its_lines_as_a_new_version(db):
    """Реплики переезжают новой версией: версии — единственный откат, который
    остаётся у автора."""
    db.add(Character(id="char-1", book_id=BOOK, name="Жена Ветциога"))
    _say(db, "ch-1", 0, "Жена Ветциога")
    _say(db, "ch-1", 1, "Дгарнин")
    db.commit()

    result = rename_role(db, book_id=BOOK, name="Жена Ветциога",
                         new_name="Жена Песгуоза", move_lines=True, actor_uid="u1")
    db.commit()

    assert result["lines"] == 1
    rows = {row["name"]: row for row in character_map(db, BOOK)}
    assert rows["Жена Песгуоза"]["lines"] == 1
    assert "Жена Ветциога" not in rows
    # «Дгарнин» здесь лежит в СВОЁМ сегменте (`_say` заводит сегмент на каждый
    # ordinal) — эта строка ничего не говорит о целостности одного сегмента с
    # несколькими спанами, за это отвечает тест ниже.
    assert rows["Дгарнин"]["lines"] == 1
    # старая версия осталась в базе — есть куда откатываться
    assert db.query(V2Attribution).filter(V2Attribution.version == 1).count() == 2


def test_renaming_keeps_every_other_span_of_the_touched_segment(db):
    """Сегмент переписывается целиком. Возьми только спаны переименовываемого
    имени — и другой спикер того же сегмента исчезнет вместе с новой версией,
    без ошибки и следа: именно эту потерю проверка выше не может увидеть, потому
    что `_say` кладёт каждую реплику в собственный сегмент."""
    db.add(Character(id="char-1", book_id=BOOK, name="Жена Ветциога"))
    text = "Реплика 0. Реплика 1. Реплика 2."
    db.add(V2Segment(id="ch-1:00000", book_id=BOOK, chapter_id="ch-1", ordinal=0,
                     kind="paragraph", text=text, char_start=0, char_end=len(text)))
    db.add(V2Attribution(id=str(uuid.uuid4()), segment_id="ch-1:00000", span_start=0,
                         span_end=11, speaker="Жена Ветциога", confidence=0.9,
                         source="llm", version=1))
    db.add(V2Attribution(id=str(uuid.uuid4()), segment_id="ch-1:00000", span_start=11,
                         span_end=22, speaker="Рассказчик", confidence=0.9,
                         source="llm", version=1))
    db.add(V2Attribution(id=str(uuid.uuid4()), segment_id="ch-1:00000", span_start=22,
                         span_end=len(text), speaker="Дракон", confidence=0.9,
                         source="llm", version=1))
    db.commit()

    result = rename_role(db, book_id=BOOK, name="Жена Ветциога",
                         new_name="Жена Песгуоза", move_lines=True, actor_uid="u1")
    db.commit()

    assert result["lines"] == 1
    rows = {row["name"]: row for row in character_map(db, BOOK)}
    assert rows["Жена Песгуоза"]["lines"] == 1
    assert rows["Рассказчик"]["lines"] == 1, "спан рассказчика пропал вместе с новой версией"
    assert rows["Дракон"]["lines"] == 1, "спан дракона пропал вместе с новой версией"


def test_renaming_onto_an_existing_name_is_refused(db):
    """Два имени в одно — это слияние, у него свои проверки и своя цена.
    Пускать в него через переименование значит обойти их."""
    db.add(Character(id="char-1", book_id=BOOK, name="Жена Ветциога"))
    db.add(Character(id="char-2", book_id=BOOK, name="Жена Песгуоза"))
    db.commit()

    with pytest.raises(CharacterMapError) as caught:
        rename_role(db, book_id=BOOK, name="Жена Ветциога",
                    new_name="Жена Песгуоза", actor_uid="u1")
    assert caught.value.code == "name_taken"


def test_renaming_onto_a_name_that_only_speaks_is_refused(db):
    """Цель без строки карты, но говорящая в разметке, — это скрытое слияние:
    оно обязано пройти проверки `merge_speaker` (`nothing_to_merge`,
    `target_not_in_cast`, аудит утраченной строки), а не проскочить мимо них
    через переименование."""
    db.add(Character(id="char-1", book_id=BOOK, name="Жена Ветциога"))
    _say(db, "ch-1", 0, "Дгарнин")
    db.commit()

    with pytest.raises(CharacterMapError) as caught:
        rename_role(db, book_id=BOOK, name="Жена Ветциога",
                    new_name="Дгарнин", actor_uid="u1")
    assert caught.value.code == "name_taken"


def test_renaming_onto_a_speaking_name_in_a_different_case_is_refused(db):
    """Дыра ровно посередине двух соседних проверок: одна берёт говорящее имя с
    точным написанием, другая — две строки каста в разном регистре. А самый
    частый случай — говорящее имя БЕЗ строки каста, отличающееся от цели
    регистром или ударением: `cast_canonicalizer` сворачивает только к именам
    каста, поэтому такое имя в словарь свёртки не попадает, цель остаётся как
    набрана, и побуквенное сравнение расходится.

    Цена промаха — не отказ, а тихое слияние: роль забирает чужие реплики,
    журнал вмешательств называет только свои, а обратное переименование
    подберёт оба написания и сделает ошибку необратимой."""
    db.add(Character(id="char-1", book_id=BOOK, name="Гамук"))
    _say(db, "ch-1", 0, "Гамук")
    _say(db, "ch-1", 1, "ДРАКОН")
    _say(db, "ch-2", 0, "ДРАКО\u0301Н")
    db.commit()

    with pytest.raises(CharacterMapError) as caught:
        rename_role(db, book_id=BOOK, name="Гамук", new_name="Дракон",
                    move_lines=True, actor_uid="u1")
    assert caught.value.code == "name_taken"
    rows = {row["name"]: row for row in character_map(db, BOOK)}
    assert rows["Гамук"]["lines"] == 1, "реплика роли уехала, хотя переименование отклонено"
    assert "Дракон" not in rows, "роль всё-таки забрала чужое написание"


def test_renaming_onto_an_existing_name_in_a_different_case_is_refused(db):
    """Регистр не спасает от коллизии: без этой проверки завелись бы две строки
    `Character`, `cast_canonicalizer` сложил бы их в одну, и реплики настоящей
    роли перетекли бы в чужую — та самая беда, ради починки которой писалась
    вся задача, воспроизведённая через переименование."""
    db.add(Character(id="char-1", book_id=BOOK, name="Жена Ветциога"))
    db.add(Character(id="char-2", book_id=BOOK, name="Жена Песгуоза"))
    db.commit()

    with pytest.raises(CharacterMapError) as caught:
        rename_role(db, book_id=BOOK, name="Жена Ветциога",
                    new_name="жена песгуоза", actor_uid="u1")
    assert caught.value.code == "name_taken"


def test_renaming_onto_an_alias_of_another_role_is_refused(db):
    """Алиас — то же имя персонажа с точки зрения карты; переименование в него
    обязано отклоняться так же, как переименование в само каноническое написание."""
    db.add(Character(id="char-1", book_id=BOOK, name="Жена Ветциога"))
    db.add(Character(id="char-2", book_id=BOOK, name="Дракон", aliases="Ящер"))
    db.commit()

    with pytest.raises(CharacterMapError) as caught:
        rename_role(db, book_id=BOOK, name="Жена Ветциога",
                    new_name="Ящер", actor_uid="u1")
    assert caught.value.code == "name_taken"


def test_renaming_onto_a_placeholder_is_refused(db):
    """`UNSURE` — служебная метка модели, а не персонаж. Пропусти эту проверку —
    и роль после переезда станет дверью без ручки: `adopt`, `delete`, `merge` и
    сам `rename` отказывают служебной метке кодом `placeholder`, и починить её
    с экрана будет уже нечем."""
    db.add(Character(id="char-1", book_id=BOOK, name="Дракон"))
    db.commit()

    with pytest.raises(CharacterMapError) as caught:
        rename_role(db, book_id=BOOK, name="Дракон", new_name="UNSURE", actor_uid="u1")
    assert caught.value.code == "placeholder"


def test_renaming_onto_the_narrator_is_refused(db):
    """У переименования цена выше, чем у усыновления: реплики уезжают новой
    версией разметки, а не просто заводят строку карты. `adopt_speaker` уже
    отбивает синоним рассказчика кодом `narrator_is_not_a_role` — `rename_role`
    обязан тем же, а не тихо свалить чужие реплики на рассказчика."""
    db.add(Character(id="char-1", book_id=BOOK, name="Дракон"))
    db.commit()

    with pytest.raises(CharacterMapError) as caught:
        rename_role(db, book_id=BOOK, name="Дракон", new_name="Рассказчик", actor_uid="u1")
    assert caught.value.code == "narrator_is_not_a_role"


def test_renaming_the_narrator_as_source_is_refused(db):
    """Зеркальный случай — и он живой, а не теоретический: `character_map`
    заводит рассказчику строку той же логикой, что и говорящей роли
    (`_effective_speakers` ловит `is_placeholder_name`, но не `is_narrator_name`),
    поэтому без этой проверки первый заход честно отвечает `needs_consent` с
    реальным числом реплик, а согласие переносит их на новое имя по-настоящему —
    рассказчик исчезает из карты, а тысячи его реплик уезжают на опечатку."""
    _say(db, "ch-1", 0, "Рассказчик")
    db.commit()

    # Первый заход (`move_lines=False`) — ровно путь из живого отказа: без этой
    # проверки он отвечает `needs_consent` с реальным числом реплик, а не
    # заваливается раньше на источнике.
    with pytest.raises(CharacterMapError) as caught:
        rename_role(db, book_id=BOOK, name="Рассказчик", new_name="Новое Имя", actor_uid="u1")
    assert caught.value.code == "narrator_is_not_a_role"

    # И с уже данным согласием — на случай, если кто-то обойдёт первый заход
    # и позовёт сразу с `move_lines=True` — тоже должен упасть, а не перенести.
    with pytest.raises(CharacterMapError) as caught:
        rename_role(db, book_id=BOOK, name="Рассказчик", new_name="Новое Имя",
                    move_lines=True, actor_uid="u1")
    assert caught.value.code == "narrator_is_not_a_role"

    rows = {row["name"]: row for row in character_map(db, BOOK)}
    assert "Рассказчик" in rows, "отказ обязан ничего не менять"
    assert rows["Рассказчик"]["lines"] == 1

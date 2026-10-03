"""Находки консилиума живут в базе и переживают повторную загрузку прогона."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
# Импорт нужен именно здесь, а не в телах тестов: `create_all` создаёт таблицы по тому,
# что зарегистрировано в `Base.metadata` НА МОМЕНТ ВЫЗОВА. Без этой строки таблица
# находок не создаётся, и файл проходит только когда модель успел импортировать
# кто-то другой раньше — то есть по случайности порядка сборки.
from app.models import ConsiliumFinding  # noqa: F401
# Та же ловушка сборки таблиц: тест приёма находки заводит V2Attribution/V2Segment
# внутри своего тела, но `create_all` уже отработал к этому моменту фикстуры — модели
# должны быть импортированы здесь, до вызова `create_all`, а не там.
from app.v2.models import V2Attribution, V2Segment  # noqa: F401


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        yield session


def _book_with_paragraph(db, text: str, spans):
    """Книга, каст и один абзац с готовой разметкой: `spans` — (начало, конец, роль, уверенность).

    Сборка одна и та же в каждом тесте приёма, а значимо в них — только форма абзаца.
    """
    import uuid

    from app.models import Character, ScriptBook
    from app.time_utils import utcnow_naive
    from app.v2.models import V2Attribution, V2Segment

    if db.get(ScriptBook, "b1") is None:
        db.add(ScriptBook(id="b1", title="Книга", source_filename="k.txt", source_format="txt",
                          created_at=utcnow_naive()))
        for name in ("Дгарнин", "Тупуг", "Пупип", "Хвьадзукилай 1", "Хвьадзукилай 2"):
            db.add(Character(id=str(uuid.uuid4()), book_id="b1", name=name, aliases="",
                             appears_in="", created_at=utcnow_naive()))
    db.add(V2Segment(id="ch:00358", book_id="b1", chapter_id="ch", ordinal=358,
                     kind="paragraph", text=text))
    for start, end, speaker, confidence in spans:
        db.add(V2Attribution(id=str(uuid.uuid4()), segment_id="ch:00358", version=1,
                             speaker=speaker, span_start=start, span_end=end, source="llm",
                             confidence=confidence, created_at=utcnow_naive()))
    db.flush()


def _finding(ordinal: int, kind: str = "wrong_voice") -> dict:
    return {
        "chapter_index": 25, "ordinal": ordinal, "segment_id": f"ch:{ordinal:05d}",
        "span_start": 0, "span_end": 24, "kind": kind,
        "current_speaker": "Дгарнин", "readers_speaker": "Тупуг",
        "arbiter_verdict": "change", "arbiter_speaker": "Тупуг",
        "evidence_para": 356, "evidence_quote": "пообещал Тупуг", "evidence_proven": True,
        "reason": "чередование в диалоге",
    }


def test_findings_are_saved_and_counted(db):
    from app.services.consilium_store import book_findings, save_findings

    save_findings(db, book_id="b1", run_label="2026-09-12",
                  findings=[_finding(358), _finding(360, kind="narrator_border")])

    out = book_findings(db, book_id="b1")
    assert out["counts"]["wrong_voice"] == 1
    assert out["counts"]["narrator_border"] == 1
    assert out["counts"]["new"] == 2


def test_the_readers_own_answers_are_kept_and_shown(db):
    """Ответы чтецов порознь, а не только их общее мнение.

    У рода `readers_split` общего мнения нет по определению: расхождение чтецов и есть
    весь смысл такой находки. Без этих двух полей строка говорит человеку «сейчас
    Дгарнин» и больше ничего, а принять её нечем — выбирать не из чего.
    """
    from app.services.consilium_store import book_findings, save_findings

    save_findings(db, book_id="b1", run_label="r", findings=[dict(
        _finding(358, kind="readers_split"),
        readers_speaker="", arbiter_verdict="", arbiter_speaker="",
        reader_opus="Гамук", reader_sol="Пупип",
    )])

    item = book_findings(db, book_id="b1")["items"][0]
    assert (item["reader_opus"], item["reader_sol"]) == ("Гамук", "Пупип")


def test_an_unknown_kind_never_reaches_the_database(db):
    """Опечатка в роде находки — не данные, а порча: список родов закрытый."""
    from app.services.consilium_store import save_findings

    with pytest.raises(ValueError):
        save_findings(db, book_id="b1", run_label="r",
                      findings=[_finding(358, kind="wrong_vioce")])


def test_a_second_import_does_not_double_the_same_place(db):
    """Прогон перезапускают; находка о том же месте — та же находка, а не вторая."""
    from app.services.consilium_store import book_findings, save_findings

    save_findings(db, book_id="b1", run_label="2026-09-12", findings=[_finding(358)])
    result = save_findings(db, book_id="b1", run_label="2026-09-13", findings=[_finding(358)])

    assert result == {"added": 0, "kept": 1}
    assert book_findings(db, book_id="b1")["counts"]["new"] == 1


def test_a_decided_finding_is_not_resurrected_by_a_new_run(db):
    """Решение человека сильнее нового прогона: отклонённое не всплывает снова.

    Иначе каждый перезапуск возвращал бы владельцу то, что он уже разобрал, и список
    перестал бы убывать — а именно убывание и есть смысл работы.
    """
    from app.models import ConsiliumFinding
    from app.services.consilium_store import book_findings, save_findings

    save_findings(db, book_id="b1", run_label="2026-09-12", findings=[_finding(358)])
    row = db.query(ConsiliumFinding).one()
    row.status = "dismissed"
    db.flush()

    save_findings(db, book_id="b1", run_label="2026-09-13", findings=[_finding(358)])

    out = book_findings(db, book_id="b1")
    assert out["counts"]["new"] == 0
    assert out["counts"]["dismissed"] == 1


def test_one_chapter_can_be_asked_for(db):
    from app.services.consilium_store import book_findings, save_findings

    save_findings(db, book_id="b1", run_label="r", findings=[_finding(358)])

    assert book_findings(db, book_id="b1", chapter_index=25)["counts"]["new"] == 1
    assert book_findings(db, book_id="b1", chapter_index=26)["counts"]["new"] == 0


def test_accepting_rewrites_only_the_disputed_span(db):
    """Ловушка версий: у абзаца две реплики, меняем одну — вторая обязана выжить."""
    import uuid

    from app.models import Character, ConsiliumFinding, ScriptBook
    from app.services.consilium_store import accept_finding, save_findings
    from app.time_utils import utcnow_naive
    from app.v2.models import V2Attribution, V2Segment
    from app.v2.reader import effective_attributions

    db.add(ScriptBook(id="b1", title="Книга", source_filename="k.txt", source_format="txt",
                      created_at=utcnow_naive()))
    for name in ("Дгарнин", "Тупуг"):
        db.add(Character(id=str(uuid.uuid4()), book_id="b1", name=name, aliases="",
                         appears_in="", created_at=utcnow_naive()))
    db.add(V2Segment(id="ch:00358", book_id="b1", chapter_id="ch", ordinal=358,
                     kind="paragraph", text="- Почему ты так уверен? - и следом слова автора."))
    db.add(V2Attribution(id=str(uuid.uuid4()), segment_id="ch:00358", version=1,
                         speaker="Дгарнин", span_start=0, span_end=24, source="llm",
                         created_at=utcnow_naive()))
    db.add(V2Attribution(id=str(uuid.uuid4()), segment_id="ch:00358", version=1,
                         speaker="Рассказчик", span_start=24, span_end=48, source="llm",
                         created_at=utcnow_naive()))
    db.flush()
    save_findings(db, book_id="b1", run_label="r", findings=[{
        "chapter_index": 25, "ordinal": 358, "segment_id": "ch:00358",
        "span_start": 0, "span_end": 24, "kind": "wrong_voice",
        "current_speaker": "Дгарнин", "readers_speaker": "Тупуг",
        "arbiter_verdict": "change", "arbiter_speaker": "Тупуг", "evidence_para": 356,
        "evidence_quote": "пообещал Тупуг", "evidence_proven": True, "reason": "чередование",
    }])
    finding = db.query(ConsiliumFinding).one()

    accept_finding(db, finding_id=finding.id, actor_uid="u1", actor_name="Владелец")

    rows = effective_attributions(db, ["ch:00358"])["ch:00358"]
    assert {(r.span_start, r.span_end, r.speaker) for r in rows} == {
        (0, 24, "Тупуг"), (24, 48, "Рассказчик"),
    }
    assert db.query(ConsiliumFinding).one().status == "accepted"


def test_accepting_renames_both_halves_of_a_reply_broken_by_a_remark(db):
    """Реплика героя разорвана ремаркой — обе её половины обязаны сменить голос.

    Форма `[X, Рассказчик, X]` — каждый пятый абзац книги (2788 из 13 276). Находка
    цепляется к самой длинной половине; если переименовать только её, вторая останется
    с отвергнутым именем, и ОДНА фраза ОДНОГО героя достанется двум актёрам. Такое
    находят у микрофона, когда студия уже оплачена.

    Проверка поэтому не «соседи целы», а «нигде в абзаце не осталось старого
    говорящего»: прежняя формулировка как раз и подписала дефект как норму.
    """
    from app.models import ConsiliumFinding
    from app.services.consilium_store import accept_finding, save_findings
    from app.v2.reader import effective_attributions

    text = ("— Ой-ей, — выдохнул хвьадзукилай, похожий на сушёный гриб с крылышками, "
            "и попятился к стене. — Он нас чуть не кокнул!")
    remark_at = text.index("— выдохнул")
    tail_at = text.index("— Он нас")
    _book_with_paragraph(db, text, [
        (0, remark_at, "Хвьадзукилай 2", 0.8),
        (remark_at, tail_at, "Рассказчик", 0.97),
        (tail_at, len(text), "Хвьадзукилай 2", 0.8),
    ])
    # Скрипт загрузки цепляет находку к самому длинному отрезку персонажа — здесь это
    # вторая половина фразы, а не первая.
    save_findings(db, book_id="b1", run_label="r", findings=[{
        "chapter_index": 39, "ordinal": 29, "segment_id": "ch:00358",
        "span_start": tail_at, "span_end": len(text), "kind": "wrong_voice",
        "current_speaker": "Хвьадзукилай 2", "readers_speaker": "Хвьадзукилай 1",
        "arbiter_verdict": "change", "arbiter_speaker": "Хвьадзукилай 1", "evidence_para": 28,
        "evidence_quote": "", "evidence_proven": False, "reason": "чередование",
    }])
    finding = db.query(ConsiliumFinding).one()

    accept_finding(db, finding_id=finding.id, actor_uid="u1", actor_name="Владелец")

    rows = effective_attributions(db, ["ch:00358"])["ch:00358"]
    assert "Хвьадзукилай 2" not in {r.speaker for r in rows}
    assert {(r.span_start, r.span_end, r.speaker) for r in rows} == {
        (0, remark_at, "Хвьадзукилай 1"),
        (remark_at, tail_at, "Рассказчик"),
        (tail_at, len(text), "Хвьадзукилай 1"),
    }
    # Ремарка — не предмет спора: её уверенность остаётся модельной.
    assert {(r.span_start, r.confidence) for r in rows} == {
        (0, 1.0), (remark_at, 0.97), (tail_at, 1.0),
    }


def test_accepting_leaves_another_character_in_the_paragraph_alone(db):
    """Переименование по имени, а не по всему абзацу: чужой голос рядом не задет.

    В книге есть абзац, где звучат двое РАЗНЫХ персонажей. «Переименовать всё» там
    отдало бы реплику соседа тому же актёру — ошибка ровно того же рода, что чинится.
    """
    from app.models import ConsiliumFinding
    from app.services.consilium_store import accept_finding, save_findings
    from app.v2.reader import effective_attributions

    text = "— Первый говорит своё слово. — А второй отвечает ему сразу же, не думая."
    second_at = text.index("— А второй")
    _book_with_paragraph(db, text, [
        (0, second_at, "Дгарнин", 0.8),
        (second_at, len(text), "Тупуг", 0.9),
    ])
    save_findings(db, book_id="b1", run_label="r", findings=[{
        "chapter_index": 25, "ordinal": 358, "segment_id": "ch:00358",
        "span_start": second_at, "span_end": len(text), "kind": "wrong_voice",
        "current_speaker": "Тупуг", "readers_speaker": "Хвьадзукилай 1",
        "arbiter_verdict": "change", "arbiter_speaker": "Хвьадзукилай 1", "evidence_para": 24,
        "evidence_quote": "", "evidence_proven": False, "reason": "чередование",
    }])
    finding = db.query(ConsiliumFinding).one()

    accept_finding(db, finding_id=finding.id, actor_uid="u1", actor_name="Владелец")

    rows = effective_attributions(db, ["ch:00358"])["ch:00358"]
    assert {(r.span_start, r.speaker) for r in rows} == {
        (0, "Дгарнин"), (second_at, "Хвьадзукилай 1"),
    }


def test_dismissing_leaves_the_markup_alone(db):
    from app.models import ConsiliumFinding
    from app.services.consilium_store import dismiss_finding, save_findings

    save_findings(db, book_id="b1", run_label="r", findings=[_finding(358)])
    finding = db.query(ConsiliumFinding).one()

    dismiss_finding(db, finding_id=finding.id, actor_uid="u1", actor_name="Владелец")

    row = db.query(ConsiliumFinding).one()
    assert row.status == "dismissed"
    assert row.decided_by == "Владелец"


def test_dismissing_an_already_decided_finding_is_refused(db):
    """«Оставить как есть» на принятой находке соврало бы в списке: разметка уже другая."""
    from app.models import ConsiliumFinding
    from app.services.consilium_store import dismiss_finding, save_findings

    save_findings(db, book_id="b1", run_label="r", findings=[_finding(358)])
    finding = db.query(ConsiliumFinding).one()
    finding.status = "accepted"
    db.flush()

    with pytest.raises(ValueError, match="finding_already_decided"):
        dismiss_finding(db, finding_id=finding.id, actor_uid="u1")


def test_a_decision_remembers_the_name_it_ended_with(db):
    """Без итога в «решённых» не видно, чем кончилось, — только в журнале."""
    from app.models import ConsiliumFinding
    from app.services.consilium_store import book_findings, dismiss_finding, save_findings

    save_findings(db, book_id="b1", run_label="r", findings=[_finding(358)])
    finding = db.query(ConsiliumFinding).one()
    dismiss_finding(db, finding_id=finding.id, actor_uid="u1", actor_name="Оператор")

    item = book_findings(db, book_id="b1")["items"][0]
    assert item["status"] == "dismissed"
    assert item["decided_speaker"] == "Дгарнин"
    assert item["decided_at"]


def test_the_list_carries_what_the_reader_needs_to_jump_and_show(db):
    """Переход между главами и строка списка: `chapter_id` и начало реплики."""
    from app.services.consilium_store import book_findings, save_findings

    _book_with_paragraph(db, "— Почему ты так уверен? — спросил он.", [(0, 24, "Дгарнин", 0.5)])
    save_findings(db, book_id="b1", run_label="r",
                  findings=[_finding(358), _finding(360, kind="narrator_border")])

    out = book_findings(db, book_id="b1")
    first = out["items"][0]
    assert first["chapter_id"] == "ch"
    assert first["excerpt"] == "— Почему ты так уверен?"
    assert out["counts"]["new_by_kind"] == {
        "wrong_voice": 1, "identity_play": 0, "narrator_border": 1, "readers_split": 0,
    }
    # Абзаца 360 в базе нет — строка не падает, а показывает пустое начало.
    assert out["items"][1]["excerpt"] == "" and out["items"][1]["chapter_id"] == ""


def test_accept_refuses_a_keep_current_verdict(db):
    """Арбитр сказал «оставить как есть» — приём не смеет откатиться на мнение чтецов.

    Пустое `arbiter_speaker` при `keep_current` — штатно, там нечего подставлять.
    `or` на имени чтецов в этом случае молча ставит роль, которую арбитр только что
    отверг. Различать нужно по вердикту, а не по пустоте строки.
    """
    import uuid

    from app.models import Character, ConsiliumFinding, ScriptBook
    from app.services.consilium_store import accept_finding, save_findings
    from app.time_utils import utcnow_naive
    from app.v2.models import V2Attribution, V2Segment

    db.add(ScriptBook(id="b1", title="Книга", source_filename="k.txt", source_format="txt",
                      created_at=utcnow_naive()))
    for name in ("Дгарнин", "Тупуг"):
        db.add(Character(id=str(uuid.uuid4()), book_id="b1", name=name, aliases="",
                         appears_in="", created_at=utcnow_naive()))
    db.add(V2Segment(id="ch:00358", book_id="b1", chapter_id="ch", ordinal=358,
                     kind="paragraph", text="- Почему ты так уверен? - и следом слова автора."))
    db.add(V2Attribution(id=str(uuid.uuid4()), segment_id="ch:00358", version=1,
                         speaker="Дгарнин", span_start=0, span_end=24, source="llm",
                         created_at=utcnow_naive()))
    db.flush()
    save_findings(db, book_id="b1", run_label="r", findings=[{
        "chapter_index": 25, "ordinal": 358, "segment_id": "ch:00358",
        "span_start": 0, "span_end": 24, "kind": "wrong_voice",
        "current_speaker": "Дгарнин", "readers_speaker": "Тупуг",
        "arbiter_verdict": "keep_current", "arbiter_speaker": "", "evidence_para": 356,
        "evidence_quote": "пообещал Тупуг", "evidence_proven": True, "reason": "чередование",
    }])
    finding = db.query(ConsiliumFinding).one()

    with pytest.raises(ValueError):
        accept_finding(db, finding_id=finding.id, actor_uid="u1", actor_name="Владелец")

    row = db.query(V2Attribution).filter(V2Attribution.segment_id == "ch:00358").one()
    assert row.version == 1
    assert row.speaker == "Дгарнин"
    assert db.query(ConsiliumFinding).one().status == "new"


@pytest.mark.parametrize("arbiter_speaker", ["", "Тупуг"])
def test_accept_refuses_an_undecidable_verdict(db, arbiter_speaker):
    """«Арбитр не смог решить» — не повод молча применить мнение чтецов.

    Спека понижает до `undecidable` всё, что не прошло проверку цитатой. Карточка
    говорит человеку «арбитр не смог», а кнопка ставила либо имя чтецов (пустой
    арбитр), либо имя, которое арбитр сам объявил недоказанным. Подсказка не вправе
    спорить сама с собой: это тот же класс, что и уже закрытый `keep_current`.
    """
    from app.models import ConsiliumFinding
    from app.services.consilium_store import accept_finding, save_findings
    from app.v2.models import V2Attribution

    _book_with_paragraph(db, "— Почему ты так уверен? — и следом слова автора.",
                         [(0, 24, "Дгарнин", 0.5)])
    save_findings(db, book_id="b1", run_label="r", findings=[{
        "chapter_index": 25, "ordinal": 358, "segment_id": "ch:00358",
        "span_start": 0, "span_end": 24, "kind": "wrong_voice",
        "current_speaker": "Дгарнин", "readers_speaker": "Тупуг",
        "arbiter_verdict": "undecidable", "arbiter_speaker": arbiter_speaker,
        "evidence_para": 356, "evidence_quote": "", "evidence_proven": False,
        "reason": "цитата не нашлась",
    }])
    finding = db.query(ConsiliumFinding).one()

    with pytest.raises(ValueError):
        accept_finding(db, finding_id=finding.id, actor_uid="u1", actor_name="Владелец")

    row = db.query(V2Attribution).filter(V2Attribution.segment_id == "ch:00358").one()
    assert (row.version, row.speaker) == (1, "Дгарнин")
    assert db.query(ConsiliumFinding).one().status == "new"


def test_accept_refuses_a_finding_that_was_already_decided(db):
    """Дважды принятая находка — это вторая вкладка со старым списком.

    Между приёмами человек мог поправить место руками; повторный приём вернул бы
    подсказку машины поверх решения человека, и никто бы этого не заметил.
    """
    from app.models import ConsiliumFinding
    from app.services.consilium_store import accept_finding, save_findings

    _book_with_paragraph(db, "— Почему ты так уверен? — и следом слова автора.",
                         [(0, 24, "Дгарнин", 0.5)])
    save_findings(db, book_id="b1", run_label="r", findings=[{
        "chapter_index": 25, "ordinal": 358, "segment_id": "ch:00358",
        "span_start": 0, "span_end": 24, "kind": "wrong_voice",
        "current_speaker": "Дгарнин", "readers_speaker": "Тупуг",
        "arbiter_verdict": "change", "arbiter_speaker": "Тупуг", "evidence_para": 356,
        "evidence_quote": "пообещал Тупуг", "evidence_proven": True, "reason": "чередование",
    }])
    finding = db.query(ConsiliumFinding).one()
    accept_finding(db, finding_id=finding.id, actor_uid="u1", actor_name="Владелец")

    with pytest.raises(ValueError):
        accept_finding(db, finding_id=finding.id, actor_uid="u1", actor_name="Владелец")


def test_accept_refuses_when_the_markup_already_moved_off_the_old_speaker(db):
    """На отрезке уже не та роль, о которой спорит находка, — пусть человек посмотрит заново.

    Границы совпадают, а имя другое: кто-то правил это место руками после прогона.
    Приём вслепую вернул бы подсказку машины поверх ручной правки.
    """
    from app.models import ConsiliumFinding
    from app.services.consilium_store import accept_finding, save_findings
    from app.v2.reader import effective_attributions

    _book_with_paragraph(db, "— Почему ты так уверен? — и следом слова автора.",
                         [(0, 24, "Дгарнин", 0.5)])
    save_findings(db, book_id="b1", run_label="r", findings=[{
        "chapter_index": 25, "ordinal": 358, "segment_id": "ch:00358",
        "span_start": 0, "span_end": 24, "kind": "wrong_voice",
        "current_speaker": "Дгарнин", "readers_speaker": "Тупуг",
        "arbiter_verdict": "change", "arbiter_speaker": "Тупуг", "evidence_para": 356,
        "evidence_quote": "пообещал Тупуг", "evidence_proven": True, "reason": "чередование",
    }])
    finding = db.query(ConsiliumFinding).one()
    # Ручная правка после прогона: те же границы, другой голос.
    from app.v2.attribution_ops import reassign_segment
    reassign_segment(db, segment_id="ch:00358",
                     spans=[{"start": 0, "end": 24, "speaker": "Пупип", "confidence": 1.0}],
                     actor_uid="u1", actor_name="Владелец")

    with pytest.raises(ValueError):
        accept_finding(db, finding_id=finding.id, actor_uid="u1", actor_name="Владелец")

    rows = effective_attributions(db, ["ch:00358"])["ch:00358"]
    assert [r.speaker for r in rows] == ["Пупип"]
    assert db.query(ConsiliumFinding).one().status == "new"


def test_accept_refuses_a_span_that_no_longer_matches_the_markup(db):
    """Границы находки разошлись с действующей разметкой — абзац переразметили между делом.

    Замена без проверки границ тихо запишет новую версию с теми же ролями, находка
    исчезнет из списка «принятой», а голос не изменится — человек узнает об этом
    только в студии.
    """
    import uuid

    from app.models import Character, ConsiliumFinding, ScriptBook
    from app.services.consilium_store import accept_finding, save_findings
    from app.time_utils import utcnow_naive
    from app.v2.models import V2Attribution, V2Segment

    db.add(ScriptBook(id="b1", title="Книга", source_filename="k.txt", source_format="txt",
                      created_at=utcnow_naive()))
    for name in ("Дгарнин", "Тупуг"):
        db.add(Character(id=str(uuid.uuid4()), book_id="b1", name=name, aliases="",
                         appears_in="", created_at=utcnow_naive()))
    db.add(V2Segment(id="ch:00358", book_id="b1", chapter_id="ch", ordinal=358,
                     kind="paragraph", text="- Почему ты так уверен? - и следом слова автора."))
    # Абзац успели переразметить: границы спорной реплики теперь другие (0..30, не 0..24).
    db.add(V2Attribution(id=str(uuid.uuid4()), segment_id="ch:00358", version=1,
                         speaker="Дгарнин", span_start=0, span_end=30, source="llm",
                         created_at=utcnow_naive()))
    db.flush()
    save_findings(db, book_id="b1", run_label="r", findings=[{
        "chapter_index": 25, "ordinal": 358, "segment_id": "ch:00358",
        "span_start": 0, "span_end": 24, "kind": "wrong_voice",
        "current_speaker": "Дгарнин", "readers_speaker": "Тупуг",
        "arbiter_verdict": "change", "arbiter_speaker": "Тупуг", "evidence_para": 356,
        "evidence_quote": "пообещал Тупуг", "evidence_proven": True, "reason": "чередование",
    }])
    finding = db.query(ConsiliumFinding).one()

    with pytest.raises(ValueError):
        accept_finding(db, finding_id=finding.id, actor_uid="u1", actor_name="Владелец")

    row = db.query(V2Attribution).filter(V2Attribution.segment_id == "ch:00358").one()
    assert row.version == 1
    assert row.speaker == "Дгарнин"
    assert db.query(ConsiliumFinding).one().status == "new"


def test_accept_touches_confidence_only_on_the_disputed_span(db):
    """Соседние реплики абзаца не должны терять исходную уверенность модели.

    `confidence=1.0` уместна только на отрезке, который человек только что подтвердил —
    у соседей она должна остаться прежней, эта цифра уходит в читалку.
    """
    import uuid

    from app.models import Character, ConsiliumFinding, ScriptBook
    from app.services.consilium_store import accept_finding, save_findings
    from app.time_utils import utcnow_naive
    from app.v2.models import V2Attribution, V2Segment
    from app.v2.reader import effective_attributions

    db.add(ScriptBook(id="b1", title="Книга", source_filename="k.txt", source_format="txt",
                      created_at=utcnow_naive()))
    for name in ("Дгарнин", "Тупуг"):
        db.add(Character(id=str(uuid.uuid4()), book_id="b1", name=name, aliases="",
                         appears_in="", created_at=utcnow_naive()))
    db.add(V2Segment(id="ch:00358", book_id="b1", chapter_id="ch", ordinal=358,
                     kind="paragraph",
                     text="- Почему ты так уверен? - и следом слова автора, и ещё сверху немного текста."))
    db.add(V2Attribution(id=str(uuid.uuid4()), segment_id="ch:00358", version=1,
                         speaker="Дгарнин", span_start=0, span_end=24, source="llm",
                         confidence=0.4, created_at=utcnow_naive()))
    db.add(V2Attribution(id=str(uuid.uuid4()), segment_id="ch:00358", version=1,
                         speaker="Рассказчик", span_start=24, span_end=48, source="llm",
                         confidence=0.9, created_at=utcnow_naive()))
    db.add(V2Attribution(id=str(uuid.uuid4()), segment_id="ch:00358", version=1,
                         speaker="Рассказчик", span_start=48, span_end=60, source="llm",
                         confidence=0.7, created_at=utcnow_naive()))
    db.flush()
    save_findings(db, book_id="b1", run_label="r", findings=[{
        "chapter_index": 25, "ordinal": 358, "segment_id": "ch:00358",
        "span_start": 0, "span_end": 24, "kind": "wrong_voice",
        "current_speaker": "Дгарнин", "readers_speaker": "Тупуг",
        "arbiter_verdict": "change", "arbiter_speaker": "Тупуг", "evidence_para": 356,
        "evidence_quote": "пообещал Тупуг", "evidence_proven": True, "reason": "чередование",
    }])
    finding = db.query(ConsiliumFinding).one()

    accept_finding(db, finding_id=finding.id, actor_uid="u1", actor_name="Владелец")

    rows = {(r.span_start, r.span_end): r.confidence
            for r in effective_attributions(db, ["ch:00358"])["ch:00358"]}
    assert rows[(0, 24)] == 1.0
    assert rows[(24, 48)] == 0.9
    assert rows[(48, 60)] == 0.7


def _undecidable(db, text, spans, **over):
    from app.models import ConsiliumFinding
    from app.services.consilium_store import save_findings

    _book_with_paragraph(db, text, spans)
    save_findings(db, book_id="b1", run_label="r", findings=[dict({
        "chapter_index": 25, "ordinal": 358, "segment_id": "ch:00358",
        "span_start": spans[0][0], "span_end": spans[0][1], "kind": "wrong_voice",
        "current_speaker": spans[0][2], "readers_speaker": "Тупуг",
        "arbiter_verdict": "undecidable", "arbiter_speaker": "Тупуг", "evidence_para": 0,
        "evidence_quote": "", "evidence_proven": False, "reason": "по смыслу",
    }, **over)])
    return db.query(ConsiliumFinding).one()


@pytest.mark.parametrize("verdict", ["undecidable", "keep_current"])
def test_a_name_from_the_human_is_applied_whatever_the_arbiter_said(db, verdict):
    """Арбитр не смог — решает человек. Его имя сильнее блока «не доказано»."""
    from app.services.consilium_store import accept_finding, book_findings
    from app.v2.reader import effective_attributions

    finding = _undecidable(db, "— Почему ты так уверен? — спросил он.",
                           [(0, 24, "Дгарнин", 0.5), (24, 37, "Рассказчик", 0.9)],
                           arbiter_verdict=verdict)

    out = accept_finding(db, finding_id=finding.id, actor_uid="u1", speaker="тупуг")

    rows = effective_attributions(db, ["ch:00358"])["ch:00358"]
    assert [r.speaker for r in rows] == ["Тупуг", "Рассказчик"]
    assert out["status"] == "accepted" and out["speaker"] == "Тупуг"
    assert book_findings(db, book_id="b1")["items"][0]["decided_speaker"] == "Тупуг"


@pytest.mark.parametrize("speaker", ["Никто", "UNSURE", ""])
def test_a_name_outside_the_cast_is_refused_and_nothing_is_written(db, speaker):
    from app.services.consilium_store import accept_finding
    from app.v2.reader import effective_attributions

    finding = _undecidable(db, "— Почему ты так уверен? — спросил он.",
                           [(0, 24, "Дгарнин", 0.5), (24, 37, "Рассказчик", 0.9)])

    with pytest.raises(ValueError, match="unknown_speaker"):
        accept_finding(db, finding_id=finding.id, actor_uid="u1", speaker=speaker or " ")

    rows = effective_attributions(db, ["ch:00358"])["ch:00358"]
    assert {r.version for r in rows} == {1}
    assert finding.status == "new"


def test_choosing_the_name_already_there_leaves_the_markup_and_dismisses(db):
    """«Оставить как в сценарии» — не новая версия абзаца, а отметка о решении."""
    from app.services.consilium_store import accept_finding
    from app.v2.reader import effective_attributions

    finding = _undecidable(db, "— Почему ты так уверен? — спросил он.",
                           [(0, 24, "Дгарнин", 0.5), (24, 37, "Рассказчик", 0.9)])

    out = accept_finding(db, finding_id=finding.id, actor_uid="u1", speaker="Дгарнин")

    assert out["status"] == "dismissed"
    assert finding.status == "dismissed" and finding.decided_speaker == "Дгарнин"
    assert {r.version for r in effective_attributions(db, ["ch:00358"])["ch:00358"]} == {1}


def test_the_narrator_can_be_named_by_the_human(db):
    from app.services.consilium_store import accept_finding
    from app.v2.reader import effective_attributions

    finding = _undecidable(db, "Прохожий, внутрь ты зайди", [(0, 25, "Дгарнин", 0.5)],
                           kind="narrator_border", readers_speaker="Рассказчик")

    accept_finding(db, finding_id=finding.id, actor_uid="u1", speaker="Рассказчик")

    assert [r.speaker for r in effective_attributions(db, ["ch:00358"])["ch:00358"]] == ["Рассказчик"]


def test_a_narrator_finding_does_not_rename_other_narrator_spans_in_the_paragraph(db):
    """Правило целого абзаца — про разорванную РЕПЛИКУ ГЕРОЯ, а не про рассказчика.

    В абзаце может звучать песня рассказчика и рядом — его же ремарка «— сказал он»:
    обе стоят как Рассказчик, но это не один разорванный кусок одной роли, как в
    случае героя. Находка о песне не вправе переименовать ремарку заодно — иначе
    «сказал он» ушёл бы тому же персонажу, что и песня.
    """
    from app.services.consilium_store import accept_finding
    from app.v2.reader import effective_attributions

    text = "Прохожий, внутрь ты зайди, — сказал он тихо."
    remark_at = text.index("— сказал")
    finding = _undecidable(db, text, [
        (0, remark_at, "Рассказчик", 0.6),
        (remark_at, len(text), "Рассказчик", 0.9),
    ], kind="narrator_border")

    accept_finding(db, finding_id=finding.id, actor_uid="u1", speaker="Дгарнин")

    rows = {(r.span_start, r.span_end): (r.speaker, r.source, r.confidence)
            for r in effective_attributions(db, ["ch:00358"])["ch:00358"]}
    assert rows[(0, remark_at)] == ("Дгарнин", "operator", 1.0)
    assert rows[(remark_at, len(text))] == ("Рассказчик", "llm", 0.9)


def test_accept_refuses_a_narrator_finding_whose_span_was_reassigned_by_hand(db):
    """Сторож устаревания находки обязан работать и в ветке рассказчика.

    Первая версия фикса #2 (`targets = hits` в ветке рассказчика) сделала `targets`
    тождественным `hits` — проверка `any(item in targets for item in hits)` после
    этого не может не сработать НИКОГДА, и ручная правка после прогона тихо
    перезаписывалась бы решением по устаревшей находке.
    """
    from app.services.consilium_store import accept_finding
    from app.v2.attribution_ops import reassign_segment
    from app.v2.reader import effective_attributions

    text = "Прохожий, внутрь ты зайди, — сказал он тихо."
    remark_at = text.index("— сказал")
    finding = _undecidable(db, text, [
        (0, remark_at, "Рассказчик", 0.6),
        (remark_at, len(text), "Рассказчик", 0.9),
    ], kind="narrator_border")
    # Ручная правка после прогона: те же границы, но уже другой голос на спорном отрезке.
    reassign_segment(db, segment_id="ch:00358",
                     spans=[{"start": 0, "end": remark_at, "speaker": "Дгарнин", "confidence": 1.0},
                            {"start": remark_at, "end": len(text), "speaker": "Рассказчик", "confidence": 0.9}],
                     actor_uid="u1", actor_name="Владелец")

    with pytest.raises(ValueError, match="speaker_already_changed"):
        accept_finding(db, finding_id=finding.id, actor_uid="u1", speaker="Тупуг")

    rows = effective_attributions(db, ["ch:00358"])["ch:00358"]
    assert {(r.span_start, r.speaker) for r in rows} == {(0, "Дгарнин"), (remark_at, "Рассказчик")}
    assert finding.status == "new"


def test_accept_refuses_a_character_finding_whose_span_was_reassigned_by_hand_with_a_human_name(db):
    """Тот же сторож, обычная роль: контроль, что ветка героя ведёт себя так же."""
    from app.services.consilium_store import accept_finding
    from app.v2.attribution_ops import reassign_segment
    from app.v2.reader import effective_attributions

    finding = _undecidable(db, "— Почему ты так уверен? — спросил он.",
                           [(0, 24, "Дгарнин", 0.5), (24, 37, "Рассказчик", 0.9)])
    # Ручная правка после прогона: те же границы, но уже другой голос на спорном отрезке.
    reassign_segment(db, segment_id="ch:00358",
                     spans=[{"start": 0, "end": 24, "speaker": "Пупип", "confidence": 1.0},
                            {"start": 24, "end": 37, "speaker": "Рассказчик", "confidence": 0.9}],
                     actor_uid="u1", actor_name="Владелец")

    with pytest.raises(ValueError, match="speaker_already_changed"):
        accept_finding(db, finding_id=finding.id, actor_uid="u1", speaker="Тупуг")

    rows = effective_attributions(db, ["ch:00358"])["ch:00358"]
    assert {(r.span_start, r.speaker) for r in rows} == {(0, "Пупип"), (24, "Рассказчик")}
    assert finding.status == "new"


def test_a_human_name_on_a_broken_reply_renames_both_halves_and_keeps_the_remark_source(db):
    """Разорванная реплика с именем от человека: старого говорящего не остаётся нигде,
    а ремарка сохраняет модельный источник и уверенность."""
    from app.services.consilium_store import accept_finding
    from app.v2.reader import effective_attributions

    text = "— Ой-ей, — выдохнул хвьадзукилай, и попятился. — Он нас чуть не кокнул!"
    remark_at, tail_at = text.index("— выдохнул"), text.index("— Он нас")
    finding = _undecidable(db, text, [
        (tail_at, len(text), "Хвьадзукилай 2", 0.8),
        (0, remark_at, "Хвьадзукилай 2", 0.8),
        (remark_at, tail_at, "Рассказчик", 0.97),
    ])

    accept_finding(db, finding_id=finding.id, actor_uid="u1", speaker="Хвьадзукилай 1")

    rows = effective_attributions(db, ["ch:00358"])["ch:00358"]
    assert "Хвьадзукилай 2" not in {r.speaker for r in rows}
    assert {(r.span_start, r.speaker, r.source, r.confidence) for r in rows} == {
        (0, "Хвьадзукилай 1", "operator", 1.0),
        (remark_at, "Рассказчик", "llm", 0.97),
        (tail_at, "Хвьадзукилай 1", "operator", 1.0),
    }


def test_apply_run_adds_updates_open_findings_and_never_touches_decided(db):
    from app.models import ConsiliumFinding
    from app.services.consilium_store import apply_run, save_findings

    save_findings(db, book_id="b1", run_label="r1",
                  findings=[_finding(358), _finding(360), _finding(362), _finding(364)])
    rows = {r.ordinal: r for r in db.query(ConsiliumFinding).all()}
    rows[360].status = "accepted"
    rows[362].status = "gone"
    db.flush()

    changed = dict(_finding(358), readers_speaker="Пупип", arbiter_speaker="Пупип", reason="новый спор")
    out = apply_run(db, book_id="b1", run_label="r2",
                    rows=[changed, dict(_finding(360), reason="не трогать"),
                          dict(_finding(362), reason="вернулось"), _finding(366)],
                    gone_keys=[(25, 364, 0, 24)])

    assert out == {"added": 1, "updated": 2, "gone": 1}
    rows = {r.ordinal: r for r in db.query(ConsiliumFinding).all()}
    assert (rows[358].readers_speaker, rows[358].status, rows[358].run_label) == ("Пупип", "new", "r2")
    assert (rows[360].status, rows[360].reason) == ("accepted", "чередование в диалоге")
    assert (rows[362].status, rows[362].reason) == ("new", "вернулось")
    assert rows[364].status == "gone" and rows[366].status == "new"


def test_gone_is_counted_and_cannot_be_decided(db):
    from app.models import ConsiliumFinding
    from app.services.consilium_store import book_findings, dismiss_finding, save_findings

    save_findings(db, book_id="b1", run_label="r", findings=[_finding(358)])
    row = db.query(ConsiliumFinding).one()
    row.status = "gone"
    db.flush()
    assert book_findings(db, book_id="b1")["counts"]["gone"] == 1
    with pytest.raises(ValueError, match="finding_already_decided"):
        dismiss_finding(db, finding_id=row.id, actor_uid="u1")


def test_gone_keys_leave_decided_findings_alone(db):
    from app.models import ConsiliumFinding
    from app.services.consilium_store import apply_run, save_findings

    save_findings(db, book_id="b1", run_label="r", findings=[_finding(358)])
    row = db.query(ConsiliumFinding).one()
    row.status = "dismissed"
    db.flush()
    out = apply_run(db, book_id="b1", run_label="r2", rows=[], gone_keys=[(25, 358, 0, 24)])
    assert out["gone"] == 0 and row.status == "dismissed"


def _decide_behind_the_sessions_back(db, ordinal: int, status: str) -> None:
    """Человек решил находку в другом запросе: база уже знает, а строка в сессии — ещё нет."""
    from sqlalchemy import update

    from app.models import ConsiliumFinding

    db.execute(update(ConsiliumFinding).where(ConsiliumFinding.ordinal == ordinal)
               .values(status=status).execution_options(synchronize_session=False))


def test_apply_run_does_not_resurrect_a_finding_decided_between_its_read_and_write(db):
    from app.models import ConsiliumFinding
    from app.services.consilium_store import apply_run, save_findings

    save_findings(db, book_id="b1", run_label="r1", findings=[_finding(358), _finding(360)])
    db.flush()
    held = db.query(ConsiliumFinding).all()  # строки уже в сессии; ссылка держит их в ней
    assert {r.status for r in held} == {"new"}
    _decide_behind_the_sessions_back(db, 358, "accepted")
    _decide_behind_the_sessions_back(db, 360, "dismissed")

    out = apply_run(db, book_id="b1", run_label="r2",
                    rows=[dict(_finding(358), reason="прогон")], gone_keys=[(25, 360, 0, 24)])
    db.flush()
    db.expire_all()
    rows = {r.ordinal: r for r in db.query(ConsiliumFinding).all()}
    assert out == {"added": 0, "updated": 0, "gone": 0}
    assert (rows[358].status, rows[358].reason, rows[358].run_label) == ("accepted", "чередование в диалоге", "r1")
    assert rows[360].status == "dismissed"

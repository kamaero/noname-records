"""Сводка студии: разделы за один проход (пакет A, 05.10)."""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import (
    AudioFile, AuditionRejection, BotContact, Character, RoleDeadline, ScriptBook, ScriptChapter, TelegramAuthAccount,
    User, UserRole,
)
from app.services import bot_status, bot_studio
from app.services.audio_uploads import derive_book_code
from app.services.role_deadlines import role_progress

NOW = datetime(2026, 10, 5, 9, 0, 0)
DUE = datetime(2026, 12, 31, 20, 59, 59)
KP, SV = "Карта Приозерья", "Сезон ветров. Книга 2"
CHATS = {"Ветрова Ольга": "41", "Иванов Пётр": "42"}


@pytest.fixture()
def db(monkeypatch):
    monkeypatch.setattr(bot_studio.Studio, "chat", lambda self, actor: CHATS.get(actor, ""))
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, autoflush=False)() as session:
        for bid, title in (("b1", KP), ("b2", SV)):
            session.add(ScriptBook(id=bid, title=title, display_title=title, source_filename="x", source_format="docx",
                                   total_chars=0, author_sheets_x1000=0, chapter_count=3, has_chapters="true",
                                   status="processing"))
        for n, title in ((1, "Глава 1"), (2, ""), (3, "Эпилог")):   # у второй главы нет названия
            session.add(ScriptChapter(id=f"b1-{n}", book_id="b1", chapter_index=n, chapter_title=title))
        session.add(ScriptChapter(id="b2-1", book_id="b2", chapter_index=1, chapter_title="Глава 1"))
        cast = [
            ("c1", "b1", "Убув", "Ветрова Ольга", "1,2,3,9"),   # глава 9 в книге нет
            ("c2", "b1", "Хоркон", "Иванов Пётр", "1"),         # молчит: сдано 0
            ("c3", "b1", "Сталин", "Иванов Пётр?", "3"),        # позвали, пробы нет
            ("c4", "b1", "Дракон", "", "2"),                    # свободна, пробы ждут
            ("c5", "b1", "Ге'Коон", "Ветрова Ольга", "1"),      # отдана Ветровой
            ("c6", "b2", "Ныкфо", "Ветрова Ольга", "1"),
        ]
        for cid, bid, name, actor, chapters in cast:
            session.add(Character(id=cid, book_id=bid, name=name, actor_name=actor, appears_in=chapters))
        for cid, actor, kind, due in (("c1", "Ветрова Ольга", "role", DUE), ("c2", "Иванов Пётр", "role", NOW - timedelta(hours=1)),
                                      ("c3", "Иванов Пётр", "audition", NOW + timedelta(days=1)),
                                      ("c6", "Ветрова Ольга", "role", NOW + timedelta(days=10))):
            session.add(RoleDeadline(character_id=cid, book_id="b1" if cid != "c6" else "b2", actor_name=actor,
                                     kind=kind, due_at=due, created_at=NOW))
        code = derive_book_code(KP)
        files = [
            ("take", "Убув", "Глава 1", "Ветрова Ольга", NOW),
            ("take", "Убув", "Глава 2", "Ветрова Ольга", NOW),   # глава без названия — метка «Глава 2»
            ("take", "Убув", "Эпилог", "Иванов Пётр", NOW),      # чужой дубль не в счёт
            ("audition", "дракон", "", "Иванов Пётр", NOW - timedelta(days=9)),
            ("audition", "Дракон", "", "Иванов Пётр", NOW - timedelta(days=2)),
            ("audition", "Дракон", "", "Unknown", NOW - timedelta(days=1)),
            ("audition", "Ге'Коон", "", "Иванов Пётр", NOW - timedelta(days=3)),  # роль отдана — не ждёт
        ]
        for i, (kind, role, chapter, actor, at) in enumerate(files):
            session.add(AudioFile(book_code=code, original_filename=f"{i}.wav", stored_key=f"k/{i}",
                                  mime_type="audio/wav", size_bytes=1, chapter=chapter, role=role,
                                  actor_name=actor, kind=kind, uploaded_at=at))
        session.add(BotContact(telegram_user_id="42", tg_name="Пётр", username="petr",
                               first_seen_at=NOW - timedelta(days=5), last_seen_at=NOW - timedelta(days=2)))
        session.commit()
        yield session


def test_progress_matches_role_progress(db):
    studio = bot_studio.Studio(db, now=NOW)
    for ch in db.query(Character).all():
        actor = (ch.actor_name or "").rstrip("?")
        assert studio.progress(ch, actor) == role_progress(db, ch, actor), ch.name


def test_pending_groups_by_pair_oldest_first_and_skips_taken_roles(db):
    pending = bot_studio.Studio(db, now=NOW).pending()
    assert [(p.role, p.actor, (NOW - p.first_at).days) for p in pending] == [
        ("Дракон", "Иванов Пётр", 9), ("Дракон", "Unknown", 1)]


def test_a_sent_rejection_takes_the_pair_out_of_pending(db):
    db.add(AuditionRejection(book_id="b1", book_code=derive_book_code(KP), role="Дракон", actor_name="Иванов Пётр",
                             due_at=NOW, status="sent", sent_at=NOW))
    db.commit()
    assert [p.actor for p in bot_studio.Studio(db, now=NOW).pending()] == ["Unknown"]


def test_deadlines_split_overdue_and_soon(db):
    overdue, soon = bot_studio.Studio(db, now=NOW).deadlines()
    assert [(d.actor, d.kind, d.role) for d in overdue] == [("Иванов Пётр", "role", "Хоркон")]
    assert [(d.actor, d.kind, d.role) for d in soon] == [("Иванов Пётр", "audition", "Сталин")]


def test_books_count_fully_recorded_roles_out_of_roles_with_chapters(db):
    """Владелец 05.10: «137 из 453 глав-ролей» непонятно — показываем «записано: X/Y», где X —
    роли, записанные во всех своих главах, Y — роли книги, у которых есть главы (роль без
    глав записать нельзя, и с ней счётчик никогда не дошёл бы до конца)."""
    db.add(AudioFile(book_code=derive_book_code(KP), original_filename="g.wav", stored_key="k/g", mime_type="audio/wav",
                     size_bytes=1, chapter="Глава 1", role="Ге'Коон", actor_name="Ветрова Ольга", kind="take",
                     uploaded_at=NOW))
    db.add(Character(id="c8", book_id="b1", name="Без глав", actor_name="Ветрова Ольга", appears_in=""))
    db.commit()
    books = {b.title: b for b in bot_studio.Studio(db, now=NOW).books()}
    kp = books[KP]
    # Ге'Коон записана целиком; Убув 2 из 3 — ещё нет; «Без глав» не в счёте
    assert (kp.recorded, kp.roles) == (1, 5)
    assert (kp.approved, kp.free) == (3, 2)   # Сталин (со «?») и Дракон свободны
    assert (books[SV].recorded, books[SV].roles) == (0, 1)


def test_silent_groups_by_person_with_last_bot_visit(db):
    silent = bot_studio.Studio(db, now=NOW).silent()
    petr = next(s for s in silent if s.actor == "Иванов Пётр")
    assert petr.chat == "42" and petr.last_seen == NOW - timedelta(days=2)
    assert petr.items == ["role «Хоркон» («Карта Приозерья») — not started",
                          "audition «Сталин» («Карта Приозерья») — not uploaded"]
    # Ольга: Убув начата, Ге'Коон без открытого срока, Ныкфо — в СВ-2, где запись не начата
    assert all(s.actor != "Ветрова Ольга" for s in silent)


def test_overview_counts_and_buttons(db):
    text, markup = bot_studio.overview(db, now=NOW)
    assert "Auditions awaiting a decision: 2 (oldest: 9 days)" in text
    assert "Deadlines: overdue 1 · upcoming 1" in text
    assert "Карта Приозерья — recorded: 0/5" in text
    assert "Inactive: 1" in text
    data = [b["callback_data"] for row in markup["inline_keyboard"] for b in row]
    assert data == ["ss:p", "ss:d", "ss:b", "ss:q"]


def test_sections_read_like_lists(db):
    assert "• «Дракон» — Иванов Пётр, 9 days" in bot_studio.section(db, "p", now=NOW)
    assert "• «Дракон» — unnamed, 1 days" in bot_studio.section(db, "p", now=NOW)
    deadlines = bot_studio.section(db, "d", now=NOW)
    assert "Overdue" in deadlines and "• Иванов Пётр — role «Хоркон» («Карта Приозерья»)" in deadlines
    assert "• Иванов Пётр — audition «Сталин» («Карта Приозерья»), due 6 October, 12:00 MSK" in deadlines
    books = bot_studio.section(db, "b", now=NOW)
    assert "• «Карта Приозерья» — recorded: 0/5 · approved: 3 · available: 2" in books
    quiet = bot_studio.section(db, "q", now=NOW)
    assert "Иванов Пётр (@petr) — last bot visit 2 days ago" in quiet
    studio = bot_studio.Studio(db, now=NOW)
    # без слов, выдающих род: не «не заходил(а)»
    assert bot_studio._seen(bot_studio.Silent("Ветрова Ольга", "41", None), studio) == "no bot visits"
    assert bot_studio._seen(bot_studio.Silent("Без Телеграма", "", None), studio) == "Telegram not found"
    assert bot_studio.section(db, "zz", now=NOW) == ""


def test_a_long_section_fits_one_message(db):
    for i in range(400):
        db.add(AudioFile(book_code=derive_book_code(KP), original_filename=f"p{i}.wav", stored_key=f"p/{i}",
                         mime_type="audio/wav", size_bytes=1, chapter="", role=f"Роль {i}",
                         actor_name="Иванов Пётр", kind="audition", uploaded_at=NOW))
    db.commit()
    text = bot_studio.section(db, "p", now=NOW)
    assert len(text) <= 4096 and "the full list is on the website" in text




def _accounts(db, monkeypatch):
    monkeypatch.setattr(bot_status, "chat_for", lambda db, actor: CHATS.get(actor, ""))
    for tid, name in (("41", "Ветрова Ольга"), ("42", "Иванов Пётр"), ("43", "Петрова Ольга")):
        db.add(User(id=f"u{tid}", login=f"tg_{tid}", password_hash="x", display_name=name, is_active="true"))
        db.add(UserRole(user_id=f"u{tid}", role="dictor"))
        db.add(TelegramAuthAccount(telegram_user_id=tid, role="dictor", access_scope="full", display_name=name,
                                   is_active="true", user_id=f"u{tid}"))
    db.add(Character(id="c9", book_id="b1", name="Химес", actor_name="Без Телеграма", appears_in="1"))
    db.commit()


def test_one_match_gives_the_card(db, monkeypatch):
    _accounts(db, monkeypatch)
    text, markup = bot_studio.dictor_reply(db, "Иванов", now=NOW)
    assert markup is None
    assert "Иванов Пётр" in text and "<code>tg_42</code>" in text and "@petr" in text
    assert "«Хоркон» — «Карта Приозерья»: completed 0 of 1 chapter" in text
    assert "awaiting audition" in text and "awaiting your audition" not in text
    assert "Password" not in text


def test_an_account_and_its_cast_name_are_one_person(db, monkeypatch):
    _accounts(db, monkeypatch)
    assert bot_studio.find_people(db, "Пётр") == [bot_studio.Person(name="Иванов Пётр", tid="42")]


def test_several_matches_give_buttons(db, monkeypatch):
    _accounts(db, monkeypatch)
    # одно слово в запросе совпадает только с целым словом имени (`names_match`):
    # «Ольга» находит обеих, «Ветров» не нашёл бы «Ветрову»
    text, markup = bot_studio.dictor_reply(db, "Ольга", now=NOW)
    data = [b["callback_data"] for row in markup["inline_keyboard"] for b in row]
    assert sorted(data) == ["dc:t:41", "dc:t:43"]
    assert "Who would you like to view" in text
    assert "Петрова Ольга" in bot_studio.card_by_pick(db, "dc:t:43", now=NOW)


def test_a_cast_only_actor_gets_a_card_without_telegram(db, monkeypatch):
    _accounts(db, monkeypatch)
    text, markup = bot_studio.dictor_reply(db, "Без Телеграма", now=NOW)
    assert markup is None and "No Telegram account linked" in text and "«Химес»" in text


def test_nobody_and_empty_query(db, monkeypatch):
    _accounts(db, monkeypatch)
    assert bot_studio.dictor_reply(db, "Нетакого", now=NOW)[0] == "No match for «Нетакого»."
    assert bot_studio.dictor_reply(db, "  ", now=NOW)[0] == "Enter: /dictor Surname"


def test_studio_chat_index_agrees_with_chat_for():
    """`Studio.chat` — индекс учёток одним запросом вместо `chat_for` на каждого (2,6 с на
    94 молчащих на проде 05.10). Обязан решать так же: одна учётка — её чат, две — никому,
    выключенная не в счёт, имя связанного пользователя считается."""
    from app.services.role_deadlines import chat_for
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, autoflush=False)() as session:
        session.add(User(id="u1", login="tg_1", password_hash="x", display_name="Шелвар Шарита", is_active="true"))
        rows = [("1", "Бранти Бурнел", "u1", "true"), ("2", "Ветрова Ольга", "", "true"),
                ("3", "Петрова Ольга", "", "true"), ("4", "Иванов Пётр", "", "false"), ("5", "Цертан Царели", "", "true")]
        for tid, name, user_id, active in rows:
            session.add(TelegramAuthAccount(telegram_user_id=tid, role="dictor", access_scope="full",
                                            display_name=name, is_active=active, user_id=user_id))
        session.commit()
        studio = bot_studio.Studio(session, now=NOW)
        for actor in ("Шелвар Шарита", "Бранти Бурнел", "Ольга", "Ветрова Ольга", "Иванов Пётр",
                      "Царели Цертан", "Панков", "", "Никто"):
            assert studio.chat(actor) == chat_for(session, actor), actor


def test_a_cast_name_is_merged_only_into_the_account_it_points_to(db, monkeypatch):
    """Ревью 05.10: учётка «Мария» (в Telegram часто одно имя) поглощала актёра каста
    «Иванова Мария» без Telegram — карточка «Марии» была пустой, а настоящие роли не
    показывались никому. Имя каста сливается с учёткой, только если указывает на неё (`chat_for`)."""
    _accounts(db, monkeypatch)
    db.add(TelegramAuthAccount(telegram_user_id="77", role="dictor", access_scope="full", display_name="Мария",
                               is_active="true"))
    db.add(Character(id="c10", book_id="b1", name="Медиге", actor_name="Иванова Мария", appears_in="1"))
    db.commit()
    people = bot_studio.find_people(db, "Иванова Мария")
    assert bot_studio.Person(name="Иванова Мария", tid="") in people
    cast_pick = next(row[0]["callback_data"] for row in bot_studio.dictor_reply(db, "Иванова Мария", now=NOW)[1]["inline_keyboard"]
                     if row[0]["callback_data"].startswith("dc:c:"))
    assert "«Медиге»" in bot_studio.card_by_pick(db, cast_pick, now=NOW)


def test_silent_skips_roles_in_books_where_recording_has_not_started(db):
    """Владелец 05.10: «Молчат: 94» — почти все утверждённые на книги, где запись ещё не
    начиналась. «Роль не начата» — только по книгам, где есть хотя бы один дубль; несданная
    проба — по любой книге: у неё срок 48 часов."""
    db.add(Character(id="c11", book_id="b2", name="Нина", actor_name="Иванов Пётр?", appears_in="1"))
    db.add(RoleDeadline(character_id="c11", book_id="b2", actor_name="Иванов Пётр", kind="audition",
                        due_at=NOW + timedelta(days=1), created_at=NOW))
    db.commit()
    silent = {s.actor: s.items for s in bot_studio.Studio(db, now=NOW).silent()}
    assert "Ветрова Ольга" not in silent          # Ныкфо в СВ-2, где дублей нет
    assert "audition «Нина» («Сезон ветров. Книга 2») — not uploaded" in silent["Иванов Пётр"]


def test_silent_says_taken_but_not_uploaded(db):
    db.query(RoleDeadline).filter_by(character_id="c3").update({"accepted_at": NOW})
    db.commit()
    petr = next(s for s in bot_studio.Studio(db, now=NOW).silent() if s.actor == "Иванов Пётр")
    assert "audition «Сталин» («Карта Приозерья») — accepted, not uploaded" in petr.items

"""Письмо человеку не должно уезжать владельцу.

`OWNER_TELEGRAM_ID` подменяет адресата у всех уведомлений — так задумано: сообщения о
ходе работ идут владельцу и не разлетаются по студии. Но «ты утверждён на роль»
адресовано конкретному диктору, и та же подмена превратила бы его в очередное
сообщение владельца самому себе, а диктор так ничего и не узнал бы.

Заодно здесь заперт список ролей, по которому ищутся дикторы книги: он стоял на
`dictor_pro`/`dictor_neo` — ролях, которых не носила ни одна учётка, — и не находил
никого.
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.db import Base
from app.models import Character, ScriptBook, TelegramAuthAccount
from app.services import telegram as tg
from app.time_utils import utcnow_naive


class _Response:
    status_code = 200


@pytest.fixture()
def sent(monkeypatch):
    """Ловим адресатов вместо того, чтобы писать в Telegram."""
    box: list[dict] = []
    monkeypatch.setattr(settings, "telegram_notify_enabled", True)
    monkeypatch.setattr(settings, "telegram_bot_token", "test-token")
    monkeypatch.setattr(settings, "owner_telegram_id", "900000100")
    monkeypatch.setattr(tg.requests, "post", lambda url, json, timeout: box.append(json) or _Response())
    return box


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        yield session


def test_an_ordinary_notice_still_goes_to_the_owner(db, sent):
    tg.send_telegram_message(db, "книга обработана", chat_ids=["900000102"])

    assert [item["chat_id"] for item in sent] == ["900000100"]


def test_a_direct_message_reaches_the_person_it_is_addressed_to(db, sent):
    tg.send_telegram_message(db, "ты утверждён на роль", chat_ids=["900000102"], direct=True)

    assert [item["chat_id"] for item in sent] == ["900000102"]


def test_a_direct_message_with_nobody_to_send_to_sends_nothing(db, sent):
    assert tg.send_telegram_message(db, "текст", chat_ids=[], direct=True) == 0
    assert sent == []


def test_the_cast_of_a_book_is_found_by_the_role_dictors_actually_have(db):
    """Фильтр стоял на `dictor_pro`/`dictor_neo`, и уведомления касту не доходили ни до кого."""
    book = ScriptBook(title="Крылья полумрака", source_filename="k.txt", source_format="txt", created_at=utcnow_naive())
    db.add(book)
    db.flush()
    db.add(Character(book_id=book.id, name="Дгарнин", actor_name="Роман Сомов"))
    db.add(TelegramAuthAccount(telegram_user_id="111", role="dictor", display_name="Роман Сомов", is_active="true"))
    db.flush()

    assert tg.resolve_cast_chat_ids(db, book.id) == ["111"]



def test_a_merely_suggested_actor_is_not_called_to_the_microphone(db):
    """«Книга готова, занимайте места у микрофонов» — рассылка утверждённому касту.

    Предложение агента утверждением не является, а `names_match` знака вопроса не
    видит. Без проверки предварительное имя превращалось бы в настоящее письмо
    человеку, которого ещё не выбрали.
    """
    book = ScriptBook(title="Крылья полумрака", source_filename="k.txt", source_format="txt", created_at=utcnow_naive())
    db.add(book)
    db.flush()
    db.add(Character(book_id=book.id, name="Химера", actor_name="Натали Ким?"))
    db.add(TelegramAuthAccount(telegram_user_id="222", role="dictor", display_name="Натали Ким", is_active="true"))
    db.flush()

    assert tg.resolve_cast_chat_ids(db, book.id) == []

"""Автор узнаёт о пробах на роль — лично, а не через владельца.

Пробы на роль адресованы автору книги: он решает, чей голос подходит его героям. Но
`OWNER_TELEGRAM_ID` нарочно подменяет адресата у всех уведомлений о ходе работ, и без
`direct` письмо автору превратилось бы в ещё одно сообщение владельца самому себе, а автор
так и не узнал бы, что кто-то пробуется.

Второе, что здесь заперто: дубли автору не уходят. Он не следит за ходом записи — его
касаются только заявки на роли.
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.db import Base
from app.models import TelegramAuthAccount
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
        session.add(TelegramAuthAccount(id="t1", telegram_user_id="900000106", role="author",
                                        display_name="Александр Белозёров", is_active="true",
                                        user_id="u1", created_at=utcnow_naive()))
        session.add(TelegramAuthAccount(id="t2", telegram_user_id="900000102", role="dictor",
                                        display_name="Сергей Зотов", is_active="true",
                                        user_id="u2", created_at=utcnow_naive()))
        session.commit()
        yield session


def _audition(role: str, actor: str = "Сергей Зотов") -> dict:
    return {"chapter": "", "role": role, "actor_name": actor,
            "original_filename": "proba.wav", "kind": "audition"}


def _take(chapter: str, role: str) -> dict:
    return {"chapter": chapter, "role": role, "actor_name": "Гончаров Иван",
            "original_filename": "take.wav", "kind": "take"}


def test_the_author_hears_about_an_audition(db, sent):
    """Письмо уходит автору, а не владельцу: иначе подмена адресата съела бы его."""
    from app.services.recording import notify_author_about_auditions

    notify_author_about_auditions(db, [_audition("Хубол")], user_name="Сергей Зотов")

    assert [item["chat_id"] for item in sent] == ["900000106"]
    assert "Хубол" in sent[0]["text"]


def test_takes_are_not_the_authors_business(db, sent):
    """Дубли — ход записи; автор на него не подписан."""
    from app.services.recording import notify_author_about_auditions

    notify_author_about_auditions(db, [_take("12", "Гамук")], user_name="Гончаров Иван")

    assert sent == []


def test_a_mixed_batch_tells_the_author_only_about_the_auditions(db, sent):
    """В смешанной пачке автору уходят пробы и ничего кроме них."""
    from app.services.recording import notify_author_about_auditions

    notify_author_about_auditions(db, [_take("12", "Гамук"), _audition("Хубол")],
                                  user_name="Сергей Зотов")

    assert len(sent) == 1
    assert "Хубол" in sent[0]["text"]
    assert "Гамук" not in sent[0]["text"]


def test_an_inactive_author_account_gets_nothing(db, sent):
    """Отключённая учётка — закрытая дверь, а не молчаливая доставка в никуда."""
    from app.services.recording import notify_author_about_auditions

    db.query(TelegramAuthAccount).filter(TelegramAuthAccount.id == "t1").one().is_active = "false"
    db.flush()

    notify_author_about_auditions(db, [_audition("Хубол")], user_name="Сергей Зотов")

    assert sent == []

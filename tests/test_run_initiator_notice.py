from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import TelegramAuthAccount, User
from app.worker_tasks import _initiator_notice


def test_result_goes_only_to_active_initiator_telegram(monkeypatch):
    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as db:
        db.add_all([
            User(id="one", login="one", password_hash="", display_name="One", is_active="true"),
            User(id="two", login="two", password_hash="", display_name="Two", is_active="true"),
            TelegramAuthAccount(telegram_user_id="101", user_id="one", role="admin", is_active="true"),
            TelegramAuthAccount(telegram_user_id="202", user_id="two", role="admin", is_active="true"),
            TelegramAuthAccount(telegram_user_id="303", user_id="one", role="admin", is_active="false"),
        ])
        db.commit()
    sent = []
    monkeypatch.setattr("app.services.telegram.send_telegram_message", lambda db, text, **kw: sent.append((text, kw)))
    _initiator_notice(factory, "one")("готово")
    assert sent == [("готово", {"chat_ids": ["101"], "direct": True})]
    assert _initiator_notice(factory, "") is None

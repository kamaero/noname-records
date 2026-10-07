"""Порог лимита трат — тем же проходом раз в 5 минут, одно письмо владельцу на порог."""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base


def test_the_pass_tells_the_owner_once(monkeypatch):
    import scripts.run_deadlines as script
    from app.config import settings
    from tests.spend_helpers import exhaust_month

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    monkeypatch.setattr(script, "SessionLocal", factory)
    monkeypatch.setattr(settings, "owner_telegram_id", "900000001")
    sent = []
    monkeypatch.setattr(script, "send_telegram_message",
                        lambda db, text, chat_ids=None, direct=False: sent.append((chat_ids, text)) or 1)
    exhaust_month(factory)
    script.main()
    script.main()
    spend_letters = [s for s in sent if "лимита" in s[1]]
    assert len(spend_letters) == 1 and spend_letters[0][0] == ["900000001"]

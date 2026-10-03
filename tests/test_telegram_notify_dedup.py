import app.services.telegram as tg


class FakeBook:
    def __init__(self):
        self.id = "b1"
        self.title = "Крылья Полумрака"
        self.display_title = "Крылья Полумрака"
        self.last_notified_status = ""


class FakeDB:
    def add(self, _obj):
        pass


def _capture(monkeypatch):
    sent: list[str] = []
    monkeypatch.setattr(tg, "send_telegram_message", lambda db, text, **kw: (sent.append(text), 1)[1])
    return sent


def test_transient_states_never_notify(monkeypatch):
    sent = _capture(monkeypatch)
    b, db = FakeBook(), FakeDB()
    assert tg.notify_book_status_change(db, b, "queued", "processing") == 0
    assert tg.notify_book_status_change(db, b, "processing", "stalled") == 0
    assert sent == []


def test_success_once_then_deduped_across_oscillation(monkeypatch):
    sent = _capture(monkeypatch)
    b, db = FakeBook(), FakeDB()
    assert tg.notify_book_status_change(db, b, "processing", "author_review") == 1
    assert b.last_notified_status == "author_review"
    assert "✅" in sent[-1]
    # recovery oscillation author_review<->stalled must NOT re-ping
    assert tg.notify_book_status_change(db, b, "author_review", "stalled") == 0
    assert tg.notify_book_status_change(db, b, "stalled", "author_review") == 0
    assert len(sent) == 1


def test_failed_once_then_deduped(monkeypatch):
    sent = _capture(monkeypatch)
    b, db = FakeBook(), FakeDB()
    assert tg.notify_book_status_change(db, b, "processing", "failed") == 1
    assert "❌" in sent[-1]
    assert tg.notify_book_status_change(db, b, "failed", "processing") == 0
    assert tg.notify_book_status_change(db, b, "processing", "failed") == 0
    assert len(sent) == 1


def test_rearm_allows_fresh_run_to_report(monkeypatch):
    sent = _capture(monkeypatch)
    b, db = FakeBook(), FakeDB()
    assert tg.notify_book_status_change(db, b, "processing", "failed") == 1
    b.last_notified_status = ""  # ensure_pipeline_run re-arms on a new run
    assert tg.notify_book_status_change(db, b, "processing", "failed") == 1
    assert len(sent) == 2

"""Кому бот может написать, а кому нет.

Вход через кнопку «Log in with Telegram» и уведомления бота — разные каналы, и их легко
спутать. Виджет работает без всякого бота: Telegram сам подтверждает личность. А писать
человеку бот не может, пока тот сам не нажал Start, — так устроен Telegram, это защита
от рассылок.

Значит человек может прекрасно входить и при этом не получать ни «ты утверждён на
роль», ни «не нашлось трёх реплик». Молча: отправка вернёт ноль, и никто не заметит.
Проверка отвечает на этот вопрос заранее.
"""
import pytest

from app.services.bot_reach import check_bot_reach


class _Answer:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def _ok(first="Жека", last="", username="NightPilot"):
    return _Answer({"ok": True, "result": {"first_name": first, "last_name": last, "username": username}})


def _no(description="Bad Request: chat not found"):
    return _Answer({"ok": False, "description": description})


@pytest.fixture()
def rows():
    return [
        {"telegram_user_id": "900000107", "display_name": "Старик Ворчалыч", "linked_login": "evgeniy.ostapovich"},
        {"telegram_user_id": "900000111", "display_name": "Литвиненко Константин", "linked_login": "konstantin.litvinenko"},
    ]


class TestWhatItReports:
    def test_a_reachable_person_is_marked_so(self, rows, monkeypatch):
        monkeypatch.setattr("app.services.bot_reach.requests.get", lambda *a, **k: _ok())

        report = check_bot_reach(rows, token="t")

        assert all(item["reachable"] for item in report["items"])
        assert report["unreachable"] == 0

    def test_an_unreachable_one_is_named_with_the_reason(self, rows, monkeypatch):
        monkeypatch.setattr("app.services.bot_reach.requests.get", lambda *a, **k: _no())

        report = check_bot_reach(rows, token="t")

        assert report["unreachable"] == 2
        assert all(item["reason"] == "Bad Request: chat not found" for item in report["items"])

    def test_it_shows_the_name_telegram_itself_knows(self, rows, monkeypatch):
        """«Старик Ворчалыч» в whitelist и «Жека» в телеграме — один человек; видеть надо оба."""
        monkeypatch.setattr("app.services.bot_reach.requests.get", lambda *a, **k: _ok(first="Жека", username="NightPilot"))

        report = check_bot_reach(rows, token="t")
        item = next(x for x in report["items"] if x["telegram_user_id"] == "900000107")

        assert item["display_name"] == "Старик Ворчалыч"
        assert item["telegram_name"] == "Жека"
        assert item["username"] == "NightPilot"

    def test_the_unreachable_come_first(self, rows, monkeypatch):
        answers = iter([_ok(), _no()])
        monkeypatch.setattr("app.services.bot_reach.requests.get", lambda *a, **k: next(answers))

        report = check_bot_reach(rows, token="t")

        assert report["items"][0]["reachable"] is False


class TestWhenItCannotAsk:
    def test_without_a_token_nothing_is_claimed(self, rows):
        report = check_bot_reach(rows, token="")

        assert report["checked"] == 0
        assert report["error"] == "no_token"

    def test_a_network_failure_is_not_a_verdict(self, rows, monkeypatch):
        """«Не дозвонились до Telegram» — не то же, что «боту нельзя писать этому человеку»."""
        def explode(*args, **kwargs):
            raise OSError("сеть")

        monkeypatch.setattr("app.services.bot_reach.requests.get", explode)

        item = next(x for x in check_bot_reach(rows, token="t")["items"] if x["telegram_user_id"] == "900000107")

        assert item["reachable"] is None
        assert "не удалось" in item["reason"].lower()

    def test_nobody_to_check(self):
        assert check_bot_reach([], token="t") == {"items": [], "checked": 0, "unreachable": 0, "error": ""}

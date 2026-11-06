"""Ручки «Сметы» и выгрузка в Excel (06.10)."""
import asyncio
import io
import json
from datetime import date

import openpyxl
import pytest

from app.services import payouts
from tests.payouts_fixture import NOW, db, same_session  # noqa: F401 — фикстура


class _Request:
    def __init__(self, body=None):
        self._body = body or {}


@pytest.fixture()
def api(db, monkeypatch):
    from app.api import payouts as api_module
    monkeypatch.setattr(api_module, "SessionLocal", lambda: same_session(db))
    monkeypatch.setattr(api_module, "is_authenticated", lambda request: True)
    monkeypatch.setattr(api_module, "session_display_name", lambda request: "Фернос Фарита")
    monkeypatch.setattr(api_module, "is_owner_request", lambda db, request: True)
    return api_module


def test_export_has_two_sheets_with_numbers_and_totals(db):
    data = payouts.collect(db, now=NOW)
    book = openpyxl.load_workbook(io.BytesIO(payouts.export_xlsx(data, today=date(2026, 10, 6))))
    assert book.sheetnames == ["Дикторы", "Роли"]
    people = book["Дикторы"]
    assert [c.value for c in people[1]][:3] == ["Диктор", "Telegram", "К оплате"]
    total = [c.value for c in people[people.max_row]]
    assert total[0] == "Итого" and total[2] == data["totals"]["owed"]
    assert isinstance(people.cell(row=2, column=3).value, int)
    roles = book["Роли"]
    assert roles.max_row - 1 == sum(len(p["roles"]) for p in data["people"])


def test_everyone_but_the_owner_is_refused(api, monkeypatch):
    monkeypatch.setattr(api, "is_owner_request", lambda db, request: False)
    assert api.api_payouts(_Request()).status_code == 403
    assert api.api_payouts_pay(_Request(), {"kind": "final"}).status_code == 403
    assert api.api_payouts_export(_Request()).status_code == 403


def test_the_owner_sees_pays_and_downloads(api, db):
    body = json.loads(api.api_payouts(_Request()).body)
    assert body["ok"] and body["people"]
    refused = api.api_payouts_pay(_Request(), {"book_id": "b1", "role_key": "c2",
                                                        "actor_name": "Ветрова Ольга", "kind": "final"})
    assert refused.status_code == 400 and json.loads(refused.body)["error"] == "nothing_to_pay"
    paid = api.api_payouts_pay(_Request(), {"book_id": "b1", "role_key": "c1",
                                                     "actor_name": "Ветрова Ольга", "kind": "final"})
    assert paid.status_code == 200 and json.loads(paid.body)["written"] == [{"kind": "final", "amount_rub": 12500}]
    person = next(p for p in payouts.collect(db, now=NOW)["people"] if p["telegram_user_id"] == "41")
    advance = api.api_payouts_person_advance(_Request(), {"person_key": person["key"], "amount_rub": 1000,
                                                                   "note": "аванс"})
    assert advance.status_code == 200
    gone = api.api_payouts_cancel(_Request(), {"payment_id": "nope"})
    assert gone.status_code == 404
    file = api.api_payouts_export(_Request())
    assert file.status_code == 200 and file.media_type.startswith("application/vnd.openxmlformats")
    assert 'smeta-' in file.headers["content-disposition"]


def test_export_never_writes_a_formula(db):
    """Проверка безопасности: openpyxl пишет строку с «=» как формулу, а имя диктора приходит
    и из его представления боту. Текст, похожий на формулу, — текстом, с апострофом."""
    from app.models import Character
    db.get(Character, "c2").name = '=HYPERLINK("http://evil","Хоркон")'
    db.get(Character, "c1").actor_name = "+Ветрова Ольга"
    db.commit()
    data = payouts.collect(db, now=NOW)
    book = openpyxl.load_workbook(io.BytesIO(payouts.export_xlsx(data, today=date(2026, 10, 6))))
    for sheet in book.worksheets:
        for row in sheet.iter_rows():
            for cell in row:
                assert cell.data_type != "f", (sheet.title, cell.coordinate, cell.value)
    roles = [str(c.value or "") for row in book["Роли"].iter_rows() for c in row]
    assert any(v.startswith("=HYPERLINK") for v in roles)   # текстом, data_type 's' — проверено выше


def test_person_advance_and_cancel_also_refuse_strangers(api, monkeypatch):
    monkeypatch.setattr(api, "is_owner_request", lambda db, request: False)
    assert api.api_payouts_person_advance(_Request(), {"person_key": "x", "amount_rub": 1}).status_code == 403
    assert api.api_payouts_cancel(_Request(), {"payment_id": "x"}).status_code == 403


def test_export_text_is_a_string_cell_without_a_visible_apostrophe(db):
    from app.models import Character
    db.get(Character, "c2").name = "=1+1"
    db.commit()
    book = openpyxl.load_workbook(io.BytesIO(payouts.export_xlsx(payouts.collect(db, now=NOW), today=date(2026, 10, 6))))
    cells = [c for row in book["Роли"].iter_rows() for c in row if str(c.value or "").endswith("1+1")]
    assert cells and cells[0].value == "=1+1" and cells[0].data_type == "s"

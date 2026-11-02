"""«Смета»: суммы, сданность, люди (06.10)."""
import json

from app.models import BookBudget, Character, CharacterBudgetSnapshot, ScriptBook, ScriptChapter
from app.services import payouts
from tests.payouts_fixture import KP, NOW, SV, db  # noqa: F401 — фикстура


def test_role_fee_matches_the_cast_budget_row(db):
    """Сумма роли — та же, что строка сметы каста (настоящая ручка): сторож против расхождения."""
    from app.api.budget_api import build_budget_api_handlers

    handlers = build_budget_api_handlers({
        "is_authenticated": lambda _r: True, "has_workspace_full_access": lambda _r: True,
        "has_any_role": lambda _r, _roles: True, "is_agent": lambda _r: False,
        "session_payload": lambda _r: {"uid": "u11"}, "session_roles": lambda _r: {"admin"},
        "session_display_name": lambda _r: "tester", "SessionLocal": db.factory,
        "get_book": lambda s, book_id: s.query(ScriptBook).filter(ScriptBook.id == book_id).first(),
        "build_character_style_maps": lambda _c: None, "format_dt": lambda v: str(v) if v else None,
        "BookBudget": BookBudget, "Character": Character, "CharacterBudgetSnapshot": CharacterBudgetSnapshot,
        "ScriptChapter": ScriptChapter,
    })
    rows = json.loads(handlers["api_book_budget"](object(), "b1").body)["characters"]
    budget = {("narrator" if r["is_narrator"] else r["character_id"]): r["total_rub"] for r in rows}
    fees = {r["role_key"]: r["fee"] for r in payouts.role_fees(db, db.get(ScriptBook, "b1"))}
    assert fees and all(fee == (budget[key] or None) for key, fee in fees.items())
    assert fees["c1"] == 12500   # 12 000 + дубль 500 — как в смете каста and fees["narrator"] == 100000 and fees["c4"] is None and "c3" not in fees


def test_states_people_and_totals(db):
    data = payouts.collect(db, now=NOW)
    olga = next(p for p in data["people"] if p["telegram_user_id"] == "41")
    states = {(r["book_title"], r["role_name"]): r["state"] for r in olga["roles"]}
    assert states == {(KP, "Убув"): "done", (KP, "Хоркон"): "in_progress", (SV, "Ныкфо"): "in_progress"}
    assert olga["owed"] == 12500 and olga["in_work"] == 8000
    assert olga["name"] == "Ветрова Ольга" and olga["telegram_url"] == "https://t.me/olga"
    narrator = next(p for p in data["people"] if p["name"] == "Примервсммчпфиб Примерубщркгюд")
    assert narrator["roles"][0]["state"] == "done" and narrator["owed"] == 100000 and narrator["telegram_url"] == ""
    petr = next(p for p in data["people"] if p["name"] == "Иванов Пётр")
    assert [r["state"] for r in petr["roles"]] == ["no_fee"] and petr["owed"] == 0
    assert data["totals"]["owed"] == 112500 and data["totals"]["in_work"] == 8000
    assert [p["name"] for p in data["people"]][:2] == ["Примервсммчпфиб Примерубщркгюд", "Ветрова Ольга"]


def test_collect_does_not_write_merged_budget_numbers(db):
    """Свёртка сметы складывает суммы дублей в объекты снапшотов; «Смета» коммитит выплаты в
    той же сессии — сложенные числа не должны уйти в базу."""
    payouts.collect(db, now=NOW)
    db.commit()
    db.expire_all()
    assert db.query(CharacterBudgetSnapshot).filter_by(character_id="c1").one().total_rub == 12000

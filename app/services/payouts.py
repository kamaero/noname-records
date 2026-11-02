"""Раздел «Смета» (спека 2026-10-06-payouts): кто из дикторов что записал и сколько ему
заплатить — только владельцу.

Доступ — по учётке владельца (к ней привязан `OWNER_TELEGRAM_ID`), а не по строке имени:
учётку уже переименовывали («Примерфчяблеяд Примербиебипге» → «Примеряфеицшпчб Примерщюагоэсгв»), а тёзка с тем же именем получил
бы деньги студии на виду.
"""
from __future__ import annotations

import threading
from types import SimpleNamespace

from app.auth import is_owner_telegram, session_payload
from app.config import settings
from app.models import (
    BookBudget, BotContact, Character, CharacterBudgetSnapshot, FeePayment, ScriptBook, TelegramAuthAccount, User,
)
from app.services.budget_rows import collapse_budget_character_rows
from app.services.cast_budget import DEFAULT_NARRATOR_FEE_RUB
from app.services.role_deadlines import same_person
from app.services.shared_runtime import NARRATOR_DISPLAY_NAME, is_narrator_name
from app.v2.cast_ops import parse_actor_name


def owner_user_id(db) -> str:
    owner = str(settings.owner_telegram_id or "").strip()
    if not owner:
        return ""
    row = (db.query(TelegramAuthAccount)
           .filter(TelegramAuthAccount.telegram_user_id == owner, TelegramAuthAccount.is_active == "true").first())
    if row is not None and str(row.user_id or "").strip():
        return str(row.user_id)
    user = db.query(User).filter(User.login == f"tg_{owner}").first()
    return str(user.id) if user is not None else ""


def is_owner_request(db, request) -> bool:
    if not str(settings.owner_telegram_id or "").strip():
        return False
    if is_owner_telegram(request):
        return True
    uid = str(session_payload(request).get("uid") or "")
    return bool(uid) and uid == owner_user_id(db)


# ---------- суммы, сданность, люди ----------

NARRATOR_KEY = "narrator"


def _snapshot_copy(row: CharacterBudgetSnapshot) -> SimpleNamespace:
    """Свёртка сметы складывает суммы дублей прямо в объект снапшота. Ручке сметы это не
    страшно — после свёртки она не коммитит; «Смета» коммитит выплаты в той же сессии, и
    сложенные числа ушли бы в базу. Поэтому свёртке — копии."""
    return SimpleNamespace(character_id=row.character_id, lines_count=int(row.lines_count or 0),
                           approx_seconds=int(row.approx_seconds or 0), total_rub=int(row.total_rub or 0),
                           fact_seconds=int(getattr(row, "fact_seconds", 0) or 0),
                           calc_mode=str(getattr(row, "calc_mode", "") or ""))


def role_fees(db, book) -> list[dict]:
    """Роли книги с утверждённым актёром и их суммы — как в смете каста: снапшот строки,
    свёрнутой `collapse_budget_character_rows`; Рассказчик — из сметы книги. Сторож —
    `test_role_fee_matches_the_cast_budget_row` (сравнение с настоящей ручкой сметы)."""
    characters = db.query(Character).filter(Character.book_id == book.id).order_by(Character.name.asc()).all()
    snapshots = [_snapshot_copy(s) for s in
                 db.query(CharacterBudgetSnapshot).filter(CharacterBudgetSnapshot.book_id == book.id).all()]
    rows, snap = collapse_budget_character_rows(characters, snapshots)
    budget = db.query(BookBudget).filter(BookBudget.book_id == book.id).first()
    out = []
    for row in rows:
        if is_narrator_name(str(row.get("name") or "")) or is_narrator_name(str(row.get("aliases") or "")):
            continue  # Рассказчик — ниже, из сметы книги, как в смете каста
        actor, tentative = parse_actor_name(str(row.get("actor_name") or ""))
        if not actor or tentative:
            continue
        s = snap.get(str(row["id"]))
        fee = int(s.total_rub or 0) if s is not None else 0
        out.append({"book_id": book.id, "role_key": str(row["id"]), "role_name": str(row["name"]).strip(),
                    "actor_name": actor, "fee": fee or None, "appears_in": str(row.get("appears_in") or ""),
                    "is_narrator": False})
    narrator, _ = parse_actor_name(str(getattr(budget, "narrator_actor_name", "") or ""))
    if narrator:
        fee = int(budget.narrative_cost_rub or 0) if budget is not None else DEFAULT_NARRATOR_FEE_RUB
        out.append({"book_id": book.id, "role_key": NARRATOR_KEY, "role_name": NARRATOR_DISPLAY_NAME,
                    "actor_name": narrator, "fee": fee or None, "appears_in": "", "is_narrator": True})
    return out


def _narrator_progress(studio, book_id: str, actor: str) -> tuple[int, int]:
    """Рассказчик сдан, когда в каждой главе книги есть его дубль роли «Рассказчик»."""
    labels = studio.labels.get(book_id, {})
    takes = studio.takes.get((studio.code_of[book_id], NARRATOR_DISPLAY_NAME), [])
    recorded = {chapter for chapter, who in takes if same_person(who, actor)}
    return sum(1 for label in labels.values() if label in recorded), len(labels)


def _telegram_url(db, tid: str) -> str:
    """Ник — `t.me`; без ника — ссылка, открывающая чат в приложении по id."""
    if not tid:
        return ""
    contact = db.get(BotContact, tid)
    nick = str(getattr(contact, "username", "") or "").strip()
    return f"https://t.me/{nick}" if nick else f"tg://user?id={tid}"


def _account_name(db, tid: str) -> str:
    account = (db.query(TelegramAuthAccount)
               .filter(TelegramAuthAccount.telegram_user_id == tid, TelegramAuthAccount.is_active == "true").first())
    user = db.get(User, str(account.user_id)) if account is not None and account.user_id else None
    return str(getattr(user, "display_name", "") or getattr(account, "display_name", "") or "").strip()


def _state(fee, done: int, total: int) -> str:
    if total == 0:
        return "no_chapters"
    if fee is None:
        return "no_fee"
    return "done" if done >= total else "in_progress"


def _new_person(db, key: str, name: str, tid: str) -> dict:
    return {"key": key, "name": name, "telegram_user_id": tid, "telegram_url": _telegram_url(db, tid), "names": [name],
            "roles": [], "person_advances": [], "owed": 0, "paid": 0, "advances": 0, "in_work": 0, "credit": 0,
            "offsets": 0, "unearned": 0}


def _person_for(db, people: dict, studio, actor: str) -> dict:
    """Один человек под разными написаниями — одна карточка: по учётке (`chat`, правило писем),
    без неё — по `same_person` с уже собранными именами."""
    tid = studio.chat(actor)
    if tid:
        key = f"tg:{tid}"
        if key not in people:
            people[key] = _new_person(db, key, _account_name(db, tid) or actor, tid)
        person = people[key]
    else:
        person = next((p for p in people.values() if not p["telegram_user_id"]
                       and any(same_person(n, actor) for n in p["names"])), None)
        if person is None:
            key = f"name:{actor}"
            people[key] = person = _new_person(db, key, actor, "")
    if actor not in person["names"]:
        person["names"].append(actor)
    return person


def collect(db, *, now) -> dict:
    """Всё для страницы и выгрузки — одна сборка, чтобы файл не разошёлся с экраном."""
    from app.services.bot_studio import Studio

    studio = Studio(db, now=now)
    people: dict[str, dict] = {}
    for book in sorted(studio.book_by_id.values(), key=lambda b: str(b.title or "").lower()):
        for fee in role_fees(db, book):
            if fee["is_narrator"]:
                done, total = _narrator_progress(studio, book.id, fee["actor_name"])
            else:
                done, total = studio.progress(SimpleNamespace(book_id=book.id, name=fee["role_name"],
                                                              appears_in=fee["appears_in"]), fee["actor_name"])
            state = _state(fee["fee"], done, total)
            person = _person_for(db, people, studio, fee["actor_name"])
            person["roles"].append({
                "book_id": book.id, "book_title": str(book.title or ""), "role_key": fee["role_key"],
                "role_name": fee["role_name"], "actor_name": fee["actor_name"], "fee": fee["fee"], "done": done,
                "total": total, "state": state, "payments": [], "paid": 0,
                "owed": int(fee["fee"]) if state == "done" else 0, "overpaid": 0,
            })
    _apply_payments(db, people, studio)
    return _finish(people)


ROLE_KINDS = ("advance", "final", "offset")


def _payment_view(row: FeePayment) -> dict:
    from app.time_utils import iso_utc

    return {"id": row.id, "kind": row.kind, "amount_rub": int(row.amount_rub or 0), "paid_at": iso_utc(row.paid_at),
            "note": str(row.note or ""), "paid_by": str(row.paid_by or "")}


def _same_payee(row: FeePayment, person: dict, actor: str) -> bool:
    """Чья выплата. Telegram записан у выплаты и известен у человека — решает только он:
    нечёткое имя путает «Ольгу» и «Ольгу Ветрову» (ревью). Без Telegram — `same_person`."""
    row_tid = str(row.telegram_user_id or "").strip()
    if row_tid and person["telegram_user_id"]:
        return row_tid == person["telegram_user_id"]
    return same_person(str(row.actor_name or ""), actor)


def _payee(db, people: dict, row: FeePayment) -> dict:
    """Карточка для выплаты, чья роль больше не видна (или аванса без роли): по Telegram,
    иначе по имени у людей без Telegram; не нашлась — своя карточка."""
    tid = str(row.telegram_user_id or "").strip()
    name = str(row.actor_name or "")
    if tid:
        key = f"tg:{tid}"
        if key not in people:
            people[key] = _new_person(db, key, _account_name(db, tid) or name, tid)
        return people[key]
    person = next((p for p in people.values() if name in p["names"]), None) or next(
        (p for p in people.values() if any(same_person(n, name) for n in p["names"])), None)
    if person is None:
        key = f"name:{name}"
        people[key] = person = _new_person(db, key, name, "")
    return person


def _past_line(person: dict, row: FeePayment, titles: dict) -> dict:
    """Роль, по которой платили, но которой у человека больше нет: отдали другому, сняли,
    удалили книгу. Выплаты остаются за ним (спека) и считаются в «выплачено» и зачётах —
    иначе зачтённый ими аванс «воскресал» и тратился второй раз (ревью)."""
    for line in person["roles"]:
        if line["state"] == "past" and line["book_id"] == row.book_id and line["role_key"] == row.role_key:
            return line
    line = {"book_id": row.book_id, "book_title": titles.get(row.book_id) or "книга удалена", "role_key": row.role_key,
            "role_name": str(row.role_name or ""), "actor_name": str(row.actor_name or ""), "fee": None, "done": 0,
            "total": 0, "state": "past", "payments": [], "paid": 0, "owed": 0, "overpaid": 0}
    person["roles"].append(line)
    return line


def _apply_payments(db, people: dict, studio) -> None:
    """Журнал — на роли и людей. Выплата роли — к текущей строке той же книги, роли и того же
    получателя; нет такой строки — к «прошлой роли» получателя. Аванс без роли — человеку."""
    titles = {bid: str(b.title or "") for bid, b in studio.book_by_id.items()}
    rows = db.query(FeePayment).order_by(FeePayment.paid_at.asc()).all()
    attached: dict[int, list] = {}
    for row in rows:
        if row.kind not in ROLE_KINDS:
            continue
        target = None
        for person in list(people.values()):
            for line in person["roles"]:
                if line["state"] != "past" and line["book_id"] == row.book_id and line["role_key"] == row.role_key \
                        and _same_payee(row, person, line["actor_name"]):
                    target = line
                    break
            if target is not None:
                break
        if target is None:
            target = _past_line(_payee(db, people, row), row, titles)
        attached.setdefault(id(target), []).append(row)
    for row in rows:
        if row.kind == "person_advance":
            _payee(db, people, row)["person_advances"].append(_payment_view(row))
    for person in people.values():
        offsets = money = 0
        for line in person["roles"]:
            mine = attached.get(id(line), [])
            line["payments"] = [_payment_view(r) for r in mine]
            line["paid"] = sum(int(r.amount_rub or 0) for r in mine)
            if line["state"] == "done":
                line["owed"] = max(0, int(line["fee"]) - line["paid"])
                line["overpaid"] = max(0, line["paid"] - int(line["fee"]))
            offsets += sum(int(r.amount_rub or 0) for r in mine if r.kind == "offset")
            money += sum(int(r.amount_rub or 0) for r in mine if r.kind in ("advance", "final"))
        given = sum(a["amount_rub"] for a in person["person_advances"])
        person["offsets"] = offsets
        person["credit"] = max(0, given - offsets)
        person["paid"] = money + given
        person["advances"] = sum(line["paid"] for line in person["roles"] if line["state"] == "in_progress") \
            + person["credit"]


def _finish(people: dict) -> dict:
    for person in people.values():
        person["in_work"] = sum(int(r["fee"] or 0) for r in person["roles"] if r["state"] == "in_progress")
        roles_owed = sum(r["owed"] for r in person["roles"])
        person["owed"] = max(0, roles_owed - person["credit"])
        person["unearned"] = max(0, person["credit"] - roles_owed)
        person.pop("names", None)
    ordered = sorted(people.values(), key=lambda p: (-p["owed"], p["name"].lower().replace("ё", "е")))
    totals = {key: sum(p[key] for p in ordered) for key in ("owed", "paid", "advances", "in_work", "credit",
                                                             "unearned")}
    return {"totals": totals, "people": ordered}


# ---------- выплаты ----------

#: «прочитал остаток — проверил — записал» под одним замком: две быстрые «Выплачено» в
#: потоках сайта иначе обе прочли бы старый остаток (ревью). Uvicorn один — замка процесса хватает.
WRITE_LOCK = threading.Lock()

#: потолок одной записи — защита от опечатки и от переполнения при вставке
MAX_AMOUNT_RUB = 10_000_000


def _amount(value) -> int:
    """Сумма — целое число рублей. `1000.9` не обрезаем молча, `true` — не единица (ревью)."""
    if isinstance(value, bool):
        raise PayoutError("bad_amount")
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, str) and value.strip().replace(" ", "").isdigit():
        value = int(value.strip().replace(" ", ""))
    if not isinstance(value, int) or not 0 < value <= MAX_AMOUNT_RUB:
        raise PayoutError("bad_amount")
    return value


class PayoutError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _find(data: dict, *, book_id: str, role_key: str, actor_name: str) -> tuple[dict, dict]:
    for person in data["people"]:
        for line in person["roles"]:
            if line["book_id"] == book_id and line["role_key"] == role_key and same_person(line["actor_name"], actor_name):
                return person, line
    raise PayoutError("role_not_found")


def _audit(db, action: str, by: str, now, **payload) -> None:
    import json

    from app.models import AuditLog

    db.add(AuditLog(user_id="", entity_type="fee_payment", entity_id=str(payload.get("payment_id") or ""),
                    action=action, created_at=now, payload_json=json.dumps({"by": by, **payload}, ensure_ascii=False)))


def pay_role(db, *, book_id: str, role_key: str, actor_name: str, kind: str, amount_rub=None, by: str, now) -> list:
    """Аванс по роли в работе (не больше остатка) или выплата остатка сданной роли — сначала
    зачётом незачтённого аванса без роли, потом деньгами. Не коммитит."""
    person, line = _find(collect(db, now=now), book_id=book_id, role_key=role_key, actor_name=actor_name)
    if line["fee"] is None:
        raise PayoutError("no_fee")
    remainder = int(line["fee"]) - int(line["paid"])
    base = {"book_id": book_id, "role_key": role_key, "role_name": line["role_name"], "actor_name": line["actor_name"],
            "telegram_user_id": person["telegram_user_id"], "paid_by": str(by or "")[:120], "paid_at": now}
    rows = []
    if kind == "advance":
        if line["state"] != "in_progress":
            raise PayoutError("not_in_progress")
        amount = _amount(amount_rub)
        if amount > remainder:
            raise PayoutError("bad_amount")
        rows.append(FeePayment(kind="advance", amount_rub=amount, **base))
    elif kind == "final":
        if line["state"] != "done" or remainder <= 0:
            raise PayoutError("nothing_to_pay")
        offset = min(int(person["credit"]), remainder)
        if offset > 0:
            rows.append(FeePayment(kind="offset", amount_rub=offset, **base))
        if remainder - offset > 0:
            rows.append(FeePayment(kind="final", amount_rub=remainder - offset, **base))
    else:
        raise PayoutError("bad_kind")
    for row in rows:
        db.add(row)
    _audit(db, "payout_pay", by, now, book_id=book_id, role_key=role_key, actor_name=line["actor_name"],
           parts=[[r.kind, r.amount_rub] for r in rows])
    db.flush()
    return rows


def person_advance(db, *, person_key: str, amount_rub, note: str, by: str, now) -> FeePayment:
    """Аванс человеку без привязки к роли — зачитывается при оплате его сданных ролей."""
    person = next((p for p in collect(db, now=now)["people"] if p["key"] == person_key), None)
    if person is None:
        raise PayoutError("person_not_found")
    amount = _amount(amount_rub)
    row = FeePayment(kind="person_advance", amount_rub=amount, actor_name=person["name"],
                     telegram_user_id=person["telegram_user_id"], note=str(note or "")[:200],
                     paid_by=str(by or "")[:120], paid_at=now)
    db.add(row)
    _audit(db, "payout_pay", by, now, actor_name=person["name"], parts=[["person_advance", amount]])
    db.flush()
    return row


def cancel(db, payment_id: str, *, by: str, now) -> None:
    """Отменить одну строку журнала (ошибочная отметка). Отмена зачёта возвращает аванс в
    «незачтено» и снова открывает остаток роли."""
    row = db.get(FeePayment, str(payment_id or ""))
    if row is None:
        raise PayoutError("not_found")
    if row.kind == "person_advance":
        # Аванс, уже закрывший роли зачётом, нельзя убрать раньше зачёта: роль осталась бы
        # закрытой несуществующим авансом, и долг спрятался бы (ревью).
        for person in collect(db, now=now)["people"]:
            if any(a["id"] == row.id for a in person["person_advances"]):
                given = sum(a["amount_rub"] for a in person["person_advances"])
                if given - int(row.amount_rub or 0) < person["offsets"]:
                    raise PayoutError("advance_offset")
    _audit(db, "payout_cancel", by, now, payment_id=row.id, kind=row.kind, amount_rub=row.amount_rub,
           actor_name=row.actor_name, book_id=row.book_id, role_key=row.role_key)
    db.delete(row)
    db.flush()


# ---------- выгрузка ----------

STATE_WORDS = {"done": "сдана", "in_progress": "в работе", "no_chapters": "нет глав", "no_fee": "сумма не посчитана",
               "past": "прошлая роль (снята или книга удалена)"}
KIND_WORDS = {"advance": "аванс", "final": "выплата", "offset": "зачёт аванса", "person_advance": "аванс без роли"}
MONEY_FORMAT = '# ##0 "₽"'


def _text(value) -> str:
    return "" if value is None else str(value)


def _date(iso: str) -> str:
    return f"{iso[8:10]}.{iso[5:7]}" if iso and len(iso) >= 10 else ""


def export_xlsx(data: dict, *, today) -> bytes:
    """Excel из той же сборки, что страница: суммы — числами, чтобы складывались."""
    import io

    from openpyxl import Workbook

    book = Workbook()
    book.properties.title = f"Смета на {today:%d.%m.%Y}"
    people = book.active
    people.title = "Дикторы"
    people.append(["Диктор", "Telegram", "К оплате", "Выплачено", "Авансы по ролям в работе",
                   "Аванс без роли: выдано", "Аванс без роли: не зачтено", "В работе"])
    for person in data["people"]:
        given = sum(a["amount_rub"] for a in person["person_advances"])
        role_advances = person["advances"] - person["credit"]
        people.append([_text(person["name"]), _text(person["telegram_url"]), person["owed"], person["paid"],
                       role_advances, given, person["credit"], person["in_work"]])
    t = data["totals"]
    people.append(["Итого", "", t["owed"], t["paid"], t["advances"] - t["credit"],
                   sum(sum(a["amount_rub"] for a in p["person_advances"]) for p in data["people"]), t["credit"],
                   t["in_work"]])
    roles = book.create_sheet("Роли")
    roles.append(["Диктор", "Книга", "Роль", "Сдано глав", "Всего глав", "Состояние", "Сумма роли", "Выплачено",
                  "К оплате", "Выплаты"])
    for person in data["people"]:
        for line in person["roles"]:
            history = "; ".join(f"{KIND_WORDS.get(p['kind'], p['kind'])} {p['amount_rub']:,} ₽ {_date(p['paid_at'])}"
                                .replace(",", " ") for p in line["payments"])
            roles.append([_text(person["name"]), _text(line["book_title"]), _text(line["role_name"]), line["done"],
                          line["total"], STATE_WORDS.get(line["state"], line["state"]), line["fee"], line["paid"],
                          line["owed"], _text(history)])
    # Имя диктора приходит и из его представления боту: «=HYPERLINK(…)» openpyxl записал бы
    # формулой у того, кто откроет файл (проверка безопасности 06.10). Тип «строка» — без
    # видимого апострофа (ревью).
    for sheet in (people, roles):
        for row in sheet.iter_rows():
            for cell in row:
                if cell.data_type == "f":
                    cell.data_type = "s"
    for sheet, money_columns in ((people, "CDEFGH"), (roles, "GHI")):
        for column in money_columns:
            for cell in sheet[column][1:]:
                cell.number_format = MONEY_FORMAT
        for column_cells in sheet.columns:
            width = max(len(str(cell.value or "")) for cell in column_cells)
            sheet.column_dimensions[column_cells[0].column_letter].width = min(60, max(10, width + 2))
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()

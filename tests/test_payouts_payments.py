"""«Смета»: выплаты, авансы на роль и без роли, зачёт (06.10)."""
import pytest

from app.models import CharacterBudgetSnapshot, FeePayment
from app.services import payouts
from tests.payouts_fixture import NOW, db  # noqa: F401 — фикстура


def _person(db, tid="41"):
    return next(p for p in payouts.collect(db, now=NOW)["people"] if p["telegram_user_id"] == tid)


def _role(person, name):
    return next(r for r in person["roles"] if r["role_name"] == name)


def _pay(db, role_key, kind, amount=None):
    return payouts.pay_role(db, book_id="b1", role_key=role_key, actor_name="Ветрова Ольга", kind=kind,
                            amount_rub=amount, by="Фернос Фарита", now=NOW)


def test_advance_on_a_role_in_progress_then_final_pays_the_rest(db):
    _pay(db, "c2", "advance", 2000)
    kardash = _role(_person(db), "Хоркон")
    assert kardash["paid"] == 2000 and kardash["owed"] == 0 and kardash["state"] == "in_progress"
    assert _person(db)["advances"] == 2000 and _person(db)["paid"] == 2000
    with pytest.raises(payouts.PayoutError) as error:            # больше остатка (5000 − 2000)
        _pay(db, "c2", "advance", 4000)
    assert error.value.code == "bad_amount"
    with pytest.raises(payouts.PayoutError):                     # окончательно — только сданную
        _pay(db, "c2", "final")
    with pytest.raises(payouts.PayoutError):                     # аванс — только по роли в работе
        _pay(db, "c1", "advance", 100)
    rows = _pay(db, "c1", "final")
    assert [(r.kind, r.amount_rub) for r in rows] == [("final", 12500)]
    assert _person(db)["owed"] == 0
    with pytest.raises(payouts.PayoutError):                     # второй раз платить нечего
        _pay(db, "c1", "final")


def test_person_advance_is_offset_when_a_role_is_paid(db):
    person = _person(db)
    payouts.person_advance(db, person_key=person["key"], amount_rub=5000, note="наличными", by="К", now=NOW)
    before = _person(db)
    assert before["credit"] == 5000 and before["owed"] == 12500 - 5000 and before["paid"] == 5000
    rows = _pay(db, "c1", "final")
    assert [(r.kind, r.amount_rub) for r in rows] == [("offset", 5000), ("final", 7500)]
    after = _person(db)
    assert after["credit"] == 0 and after["owed"] == 0 and after["paid"] == 12500   # зачёт — не деньги


def test_an_advance_bigger_than_earnings(db):
    payouts.person_advance(db, person_key=_person(db)["key"], amount_rub=50000, note="", by="К", now=NOW)
    after = _person(db)
    assert after["owed"] == 0 and after["unearned"] == 50000 - 12500


def test_cancelling_an_offset_reopens_the_role(db):
    payouts.person_advance(db, person_key=_person(db)["key"], amount_rub=5000, note="", by="К", now=NOW)
    rows = _pay(db, "c1", "final")
    payouts.cancel(db, rows[0].id, by="К", now=NOW)
    after = _person(db)
    assert after["credit"] == 5000 and _role(after, "Убув")["owed"] == 5000 and after["owed"] == 0
    with pytest.raises(payouts.PayoutError):
        payouts.cancel(db, "nope", by="К", now=NOW)


def test_a_paid_sum_stays_when_the_budget_changes(db):
    _pay(db, "c1", "final")
    db.query(CharacterBudgetSnapshot).filter_by(character_id="c1").update({"total_rub": 13000})
    db.commit()
    agip = _role(_person(db), "Убув")
    assert agip["paid"] == 12500 and agip["owed"] == 1000
    db.query(CharacterBudgetSnapshot).filter_by(character_id="c1").update({"total_rub": 11000})
    db.commit()
    assert _role(_person(db), "Убув")["overpaid"] == 1000


def test_someone_with_only_a_person_advance_has_a_card(db):
    db.add(FeePayment(actor_name="Новый Диктор", kind="person_advance", amount_rub=3000, paid_by="К", paid_at=NOW))
    db.commit()
    card = next(p for p in payouts.collect(db, now=NOW)["people"] if p["name"] == "Новый Диктор")
    assert card["roles"] == [] and card["unearned"] == 3000 and card["person_advances"][0]["amount_rub"] == 3000


def test_payments_are_listed_under_the_role(db):
    _pay(db, "c2", "advance", 1000)
    payment = _role(_person(db), "Хоркон")["payments"][0]
    assert payment["kind"] == "advance" and payment["amount_rub"] == 1000 and payment["id"]
    assert payment["paid_at"].startswith("2026-10-06T09:00")   # iso_utc проекта — со смещением UTC


def test_payments_of_a_reassigned_role_stay_with_the_first_actor_and_credit_does_not_return(db):
    """Ревью: выплаты по роли, отданной другому, пропадали из «выплачено», а зачтённый ими
    аванс без роли «воскресал» и тратился второй раз."""
    from app.models import Character
    payouts.person_advance(db, person_key=_person(db)["key"], amount_rub=5000, note="", by="К", now=NOW)
    _pay(db, "c1", "final")                                   # зачёт 5000 + выплата 7500
    db.get(Character, "c1").actor_name = "Иванов Пётр"
    db.commit()
    olga = _person(db)
    assert olga["paid"] == 12500 and olga["credit"] == 0 and olga["unearned"] == 0
    past = next(r for r in olga["roles"] if r["role_name"] == "Убув")
    assert past["state"] == "past" and past["paid"] == 12500 and past["owed"] == 0


def test_payments_of_a_deleted_book_stay_and_credit_is_not_spent_twice(db):
    from app.services.book_purge import purge_book_rows
    from app.models import ScriptBook
    payouts.person_advance(db, person_key=_person(db)["key"], amount_rub=5000, note="", by="К", now=NOW)
    _pay(db, "c1", "final")
    purge_book_rows(db, "b1")
    db.delete(db.get(ScriptBook, "b1"))
    db.commit()
    olga = _person(db)
    assert olga["credit"] == 0 and olga["paid"] == 12500
    past = next(r for r in olga["roles"] if r["role_name"] == "Убув")
    assert past["state"] == "past" and past["book_title"] == "книга удалена"


def test_an_offset_advance_cannot_be_cancelled_before_its_offset(db):
    """Ревью: отмена уже зачтённого аванса без роли прятала долг — роль закрыта зачётом
    несуществующего аванса."""
    advance = payouts.person_advance(db, person_key=_person(db)["key"], amount_rub=5000, note="", by="К", now=NOW)
    rows = _pay(db, "c1", "final")
    with pytest.raises(payouts.PayoutError) as error:
        payouts.cancel(db, advance.id, by="К", now=NOW)
    assert error.value.code == "advance_offset"
    payouts.cancel(db, rows[0].id, by="К", now=NOW)           # сначала зачёт
    payouts.cancel(db, advance.id, by="К", now=NOW)
    assert _role(_person(db), "Убув")["owed"] == 5000


def test_a_payment_with_a_telegram_id_does_not_drift_to_a_namesake(db):
    """Ревью: выплаты сверялись только нечётким именем — роль от «Ольги» к «Ольге Ветровой»
    (другой человек) забирала чужие выплаты."""
    from app.models import FeePayment
    db.add(FeePayment(book_id="b1", role_key="c1", role_name="Убув", actor_name="Ветрова Ольга",
                      telegram_user_id="99", kind="advance", amount_rub=1000, paid_by="К", paid_at=NOW))
    db.commit()
    agip = _role(_person(db), "Убув")
    assert agip["paid"] == 0                                  # Telegram у выплаты другой — не её


@pytest.mark.parametrize("amount", [1000.9, True, "1e3", 10**12, -5])
def test_bad_amounts_are_refused(db, amount):
    with pytest.raises(payouts.PayoutError):
        payouts.person_advance(db, person_key=_person(db)["key"], amount_rub=amount, note="", by="К", now=NOW)

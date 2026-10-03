"""K3+: reconcile a book's extracted characters against the canon (known vs new)."""
from app.services.canon_reconcile import reconcile


def test_known_and_new_classification():
    canon = [
        ("Вохпкогамкиуб", ["Кор"], "confirmed", "Иванов"),
        ("Дгарнин", [], "confirmed", "Филатов"),
    ]
    book = [("Кор", []), ("Дгарнин", []), ("Новенький", [])]
    r = reconcile(book, canon)
    assert r["known"] == 2
    assert r["new"] == 1
    assert r["new_list"][0]["canonical"] == "Новенький"


def test_known_carries_canonical_status_and_actor():
    canon = [("Вохпкогамкиуб", ["Кор"], "confirmed", "Иванов")]
    r = reconcile([("Кор", [])], canon)
    c = r["confirmed"][0]
    assert c["existing"] == "Вохпкогамкиуб"
    assert c["actor"] == "Иванов"
    assert c["canon_status"] == "confirmed"


def test_empty_canon_all_new():
    r = reconcile([("X", []), ("Y", [])], [])
    assert r["known"] == 0 and r["new"] == 2

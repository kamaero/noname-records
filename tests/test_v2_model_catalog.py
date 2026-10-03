"""The book remembers which model it is marked up with, and what that costs."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import ScriptBook
from app.v2.model_catalog import CATALOG, DEFAULT_KEY, for_book, get


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with sessionmaker(bind=engine, autocommit=False, autoflush=False)() as session:
        session.add(ScriptBook(id="b1", title="Крылья", source_filename="k.txt", source_format="txt"))
        session.commit()
        yield session


def test_an_unknown_key_falls_back_to_the_default_rather_than_failing():
    assert get("no-such-model").key == DEFAULT_KEY
    assert get("").key == DEFAULT_KEY


def test_a_book_without_a_choice_runs_on_the_default(db):
    assert for_book(db.get(ScriptBook, "b1")).key == DEFAULT_KEY


def test_a_book_remembers_the_model_it_was_pointed_at(db):
    book = db.get(ScriptBook, "b1")
    muse = get("muse-spark-1.3-contributor")
    book.llm_provider, book.llm_model = muse.provider, muse.model
    db.commit()

    assert for_book(db.get(ScriptBook, "b1")).key == "muse-spark-1.3-contributor"


def test_a_ruble_tariff_needs_no_exchange_rate():
    muse = get("muse-spark-1.3-contributor")
    # 1M in at 11.26 ₽ + 0.5M out at 22.51 ₽
    assert round(muse.cost_rub(1_000_000, 500_000, 0.0), 2) == round(11.26 + 11.255, 2)


def test_a_dollar_tariff_is_converted_and_doubles_in_the_peak_window():
    deepseek = get(DEFAULT_KEY)
    # https://api-docs.deepseek.com/quick_start/pricing — $0.66 in, $1.98 out off-peak
    assert deepseek.rub(100.0) == {"in": 66.0, "out": 198.0, "cache_read": 2.2}
    assert deepseek.rub(100.0, peak=True) == {"in": 132.0, "out": 396.0, "cache_read": 4.4}
    assert deepseek.cost_rub(1_000_000, 500_000, 100.0) == 66.0 + 99.0
    assert deepseek.cost_rub(1_000_000, 500_000, 100.0, peak=True) == 132.0 + 198.0


def test_without_a_rate_a_dollar_tariff_gives_no_number_rather_than_a_wrong_one():
    assert get(DEFAULT_KEY).cost_rub(1_000_000, 500_000, 0.0) is None
    assert get(DEFAULT_KEY).as_dict(0.0)["rub_in"] is None


def test_the_peak_window_is_weekday_hours_in_utc():
    from datetime import datetime

    from app.v2.model_catalog import is_peak

    assert is_peak(datetime(2026, 9, 3, 2, 30)) is True    # Thursday 02:30 UTC
    assert is_peak(datetime(2026, 9, 3, 7, 0)) is True     # Thursday 07:00 UTC
    assert is_peak(datetime(2026, 9, 3, 5, 0)) is False    # between the windows
    assert is_peak(datetime(2026, 9, 5, 23, 0)) is False   # night
    assert is_peak(datetime(2026, 9, 6, 7, 0)) is False    # Sunday
    assert is_peak(None) is False


def test_the_consent_question_is_on_the_row_not_in_a_footnote():
    trains = {item.key: item.trains_on_text for item in CATALOG}
    assert trains["muse-spark-1.3-contributor"] is True
    assert trains[DEFAULT_KEY] is False

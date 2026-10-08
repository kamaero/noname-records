"""Общие помощники тестов трат."""
from datetime import datetime


def calm_now() -> datetime:
    """«Сейчас», но вне пикового тарифа DeepSeek. Запись без явного времени в будни 01–04 и
    06–10 UTC считается по двойной цене, и тесты, ждущие обычную цену, краснели бы по
    расписанию. В пиковый час берём тот же день в 12:00 UTC: окна пика (01–10 UTC) не
    пересекают границу суток по Москве, так что месяц остаётся тем же. Настоящее правило
    пика при этом не подменяется — тесты самого пика передают время явно."""
    from app.time_utils import utcnow_naive
    from app.v2.model_catalog import is_peak

    now = utcnow_naive()
    return now.replace(hour=12) if is_peak(now) else now


def exhaust_month(factory):
    """Месяц уже исчерпан."""
    from app.services import spend
    from app.services.studio_settings import studio_settings

    with factory() as db:
        row = studio_settings(db)
        row.monthly_limit_rub, row.usd_rub_rate = 1, 100.0
        spend.record(db, step="attribution", provider="deepseek", model="deepseek-v4-pro", unit="tokens",
                     input_units=1_000_000, output_units=0, now=calm_now())
        db.commit()

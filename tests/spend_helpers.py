"""Общий помощник тестов лимита: месяц уже исчерпан."""


def exhaust_month(factory):
    from app.services import spend
    from app.services.studio_settings import studio_settings

    with factory() as db:
        row = studio_settings(db)
        row.monthly_limit_rub, row.usd_rub_rate = 1, 100.0
        spend.record(db, step="attribution", provider="deepseek", model="deepseek-v4-pro", unit="tokens",
                     input_units=1_000_000, output_units=0)
        db.commit()

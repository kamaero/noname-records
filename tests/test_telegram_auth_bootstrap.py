from app.services.telegram_auth_bootstrap import parse_telegram_auth_whitelist


def test_parse_telegram_auth_whitelist_accepts_id_name_pairs() -> None:
    entries = parse_telegram_auth_whitelist("900000100|Max Ray;900000106|Александр Белозёров")

    assert [(entry.telegram_user_id, entry.display_name) for entry in entries] == [
        ("900000100", "Max Ray"),
        ("900000106", "Александр Белозёров"),
    ]


def test_parse_telegram_auth_whitelist_skips_invalid_and_duplicate_entries() -> None:
    entries = parse_telegram_auth_whitelist("bad|Nope;900000100|Max Ray;900000100|Duplicate;123|")

    assert [(entry.telegram_user_id, entry.display_name) for entry in entries] == [("900000100", "Max Ray")]

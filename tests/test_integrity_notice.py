from app.services.integrity_notice import integrity_notice


def test_the_notice_names_the_role_and_the_person():
    """«Что-то не сошлось» бесполезно: владельцу нужно знать, кого просить перезалить."""
    text = integrity_notice("Глава 1", [
        {"role": "Дгарнин", "actor_name": "Сергей Зотов", "canonical_filename": "KP_Ch01_Dgarnin_Zotov.wav", "verify_state": "mismatch"},
        {"role": "Оснива", "actor_name": "Наташа Уткина", "canonical_filename": "KP_Ch01_Osniva_Utkina.wav", "verify_state": "missing"},
    ])
    assert "Глава 1" in text
    assert "Дгарнин" in text and "Сергей Зотов" in text
    assert "Оснива" in text and "Наташа Уткина" in text
    assert "не сходится" in text
    assert "нет на месте" in text


def test_no_problems_means_no_notice():
    assert integrity_notice("Глава 1", []) == ""

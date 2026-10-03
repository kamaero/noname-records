from app.services.author_imports.compendium import parse_compendium_fb2

FB2 = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0" '
    'xmlns:l="http://www.w3.org/1999/xlink">'
    '<body>'
    '<section><title><p>Полумрак. Бароны.</p></title>'
    '<p><strong>Хубол</strong>. Он же Полумракский Купец. Жадный карлик.</p>'
    '<p><strong>Дазиптовд</strong>. Она же Матерь Демонов. Хтонь.</p>'
    '</section>'
    '<section><title><p>Магия.</p></title>'
    '<p><strong>Файрбол</strong>. Заклинание огня.</p>'
    '</section>'
    '</body></FictionBook>'
).encode("utf-8")


def test_parse_filters_person_topics_and_captures_aliases():
    entries = parse_compendium_fb2(FB2, allowlist={"Полумрак. Бароны."})
    names = {e["name"]: e for e in entries}
    assert set(names) == {"Хубол", "Дазиптовд"}
    assert names["Хубол"]["aliases"] == ["Полумракский Купец"]
    assert names["Дазиптовд"]["aliases"] == ["Матерь Демонов"]
    assert names["Хубол"]["topic"] == "Полумрак. Бароны."
    assert "карлик" in names["Хубол"]["description"]


NUMBERED_FB2 = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">'
    '<body>'
    '<section><title><p>Девять Небес. Имена Мардука.</p></title>'
    '<p><strong>1. Огимгоик</strong>. Первое имя.</p>'
    '<p><strong>10) Инане</strong>. Десятое имя.</p>'
    '</section>'
    '</body></FictionBook>'
).encode("utf-8")


def test_parse_strips_numeric_list_prefix():
    entries = parse_compendium_fb2(NUMBERED_FB2, allowlist={"Девять Небес. Имена Мардука."})
    names = {e["name"] for e in entries}
    assert names == {"Огимгоик", "Инане"}        # "1. " / "10) " prefixes stripped


def test_without_an_allowlist_every_section_is_taken():
    """Энциклопедия другого автора делится на свои разделы; молча выбросить их — хуже,
    чем показать лишнее: человек отсеет сам."""
    names = {e["name"] for e in parse_compendium_fb2(FB2)}
    assert names == {"Хубол", "Дазиптовд", "Файрбол"}

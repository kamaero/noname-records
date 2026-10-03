from app.services.author_imports.cast import parse_cast_html

HTML = (
    "<html><script>\n"
    'const CAST_DATA = [{"actor": "Румянцев Константин", "roles": ["Кимиуб", "Нохпет"]}, '
    '{"actor": "Зарецкий Мирон", "roles": ["Агг"]}];\n'
    "</script></html>"
)


def test_parse_cast_role_to_actor():
    m = parse_cast_html(HTML)
    assert m["Кимиуб"] == "Румянцев Константин"
    assert m["Агг"] == "Зарецкий Мирон"
    assert m["Нохпет"] == "Румянцев Константин"

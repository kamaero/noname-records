from app.services.author_imports.legend import parse_legend_html

HTML = (
    '<div><span>Агг</span><i style="background-color: #cc0000"></i></div>'
    '<div><span>Хубол</span><i style="background-color: #660000"></i></div>'
)


def test_parse_legend_name_to_color():
    m = parse_legend_html(HTML)
    assert m["Агг"] == "#cc0000"
    assert m["Хубол"] == "#660000"

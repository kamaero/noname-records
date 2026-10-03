from types import SimpleNamespace

from app.services.budget_rows import collapse_budget_character_rows as _collapse_budget_character_rows


def _char(cid, name, char_map_id):
    return SimpleNamespace(
        id=cid, name=name, char_map_id=char_map_id, aliases="", appears_in="",
        actor_name="", manual_rate_rub_per_min=0, manual_fixed_rub=0, race="",
        temperament="", character_color="", character_text_color="",
        character_font_weight="", character_font_style="",
    )


def _snap(cid, lines):
    return SimpleNamespace(character_id=cid, lines_count=lines, approx_seconds=0, total_rub=0)


def test_three_narrator_rows_collapse_to_one():
    chars = [
        _char("c1", "Расска́зчик", "map-1"),
        _char("c2", "Narrator", "map-2"),
        _char("c3", "NARRATOR", "map-3"),
        _char("c4", "Дгарнин", "map-4"),
    ]
    snaps_in = [_snap("c1", 5900), _snap("c2", 0), _snap("c3", 10), _snap("c4", 932)]

    rows, snaps = _collapse_budget_character_rows(chars, snaps_in)

    names = sorted(r["name"] for r in rows)
    assert names == ["Дгарнин", "Рассказчик"]
    narrator_row = next(r for r in rows if r["name"] == "Рассказчик")
    assert narrator_row["id"] in {"c1", "c2", "c3"}
    # all narrator-variant lines are summed onto the first-seen narrator's snapshot
    assert snaps[str(narrator_row["id"])].lines_count == 5910

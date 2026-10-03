from __future__ import annotations

import re
import unicodedata

from app.models import Character, ScriptBook


#: Утверждённые студией цвета ролей по имени: (фон, текст, насыщенность, начертание).
#: Пусто — цвета подбираются автоматически; студия может закрепить свои, например
#: «Рассказчик»: ("#000000", "#ffffff", "700", "normal").
LEGEND_STYLE_OVERRIDES: dict[str, tuple[str, str, str, str]] = {}

AUTO_COLOR_PALETTE = [
    "#4ed0aa",
    "#ffbd38",
    "#7ea8ff",
    "#c85be3",
    "#f58fb2",
    "#8ed6c4",
    "#f08c62",
    "#7fd9a3",
    "#8fd4f5",
    "#c8df6a",
    "#ff9f6e",
    "#c687d9",
]

TYPOGRAPHY_VARIANTS = (
    ("#11161d", "700", "normal"),
    ("#5b1838", "700", "italic"),
    ("#17324a", "800", "normal"),
    ("#29402b", "700", "italic"),
    ("#5a3210", "800", "normal"),
    ("#f8fbff", "800", "normal"),
    ("#fff4dc", "800", "italic"),
)


def normalize_character_key(name: str) -> str:
    # Strip only stress accents (acute/grave), tolerating both combining and
    # precomposed forms, while preserving letters like Ё (diaeresis is kept).
    value = unicodedata.normalize("NFD", str(name or ""))
    value = value.replace("\u0301", "").replace("\u0300", "")
    value = unicodedata.normalize("NFC", value).strip()
    value = re.sub(r"^[^A-Za-zА-Яа-яЁё0-9(]+", "", value)
    value = re.sub(r"[\[\]{}\"'`]+", "", value)
    value = re.sub(r"\s+", " ", value).strip(" -_.:,;")
    return value


def _stable_hash(value: str) -> int:
    hash_value = 2166136261
    for char in (value or ""):
        hash_value ^= ord(char)
        hash_value = (hash_value * 16777619) & 0xFFFFFFFF
    return hash_value


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    value = (hex_color or "").strip().lstrip("#")
    if len(value) != 6:
        return (0, 0, 0)
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def _contrast_text_color(bg_hex: str) -> str:
    r, g, b = _hex_to_rgb(bg_hex)
    luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255
    return "#ffffff" if luminance < 0.52 else "#11161d"


def _relative_luminance(hex_color: str) -> float:
    r, g, b = _hex_to_rgb(hex_color)

    def _to_linear(channel: int) -> float:
        normalized = channel / 255
        if normalized <= 0.04045:
            return normalized / 12.92
        return ((normalized + 0.055) / 1.055) ** 2.4

    return 0.2126 * _to_linear(r) + 0.7152 * _to_linear(g) + 0.0722 * _to_linear(b)


def _contrast_ratio(bg_hex: str, fg_hex: str) -> float:
    bg_lum = _relative_luminance(bg_hex)
    fg_lum = _relative_luminance(fg_hex)
    lighter = max(bg_lum, fg_lum)
    darker = min(bg_lum, fg_lum)
    return (lighter + 0.05) / (darker + 0.05)


def _color_distance(bg_hex_a: str, bg_hex_b: str) -> float:
    r1, g1, b1 = _hex_to_rgb(bg_hex_a)
    r2, g2, b2 = _hex_to_rgb(bg_hex_b)
    return ((r1 - r2) ** 2 + (g1 - g2) ** 2 + (b1 - b2) ** 2) ** 0.5


def _legend_override(name: str) -> tuple[str, str, str, str] | None:
    clean_name = (name or "").strip()
    if clean_name in LEGEND_STYLE_OVERRIDES:
        return LEGEND_STYLE_OVERRIDES[clean_name]
    return None


def _hsl_to_hex(h: float, s: float, l: float) -> str:
    c = (1 - abs(2 * l - 1)) * s
    x = c * (1 - abs((h / 60) % 2 - 1))
    m = l - c / 2
    if h < 60:
        r1, g1, b1 = c, x, 0
    elif h < 120:
        r1, g1, b1 = x, c, 0
    elif h < 180:
        r1, g1, b1 = 0, c, x
    elif h < 240:
        r1, g1, b1 = 0, x, c
    elif h < 300:
        r1, g1, b1 = x, 0, c
    else:
        r1, g1, b1 = c, 0, x
    r = round((r1 + m) * 255)
    g = round((g1 + m) * 255)
    b = round((b1 + m) * 255)
    return f"#{r:02x}{g:02x}{b:02x}"


def fallback_character_style(name: str) -> tuple[str, str, str, str]:
    hash_value = _stable_hash(name or "")
    hue = hash_value % 360
    saturation = 0.60 + ((hash_value // 360) % 9) / 100
    lightness = 0.72 + ((hash_value // 91) % 10) / 100
    bg = _hsl_to_hex(hue, saturation, min(lightness, 0.84))
    fg, weight, style = _select_typography(bg, name)
    return bg, fg, weight, style


def resolve_character_style(name: str) -> tuple[str, str, str, str]:
    clean_name = (name or "").strip()
    override = _legend_override(clean_name)
    if override:
        return override
    return fallback_character_style(clean_name)


def _shift_hex_color(bg_hex: str, name: str, attempt: int) -> str:
    r, g, b = _hex_to_rgb(bg_hex)
    hash_value = _stable_hash(f"{name}:{attempt}")
    shift = 22 + (hash_value % 46)
    values = [r, g, b]
    primary = attempt % 3
    secondary = (primary + 1 + (hash_value % 2)) % 3
    direction = 1 if (hash_value % 2 == 0) else -1
    values[primary] = max(0, min(255, values[primary] + (shift * direction)))
    values[secondary] = max(0, min(255, values[secondary] - max(12, shift // 2) * direction))
    return f"#{values[0]:02x}{values[1]:02x}{values[2]:02x}"


def _preferred_palette_color(name: str, used_colors: list[str]) -> str:
    if not AUTO_COLOR_PALETTE:
        return fallback_character_style(name)[0]
    offset = _stable_hash(name) % len(AUTO_COLOR_PALETTE)
    ordered = AUTO_COLOR_PALETTE[offset:] + AUTO_COLOR_PALETTE[:offset]
    used = {color.lower() for color in used_colors}
    best_color = ordered[0]
    best_score = -1.0
    for candidate in ordered:
        if candidate.lower() in used:
            continue
        if not used_colors:
            return candidate
        score = min(_color_distance(candidate, used) for used in used_colors)
        if score > best_score:
            best_score = score
            best_color = candidate
    if best_color.lower() in used:
        for candidate in ordered:
            if candidate.lower() not in used:
                return candidate
    return best_color


def _find_distinct_color(bg_hex: str, name: str, used_colors: list[str]) -> str:
    candidate = bg_hex
    if used_colors and any(_color_distance(candidate, used) < 96 for used in used_colors):
        candidate = _preferred_palette_color(name, used_colors)
    attempt = 0
    while used_colors and any(_color_distance(candidate, used) < 96 for used in used_colors):
        attempt += 1
        candidate = _shift_hex_color(candidate, name, attempt)
        if attempt >= 24:
            break
    return candidate


def _select_typography(bg_hex: str, name: str, *, prefer_distinct: bool = False) -> tuple[str, str, str]:
    offset = _stable_hash(f"typo:{name}:{bg_hex}") % len(TYPOGRAPHY_VARIANTS)
    ordered = TYPOGRAPHY_VARIANTS[offset:] + TYPOGRAPHY_VARIANTS[:offset]
    contrast_fallback = _contrast_text_color(bg_hex)
    viable = [variant for variant in ordered if _contrast_ratio(bg_hex, variant[0]) >= 4.2]
    if viable:
        if prefer_distinct:
            for fg, weight, style in viable:
                if (fg, weight, style) != (contrast_fallback, "700", "normal"):
                    return fg, weight, style
        fg, weight, style = viable[0]
        return fg, weight, style
    return contrast_fallback, "800", "normal"


def _has_explicit_typography(weight: str, style: str) -> bool:
    clean_weight = str(weight or "").strip()
    clean_style = str(style or "").strip()
    return bool(clean_weight and clean_weight != "700") or bool(clean_style and clean_style != "normal")


def _is_basic_auto_style(bg_hex: str, fg_hex: str, weight: str, style: str) -> bool:
    return (
        str(fg_hex or "").strip().lower() == _contrast_text_color(bg_hex).lower()
        and str(weight or "").strip() in {"", "700"}
        and str(style or "").strip() in {"", "normal"}
    )


def inherit_author_character_styles(db, *, book: ScriptBook | None, characters: list[Character]) -> bool:
    if not book or not characters:
        return False
    author_id = str(getattr(book, "created_by_user_id", "") or "").strip()
    if not author_id:
        return False
    target_names = {
        normalize_character_key(str(getattr(character, "name", "") or ""))
        for character in characters
        if normalize_character_key(str(getattr(character, "name", "") or ""))
    }
    if not target_names:
        return False
    source_books = db.query(ScriptBook.id).filter(
        ScriptBook.created_by_user_id == author_id,
        ScriptBook.id != book.id,
    ).all()
    source_book_ids = [str(item[0]) for item in source_books if str(item[0] or "").strip()]
    if not source_book_ids:
        return False
    source_rows = db.query(Character).filter(Character.book_id.in_(source_book_ids)).all()
    style_memory: dict[str, tuple[str, str, str, str]] = {}
    for row in source_rows:
        normalized = normalize_character_key(str(getattr(row, "name", "") or ""))
        if not normalized or normalized not in target_names or normalized in style_memory:
            continue
        bg = str(getattr(row, "character_color", "") or "").strip()
        fg = str(getattr(row, "character_text_color", "") or "").strip()
        weight = str(getattr(row, "character_font_weight", "") or "").strip() or "700"
        style = str(getattr(row, "character_font_style", "") or "").strip() or "normal"
        if not bg or not fg:
            continue
        style_memory[normalized] = (bg, fg, weight, style)
    changed = False
    for character in characters:
        normalized = normalize_character_key(str(getattr(character, "name", "") or ""))
        inherited = style_memory.get(normalized)
        if not inherited:
            continue
        bg, fg, weight, style = inherited
        current_bg = str(getattr(character, "character_color", "") or "").strip()
        current_fg = str(getattr(character, "character_text_color", "") or "").strip()
        current_weight = str(getattr(character, "character_font_weight", "") or "").strip() or "700"
        current_style = str(getattr(character, "character_font_style", "") or "").strip() or "normal"
        if current_bg == bg and current_fg == fg and current_weight == weight and current_style == style:
            continue
        if current_bg and current_fg and not _is_basic_auto_style(current_bg, current_fg, current_weight, current_style):
            continue
        character.character_color = bg
        character.character_text_color = fg
        character.character_font_weight = weight
        character.character_font_style = style
        changed = True
    return changed


def ensure_character_style(character: Character) -> tuple[str, str, str, str]:
    bg = (character.character_color or "").strip()
    fg = (character.character_text_color or "").strip()
    weight = (character.character_font_weight or "").strip() or "700"
    style = (character.character_font_style or "").strip() or "normal"
    if bg and fg:
        if not _has_explicit_typography(weight, style):
            fg, weight, style = _select_typography(bg, character.name or "", prefer_distinct=True)
            character.character_text_color = fg
        character.character_font_weight = weight
        character.character_font_style = style
        return bg, fg, weight, style
    bg, fg, weight, style = resolve_character_style(character.name or "")
    character.character_color = bg
    character.character_text_color = fg
    character.character_font_weight = weight
    character.character_font_style = style
    return bg, fg, weight, style


def build_character_style_maps(characters: list[Character], extra_names: list[str] | None = None) -> tuple[dict[str, str], dict[str, str], dict[str, str], dict[str, str]]:
    bg_map: dict[str, str] = {}
    text_map: dict[str, str] = {}
    weight_map: dict[str, str] = {}
    style_map: dict[str, str] = {}
    style_by_group: dict[str, tuple[str, str, str, str]] = {}
    used_colors: list[str] = []
    priority_order = {str(name or "").strip(): idx for idx, name in enumerate(extra_names or []) if str(name or "").strip()}
    for character in sorted(
        characters,
        key=lambda item: (
            0 if (item.name or "").strip() in priority_order else 1,
            priority_order.get((item.name or "").strip(), 10**6),
            (item.name or "").lower(),
        ),
    ):
        name = (character.name or "").strip()
        if not name:
            continue
        group_key = str(getattr(character, "char_map_id", "") or "").strip() or f"name:{normalize_character_key(name)}"
        had_manual_colors = bool((character.character_color or "").strip() and (character.character_text_color or "").strip())
        if group_key in style_by_group:
            bg, fg, weight, style = style_by_group[group_key]
            character.character_color = bg
            character.character_text_color = fg
            character.character_font_weight = weight
            character.character_font_style = style
        else:
            bg, fg, weight, style = ensure_character_style(character)
            if not had_manual_colors:
                if name in priority_order and len(used_colors) < len(AUTO_COLOR_PALETTE):
                    bg = AUTO_COLOR_PALETTE[len(used_colors)]
                else:
                    bg = _preferred_palette_color(name, used_colors)
                fg, weight, style = _select_typography(bg, name)
                character.character_color = bg
                character.character_text_color = fg
                character.character_font_weight = weight
                character.character_font_style = style
                if bg.lower() in {color.lower() for color in used_colors} or len(used_colors) >= len(AUTO_COLOR_PALETTE):
                    next_bg = _find_distinct_color(bg, name, used_colors)
                    if next_bg != bg:
                        bg = next_bg
                        fg, weight, style = _select_typography(bg, name)
                        character.character_color = bg
                        character.character_text_color = fg
                        character.character_font_weight = weight
                        character.character_font_style = style
            else:
                # A colour somebody chose is rendered as chosen. Spreading applies to the
                # palette we assign, not to a decision: replacing it here made the cast
                # screen and the reader show different colours for the same role, with
                # nothing in the interface to explain why. Roles that are genuinely too
                # close are named on the cast screen and repainted deliberately there.
                if _is_basic_auto_style(bg, fg, weight, style):
                    fg, weight, style = _select_typography(bg, name, prefer_distinct=True)
                    character.character_text_color = fg
                    character.character_font_weight = weight
                    character.character_font_style = style
            used_colors.append(bg)
            style_by_group[group_key] = (bg, fg, weight, style)
        bg_map[name] = bg
        text_map[name] = fg
        weight_map[name] = weight
        style_map[name] = style
        normalized_name = normalize_character_key(name)
        if normalized_name and normalized_name not in bg_map:
            bg_map[normalized_name] = bg
            text_map[normalized_name] = fg
            weight_map[normalized_name] = weight
            style_map[normalized_name] = style
    for name in sorted((extra_names or []), key=lambda item: (item or "").lower()):
        clean_name = (name or "").strip()
        if not clean_name or clean_name in bg_map:
            continue
        bg = _preferred_palette_color(clean_name, used_colors)
        fg, weight, style = _select_typography(bg, clean_name)
        if bg.lower() in {color.lower() for color in used_colors} or len(used_colors) >= len(AUTO_COLOR_PALETTE):
            bg = _find_distinct_color(bg, clean_name, used_colors)
        fg, weight, style = _select_typography(bg, clean_name)
        used_colors.append(bg)
        bg_map[clean_name] = bg
        text_map[clean_name] = fg
        weight_map[clean_name] = weight
        style_map[clean_name] = style
        normalized_name = normalize_character_key(clean_name)
        if normalized_name and normalized_name not in bg_map:
            bg_map[normalized_name] = bg
            text_map[normalized_name] = fg
            weight_map[normalized_name] = weight
            style_map[normalized_name] = style
    return bg_map, text_map, weight_map, style_map

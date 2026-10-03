import json
import re
from pathlib import Path
from collections import Counter

DICT_PATH = Path("app/pronunciation_dict.json")
_DICT_CACHE: dict[str, str | list[str]] | None = None
_DICT_CACHE_MTIME_NS: int | None = None
_DICT_PATTERN_CACHE: list[tuple[str, str, re.Pattern[str]]] | None = None
_DICT_KNOWN_SET_CACHE: set[str] | None = None
_WORD_TOKEN_RE = re.compile(r"[А-Яа-яЁё][А-Яа-яЁё-]{2,}")
FALLBACK_DICT = {
    "договор": "догово́р",
    "звонит": "звони́т",
    "каталог": "катало́г",
    "квартал": "кварта́л",
    "красивее": "красиве́е",
    "жалюзи": "жалюзи́",
    "ходатайство": "хода́тайство",
    "свекла": "свёкла",
    "сливовый": "сли́вовый",
    "исчерпать": "исчерпа́ть",
    "торты": "то́рты",
    "банты": "ба́нты",
    "замок": ["за́мок", "замо́к"],
    "мука": ["му́ка", "мука́"],
}

ACCENT_SUGGESTION_STOPWORDS = {
    "а",
    "без",
    "был",
    "была",
    "были",
    "быть",
    "в",
    "вам",
    "вас",
    "весь",
    "вот",
    "все",
    "всего",
    "вы",
    "где",
    "да",
    "даже",
    "для",
    "до",
    "его",
    "ее",
    "её",
    "если",
    "есть",
    "еще",
    "ещё",
    "же",
    "за",
    "здесь",
    "и",
    "из",
    "или",
    "их",
    "как",
    "когда",
    "кто",
    "ли",
    "лишь",
    "между",
    "меня",
    "мне",
    "может",
    "мой",
    "мы",
    "на",
    "над",
    "нам",
    "нас",
    "не",
    "него",
    "нее",
    "неё",
    "нет",
    "ни",
    "но",
    "ну",
    "о",
    "об",
    "один",
    "она",
    "они",
    "оно",
    "он",
    "от",
    "очень",
    "по",
    "под",
    "при",
    "про",
    "раз",
    "с",
    "сам",
    "себя",
    "сказал",
    "со",
    "так",
    "такой",
    "там",
    "тебя",
    "тем",
    "то",
    "только",
    "тут",
    "ты",
    "у",
    "уже",
    "хоть",
    "чем",
    "что",
    "чтоб",
    "чтобы",
    "эта",
    "это",
    "этот",
    "я",
}


def load_pronunciation_dict() -> dict[str, str | list[str]]:
    global _DICT_CACHE, _DICT_CACHE_MTIME_NS, _DICT_PATTERN_CACHE, _DICT_KNOWN_SET_CACHE

    if not DICT_PATH.exists():
        _DICT_CACHE = dict(FALLBACK_DICT)
        _DICT_CACHE_MTIME_NS = None
        _DICT_PATTERN_CACHE = None
        _DICT_KNOWN_SET_CACHE = _build_known_words_cache(_DICT_CACHE)
        return _DICT_CACHE

    try:
        current_mtime_ns = DICT_PATH.stat().st_mtime_ns
    except OSError:
        current_mtime_ns = None

    if _DICT_CACHE is not None and _DICT_CACHE_MTIME_NS == current_mtime_ns:
        return _DICT_CACHE

    try:
        data = json.loads(DICT_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        _DICT_CACHE = dict(FALLBACK_DICT)
        _DICT_CACHE_MTIME_NS = current_mtime_ns
        _DICT_PATTERN_CACHE = None
        _DICT_KNOWN_SET_CACHE = _build_known_words_cache(_DICT_CACHE)
        return _DICT_CACHE
    out: dict[str, str | list[str]] = {}
    if isinstance(data, dict):
        for k, v in data.items():
            key = str(k).strip().lower()
            if not key:
                continue
            if isinstance(v, str):
                out[key] = v.strip()
            elif isinstance(v, list):
                vals = [str(x).strip() for x in v if str(x).strip()]
                if vals:
                    out[key] = vals
    # FALLBACK_DICT всегда добавляется для слов, которых нет в основном словаре
    for k, v in FALLBACK_DICT.items():
        if k not in out:
            out[k] = v
    if not out:
        _DICT_CACHE = dict(FALLBACK_DICT)
        _DICT_CACHE_MTIME_NS = current_mtime_ns
        _DICT_PATTERN_CACHE = None
        _DICT_KNOWN_SET_CACHE = _build_known_words_cache(_DICT_CACHE)
        return _DICT_CACHE
    _DICT_CACHE = out
    _DICT_CACHE_MTIME_NS = current_mtime_ns
    _DICT_PATTERN_CACHE = None
    _DICT_KNOWN_SET_CACHE = _build_known_words_cache(out)
    return _DICT_CACHE


def parse_pronunciation_notes(notes: str) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for raw in (notes or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        src, dst = line.split("=", 1)
        src = _strip_stress(src.strip()).lower()
        dst = _normalize_stress_marks(dst.strip())
        if src and dst:
            mapping[src] = dst
    return mapping


def build_pronunciation_variants(term: str, stressed: str, words: list[str]) -> dict[str, str]:
    source = _strip_stress(term).strip("-").lower()
    stressed_value = _normalize_stress_marks(str(stressed or "").strip())
    stress_vowel_index = _stress_vowel_index(stressed_value)
    if not source or not stressed_value or stress_vowel_index < 0:
        return {}

    variants: dict[str, str] = {source: stressed_value.lower()}
    seen: set[str] = {source}
    for raw_word in words:
        candidate = _strip_stress(str(raw_word or "")).strip("-").lower()
        if not candidate or candidate in seen:
            continue
        if not _looks_like_same_word_family(source, candidate):
            continue
        accented = _accent_word_by_vowel_index(candidate, stress_vowel_index)
        if accented == candidate:
            continue
        variants[candidate] = accented
        seen.add(candidate)
    return variants


def apply_manual_pronunciation_overrides(text: str, mapping: dict[str, str]) -> tuple[str, int]:
    out = text or ""
    applied = 0
    for word, value in mapping.items():
        if len(word) < 4 or word in ACCENT_SUGGESTION_STOPWORDS:
            continue
        pattern = _compile_word_pattern(word)
        out, count = pattern.subn(_make_replacer(value), out)
        applied += int(count or 0)
    return _normalize_stress_marks(out), applied


def apply_pronunciation_dictionary(text: str, notes: str) -> tuple[str, dict]:
    base = load_pronunciation_dict()
    manual = parse_pronunciation_notes(notes)
    out = text or ""
    applied = 0
    ambiguous: list[str] = []
    for word, value in base.items():
        if isinstance(value, list):
            ambiguous.append(word)
    base_patterns = _ensure_pattern_cache(base)
    manual_keys = set(manual.keys())
    for word, value, pattern in base_patterns:
        if word in manual_keys:
            continue
        out, n = pattern.subn(_make_replacer(value), out)
        applied += int(n)
    for word, value in manual.items():
        if len(word) < 4 or word in ACCENT_SUGGESTION_STOPWORDS:
            continue
        pattern = _compile_word_pattern(word)
        out, n = pattern.subn(_make_replacer(value), out)
        applied += int(n)
    return out, {"applied": applied, "ambiguous_words": sorted(set(ambiguous))}


def _strip_stress(value: str) -> str:
    return (value or "").replace("\u0301", "")


def _normalize_stress_marks(value: str) -> str:
    return re.sub(r"\u0301{2,}", "\u0301", value or "")


def _stress_vowel_index(word: str) -> int:
    vowel_index = -1
    for char in word or "":
        if is_vowel_ru(char):
            vowel_index += 1
            continue
        if char == "\u0301":
            return vowel_index
    return -1


def _tokenize_ru_words(text: str) -> list[str]:
    return _WORD_TOKEN_RE.findall(text or "")


def _accent_word_by_vowel_index(word: str, vowel_index: int) -> str:
    chars: list[str] = []
    current_vowel = -1
    inserted = False
    for char in word or "":
        chars.append(char)
        if not is_vowel_ru(char):
            continue
        current_vowel += 1
        if current_vowel == vowel_index:
            chars.append("\u0301")
            inserted = True
    return "".join(chars) if inserted else word


def detect_unknown_words(
    text: str,
    notes: str = "",
    extra_known_words: set[str] | None = None,
    limit: int = 120,
) -> list[dict[str, int | str]]:
    base = load_pronunciation_dict()
    manual = parse_pronunciation_notes(notes)
    known: set[str] = set(_DICT_KNOWN_SET_CACHE or _build_known_words_cache(base))
    for word, value in manual.items():
        lw = _strip_stress(word).lower()
        if lw:
            known.add(lw)
        vv = _strip_stress(value).lower()
        if vv:
            known.add(vv)
    if extra_known_words:
        for item in extra_known_words:
            for token in _tokenize_ru_words(str(item)):
                known.add(_strip_stress(token).lower())

    counts: Counter[str] = Counter()
    cap_counts: Counter[str] = Counter()
    for token in _WORD_TOKEN_RE.findall(text or ""):
        cleaned = _strip_stress(token).strip("-").lower()
        if (
            not cleaned
            or cleaned in known
            or cleaned in ACCENT_SUGGESTION_STOPWORDS
            or len(cleaned) < 4
            or cleaned.isdigit()
        ):
            continue
        counts[cleaned] += 1
        if token[:1].isupper():
            cap_counts[cleaned] += 1

    # Показываем в первую очередь сущности, похожие на имена/топонимы:
    # слово должно иметь заметную долю вхождений с заглавной буквы.
    out = []
    for word, cnt in counts.most_common():
        cap = cap_counts.get(word, 0)
        if cap <= 0:
            continue
        # Отсекаем обычные слова, попадающие в список лишь потому что
        # встречаются с заглавной буквы в начале предложения.
        # Требуем: ≥3 вхождений с заглавной И ≥60 % от всех вхождений.
        if cnt < 2:
            continue
        if cap < 3:
            continue
        if (cap / max(1, cnt)) < 0.60:
            continue
        out.append({"word": word, "count": int(cnt)})
        if len(out) >= limit:
            break
    return out


def _compile_word_pattern(word: str) -> re.Pattern[str]:
    parts: list[str] = []
    for char in word:
      escaped = re.escape(char)
      if re.match(r"[А-Яа-яЁё]", char):
          parts.append(f"{escaped}\u0301?")
      else:
          parts.append(escaped)
    body = "".join(parts)
    return re.compile(rf"(?<![A-Za-zА-Яа-яЁё0-9_])({body})(?![A-Za-zА-Яа-яЁё0-9_])", re.IGNORECASE)


def _looks_like_same_word_family(source: str, candidate: str) -> bool:
    if source == candidate:
        return True
    prefix = 0
    for left, right in zip(source, candidate):
        if left != right:
            break
        prefix += 1
    min_len = min(len(source), len(candidate))
    max_len = max(len(source), len(candidate))
    if prefix < max(4, min_len - 2):
        return False
    return (prefix / max_len) >= 0.7


def is_vowel_ru(char: str) -> bool:
    return char in "аеёиоуыэюяАЕЁИОУЫЭЮЯ"


def _make_replacer(value: str):
    def _apply_case_pattern(template: str, replacement: str) -> str:
        plain_template = _strip_stress(template)
        if not plain_template:
            return replacement
        if plain_template.isupper():
            return replacement.upper()
        if plain_template.islower():
            return replacement.lower()
        if plain_template[:1].isupper() and plain_template[1:].islower():
            return replacement[:1].upper() + replacement[1:].lower() if replacement else replacement

        result: list[str] = []
        template_index = 0
        for char in replacement:
            if char == "\u0301":
                result.append(char)
                continue
            if template_index >= len(plain_template):
                result.append(char)
                continue
            source_char = plain_template[template_index]
            result.append(char.upper() if source_char.isupper() else char.lower())
            template_index += 1
        return "".join(result)

    def _replace(m: re.Match, _v: str = value) -> str:
        matched = m.group(1)
        return _apply_case_pattern(matched, _v)
    return _replace


def _build_pattern_cache(source: dict[str, str | list[str]]) -> list[tuple[str, str, re.Pattern[str]]]:
    cached: list[tuple[str, str, re.Pattern[str]]] = []
    for word, value in source.items():
        if isinstance(value, list):
            continue
        if len(word) < 4 or word in ACCENT_SUGGESTION_STOPWORDS:
            continue
        cached.append((word, value, _compile_word_pattern(word)))
    return cached


def _build_known_words_cache(source: dict[str, str | list[str]]) -> set[str]:
    known: set[str] = set()
    for word, value in source.items():
        lw = _strip_stress(word).lower()
        if lw:
            known.add(lw)
        if isinstance(value, str):
            vv = _strip_stress(value).lower()
            if vv:
                known.add(vv)
        elif isinstance(value, list):
            for item in value:
                vv = _strip_stress(str(item)).lower()
                if vv:
                    known.add(vv)
    return known


def _ensure_pattern_cache(source: dict[str, str | list[str]]) -> list[tuple[str, str, re.Pattern[str]]]:
    global _DICT_PATTERN_CACHE
    if _DICT_PATTERN_CACHE is None:
        _DICT_PATTERN_CACHE = _build_pattern_cache(source)
    return _DICT_PATTERN_CACHE

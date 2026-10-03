from __future__ import annotations

import re

from app.config import settings
from app.models import AudioFile
from app.services import audio_mirror, audio_storage
from app.services.audio_probe import probe_audio_file
from app.services.asr_coverage import transliterate
from app.services.audio_naming import canonical_audio_name, name_suffixes

AUDIO_EXTS = {"wav", "mp3", "m4a", "flac", "ogg", "aac", "opus", "webm"}
FILENAME_STOPWORDS = {
    "глава",
    "chapter",
    "ch",
    "роль",
    "role",
    "диктор",
    "actor",
    "актёр",
    "актер",
    "take",
    "дубль",
    "проба",
    "пробы",
    "проб",
    "audition",
    "track",
    "audio",
    "voice",
}


#: a take belongs to a chapter of an approved role; an audition belongs to nobody yet
TAKE = "take"
AUDITION = "audition"

#: Короткий фикс: отдельный токен «fix»/«фикс» с необязательным номером. Не часть роли и не актёр.
FIX_TOKEN = re.compile(r"(?:fix|фикс)([0-9]{0,3})", re.IGNORECASE)


def _tokens(filename: str) -> list[str]:
    stem = re.sub(r"\.[^.]+$", "", (filename or "").strip()).replace("—", "-").replace("–", "-")
    return [t for t in re.split(r"[\s_\-]+", stem) if t.strip()]


def fix_number(original_filename: str) -> int | None:
    """Номер фикса из имени, которое прислал актёр. `None` — это не фикс, `0` — фикс без номера."""
    for token in _tokens(original_filename):
        match = FIX_TOKEN.fullmatch(token)
        if match:
            return int(match.group(1)) if match.group(1) else 0
    return None


def _role_tokens(value: str) -> list[str]:
    """A role's words, folded to Latin so «Слуга Билсима» and `Sluga_Bilsima` meet."""
    folded = transliterate(str(value or ""))
    return [token for token in re.split(r"[^a-z0-9]+", folded.lower()) if token]


def filename_names_role(filename_role: str, chosen_role: str) -> bool:
    """Whether the role read out of a filename is plausibly the role picked in the form.

    Роман выбрал «Сатухух» и прислал `03-Za'Maor.wav`; форма и файл говорили о разном,
    и ничто этого не сверяло. Сверка нарочно снисходительна: имя в файле склоняют
    («Энея» о роли «Эней») и пишут латиницей, а расходятся они всерьёз только когда
    не совпадает ни одно слово.
    """
    left, right = _role_tokens(filename_role), _role_tokens(chosen_role)
    if not left or not right:
        return False
    for a in left:
        for b in right:
            if a == b:
                return True
            long, short = (a, b) if len(a) >= len(b) else (b, a)
            if len(short) >= 4 and long.startswith(short):
                return True
    return False


def _extract_chapter_index(tokens: list[str], normalized: str) -> int:
    """Догадка о главе для подсказки-имени: нарочно снисходительна, отказы на ней не строят."""
    chapter_match = re.search(r"(?:глава|chapter|ch)\s*([0-9]{1,3})", normalized, flags=re.IGNORECASE)
    if chapter_match:
        return int(chapter_match.group(1))
    for tok in tokens:
        match = re.fullmatch(r"([0-9]{1,3})[.)]?", tok.strip(), flags=re.IGNORECASE)
        if match:
            return int(match.group(1))
    return 0


def extract_filename_chapter_index(normalized: str) -> int:
    """Номер главы, который имя файла называет ЯВНО: «Ch34», «CH№32», «KP.Ch34», «глава 34».

    Отдельно от `_extract_chapter_index` и строже его. Тот кормит собой лишь догадку о
    каноническом имени, поэтому ему не жалко ошибиться: он ищет «ch» где угодно внутри
    слова и, не найдя, берёт за главу любое отдельно стоящее число. Здесь число идёт в
    отказ, и цена ошибки другая — диктору называют главу, которой он не писал. На живых
    именах снисходительный разбор даёт `Rgikt_2.wav` -> 2, `Gamuk-3.wav` -> 3,
    `03-Za Maor.wav` -> 3, `KP_Rgikt_Tikhonov_2026-09-16.wav` -> 9; здесь все четыре — 0,
    то есть «в имени главы нет», и файл проходит.

    Строгость — в привязке к слову «глава», а не в разделителе после него. На проде через
    `№` написаны 20 имён из 152 — `KP_CH№32_Zar'_Klopov_Zotov.wav` и, реже, `C№` без
    «h» (`KP_C№19_Pohotliviy_Jrez_Zotov.wav`). Не знай разбор про `№`, самая ходовая
    раскладка имени проходила бы мимо сверки молча. Поэтому между словом и числом
    допускается любой набор разделителей, а точка считается границей слова («KP.Ch34.wav»).

    Одинокое «c» читается как «глава» ТОЛЬКО перед `№`/`#`: буква слишком частая, чтобы
    пускать её в отказ саму по себе. `(?![0-9])` в хвосте бережёт от того, чтобы откусить
    три цифры от длинного числа — дата `2026` не должна стать главой 202.

    Голое `_2` и дата номером главы не станут: без слова «глава» совпадения нет вовсе.

    0 значит «имя главу не называет» — сверять не с чем, отказа не будет.
    """
    match = re.search(
        r"(?:^|[\s_\-.])(?:глава|chapter|ch|c(?=[№#]))[\s№#.\-_]*([0-9]{1,3})(?![0-9])",
        normalized or "",
        flags=re.IGNORECASE,
    )
    return int(match.group(1)) if match else 0


def chapter_label_number(label: str) -> int:
    """Номер главы из её метки в форме: «Глава 34. Поздравляю…» -> 34, «» -> 0.

    Один экземпляр на весь путь: с этим номером сверяется имя файла и в предпросмотре
    (`apply_batch_overrides`), и при сохранении (`app/api/recording.py`). Разойдись эти
    два счёта — предпросмотр отказывал бы там, где загрузка принимает, или наоборот.
    """
    match = re.search(r"(\d+)", str(label or ""))
    return int(match.group(1)) if match else 0


def _detect_book_code(tokens: list[str], default_book_code: str) -> str:
    book_code = (default_book_code or "").strip()
    if not book_code and tokens:
        t0 = tokens[0].strip()
        if re.fullmatch(r"[A-Za-zА-Яа-я0-9]{2,20}", t0):
            book_code = t0
    return book_code or "BOOK"


def _is_structural_token(tok: str, *, book_code: str) -> bool:
    """Токен книги или главы — не содержание, угадывать по нему роль/актёра нечего."""
    if re.fullmatch(r"(?:глава|chapter|ch)[0-9]{0,3}", tok.lower()):
        return True
    if re.fullmatch(r"[0-9]{1,3}[.)]?", tok):
        return True
    return transliterate(normalize_word(tok)) == transliterate(normalize_word(book_code))


def _detect_actor_name(
    tokens: list[str], default_actor_name: str, normalize_role_label, *, book_code: str = "", was_fix: bool = False,
) -> str:
    actor_name = (default_actor_name or "").strip()
    if not actor_name and len(tokens) >= 2:
        # «Роль_fixN» без актёра — короткая пересъёмка, а не полное имя файла: единственное
        # оставшееся слово называет роль, а не диктора, угадывать актёра из него нельзя.
        content = [t for t in tokens if not _is_structural_token(t, book_code=book_code)]
        if was_fix and len(content) <= 1:
            return actor_name or "Unknown"
        candidate = normalize_role_label(tokens[-1])
        lc = candidate.lower()
        if lc not in FILENAME_STOPWORDS and not re.fullmatch(r"[0-9]{1,3}", candidate):
            actor_name = candidate
    return actor_name or "Unknown"


def _detect_role(tokens: list[str], *, book_code: str, actor_name: str, normalize_role_label) -> str:
    role_tokens: list[str] = []
    for tok in tokens:
        t = normalize_role_label(tok)
        if not t:
            continue
        low = t.lower()
        if low in FILENAME_STOPWORDS:
            continue
        if re.fullmatch(r"[0-9]{1,3}[.)]?", t):
            continue
        if re.fullmatch(r"(?:глава|chapter|ch)[0-9]{0,3}", low):
            continue
        # the code is typed in Latin («KP») while the book derives a Cyrillic one («КП»);
        # compared folded, or every Latin-typed filename reads its book code as a role
        if transliterate(normalize_word(t)) == transliterate(normalize_word(book_code)):
            continue
        if normalize_word(t) == normalize_word(actor_name):
            continue
        role_tokens.append(t)
    return normalize_role_label(" ".join(role_tokens[:4]))


def _build_audio_parse_warnings(*, ext: str, chapter: str, role: str, actor_name: str) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    if ext and ext not in AUDIO_EXTS:
        errors.append(f"unsupported_ext:{ext}")
    if not chapter:
        errors.append("chapter_not_found")
    if not role:
        # Not "Narrator": narration is the one role accepted without a coverage check,
        # so guessing it for an unreadable name would wave the file through unverified.
        errors.append("role_not_found")
    if actor_name == "Unknown":
        warnings.append("actor_fallback_unknown")
    return errors, warnings


def derive_book_code(title: str) -> str:
    """The code an actor writes in a filename, built from the title's initials.

    «Крылья полумрака» -> «КП», «Сказки волшебников 2» -> «СВ2». Digits are kept whole
    because they distinguish books in a series, and everything is compared folded to
    Latin so a code typed on either keyboard layout resolves to the same book.
    """
    parts: list[str] = []
    for token in re.split(r"[\s_\-–—:,.]+", (title or "").strip()):
        token = token.strip()
        if not token:
            continue
        if token.isdigit():
            parts.append(token)
        else:
            parts.append(token[0])
    # Upper-cased because a code is read and typed as one — «Крылья полумрака» is
    # «КП», not «Кп». Matching folds case anyway, so this is purely how it shows.
    return "".join(parts).upper()


def book_code_matches_title(title: str, code: str) -> bool:
    """Whether `code` names the book called `title`.

    Equality of derived codes, never containment: a substring rule ties books together
    the moment one title's letters turn up inside another's.
    """
    wanted = (code or "").strip()
    if not wanted or not (title or "").strip():
        return False
    return transliterate(wanted) == transliterate(derive_book_code(title))


def normalize_word(value: str) -> str:
    return re.sub(r"[^A-Za-zА-Яа-я0-9]+", "", (value or "").strip()) or "X"


def build_canonical_audio_filename(
    book_code: str,
    chapter: str,
    role: str,
    actor_name: str,
    original_filename: str,
    *,
    kind: str = TAKE,
    ordinal: int = 1,
    fix: int | None = None,
) -> str:
    """Имя файла по студийному стандарту — см. `app.services.audio_naming`.

    Позиционная сигнатура сохранена: её знает контракт внедрения зависимостей.
    """
    return canonical_audio_name(
        book_code=book_code, chapter=chapter, role=role, actor_name=actor_name,
        original_filename=original_filename, kind=kind, ordinal=ordinal, fix=fix,
    )


def parse_batch_audio_filename(
    filename: str,
    *,
    default_book_code: str = "",
    default_actor_name: str = "",
    normalize_role_label,
) -> dict:
    raw_name = (filename or "").strip()
    ext_match = re.search(r"\.([a-zA-Z0-9]+)$", raw_name)
    ext = ext_match.group(1).lower() if ext_match else ""
    stem = re.sub(r"\.[^.]+$", "", raw_name)
    normalized = stem.replace("—", "-").replace("–", "-")
    tokens = [t for t in re.split(r"[\s_\-]+", normalized) if t.strip()]
    # Токен фикса — не часть роли и не актёр, угадывать по нему нечего.
    was_fix = any(FIX_TOKEN.fullmatch(t) for t in tokens)
    tokens = [t for t in tokens if not FIX_TOKEN.fullmatch(t)]

    chapter_index = _extract_chapter_index(tokens, normalized)
    chapter = ""
    if chapter_index > 0:
        chapter = f"Глава{chapter_index}"

    book_code = _detect_book_code(tokens, default_book_code)
    actor_name = _detect_actor_name(tokens, default_actor_name, normalize_role_label, book_code=book_code, was_fix=was_fix)
    role = _detect_role(tokens, book_code=book_code, actor_name=actor_name, normalize_role_label=normalize_role_label)
    errors, warnings = _build_audio_parse_warnings(ext=ext, chapter=chapter, role=role, actor_name=actor_name)

    # Предпросмотр: фикс без номера показываем как `fix1`, точный номер назначит сохранение.
    fix = fix_number(raw_name)
    canonical_filename = build_canonical_audio_filename(
        book_code, chapter, role, actor_name, raw_name, fix=max(1, fix) if fix is not None else None,
    )
    return {
        "kind": TAKE,
        # kept because the override is about to replace `role`, and the two are worth comparing
        "filename_role": role,
        "original_filename": raw_name,
        "ext": ext,
        "book_code": book_code,
        "chapter": chapter,
        "chapter_index": chapter_index,
        "role": role,
        "actor_name": actor_name,
        "canonical_filename": canonical_filename,
        # Номер главы, который назвал сам файл — не то же самое, что глава, выбранная в
        # форме ниже (`apply_batch_overrides` сравнивает их), и не то же самое, что
        # `chapter_index` выше: тот угадывает главу для имени, а этот идёт в отказ,
        # поэтому считается строго. 0 значит «в имени номера нет».
        "filename_chapter_index": extract_filename_chapter_index(normalized),
        "errors": errors,
        "warnings": warnings,
        "ok": len(errors) == 0,
    }


def apply_batch_overrides(parsed: dict, override: dict | None, *, normalize_role_label) -> dict:
    if not isinstance(parsed, dict):
        return {}
    if not isinstance(override, dict):
        return parsed

    kind = str(override.get("kind") or parsed.get("kind") or TAKE).strip() or TAKE
    book_code = str(override.get("book_code") or parsed.get("book_code") or "").strip() or "BOOK"
    chapter = str(override.get("chapter") or parsed.get("chapter") or "").strip()
    role = normalize_role_label(str(override.get("role") or parsed.get("role") or "").strip())
    actor_name = normalize_role_label(str(override.get("actor_name") or parsed.get("actor_name") or "").strip())
    if not actor_name:
        actor_name = "Unknown"

    chapter_index = chapter_label_number(chapter)
    parsed["kind"] = kind
    parsed["book_code"] = book_code
    parsed["chapter"] = chapter
    parsed["chapter_index"] = chapter_index
    parsed["role"] = role
    parsed["actor_name"] = actor_name
    original_filename = str(parsed.get("original_filename") or "")
    # Предпросмотр: фикс без номера показываем как `fix1`, точный номер назначит сохранение.
    fix = fix_number(original_filename) if kind != AUDITION else None
    parsed["canonical_filename"] = build_canonical_audio_filename(
        book_code,
        chapter,
        role,
        actor_name,
        original_filename,
        kind=kind,
        fix=max(1, fix) if fix is not None else None,
    )

    errors: list[str] = []
    warnings = list(parsed.get("warnings") or [])
    ext = str(parsed.get("ext") or "").lower().strip()
    if ext and ext not in AUDIO_EXTS:
        errors.append(f"unsupported_ext:{ext}")
    if not chapter and kind != AUDITION:
        errors.append("chapter_not_found")
    if not role:
        errors.append("role_required")
    filename_role = str(parsed.get("filename_role") or "").strip()
    if role and filename_role and not filename_names_role(filename_role, role):
        # a warning, never a refusal: the parser is guessing, the dictor is not
        warnings.append("role_name_mismatch")
    if not actor_name:
        warnings.append("actor_fallback_unknown")
    # Главу задаёт форма, а не имя файла: диктор выбирает её из списка глав книги.
    # Пока расхождение молчало, файл ложился в чужую главу, где его роль не говорит ни
    # слова: опись показывала «Ничья роль», а распознавание было оплачено впустую
    # (Тихонов Дмитрий, 2026-09-16). Поэтому расхождение — отказ, который диктор может
    # снять сам, если знает, что делает.
    filename_chapter = int(parsed.get("filename_chapter_index") or 0)
    confirmed = bool((override or {}).get("confirm_chapter"))
    if kind != AUDITION and filename_chapter and chapter_index and filename_chapter != chapter_index and not confirmed:
        errors.append("chapter_mismatch")
    parsed["errors"] = errors
    parsed["warnings"] = warnings
    parsed["ok"] = len(errors) == 0
    return parsed


def next_ordinal(db, *, kind: str, book_code: str, chapter: str, role: str, actor_name: str) -> int:
    """Какая это по счёту запись той же роли тем же актёром.

    Считается и для проб, и для дублей. Раньше номер нужен был только пробам —
    остальное разводил uuid в начале ключа. Раскладка по книге и главе uuid убрала,
    и теперь номер — единственное, что стоит между вторым дублём и затиранием
    первого: ключ вычисляется из книги, главы, роли и актёра, а `write_stream`
    открывает целевой файл на запись, ничего не спрашивая.

    Дубли считаются в пределах главы, пробы — по всей книге: у пробы главы может
    не быть вовсе, роль ещё не назначена.
    """
    query = db.query(AudioFile).filter(
        AudioFile.kind == (kind or TAKE),
        AudioFile.book_code == (book_code or "").strip(),
        AudioFile.role == (role or "").strip(),
        AudioFile.actor_name == (actor_name or "").strip(),
    )
    if str(kind or TAKE) != AUDITION:
        query = query.filter(AudioFile.chapter == (chapter or "").strip())
    # Следующий после наибольшего УЖЕ ВЫДАННОГО номера, а не «сколько строк + 1»: актёр
    # вправе удалить свою свежую загрузку, и после удаления первого из `base`, `_2`
    # счёт давал бы `_2` снова — тот же ключ, молча затёртый файл и две строки на один звук.
    # Фиксы — короткие пересъёмки, а не вторая полная запись роли; свою нумерацию
    # они получают отдельно (`next_fix_number`) и в счёт дублей не идут.
    highest = 0
    for item in query.all():
        fix, ordinal = name_suffixes(item.canonical_filename)
        if fix is None and fix_number(item.canonical_filename) is None:
            highest = max(highest, ordinal)
    return highest + 1


def next_fix_number(db, *, book_code: str, chapter: str, role: str, actor_name: str, wanted: int) -> int:
    """Номер фикса той же роли/главы/актёра: `wanted`, если свободен, иначе наименьший свободный ≥ 1."""
    query = db.query(AudioFile).filter(
        AudioFile.kind == TAKE,
        AudioFile.book_code == (book_code or "").strip(),
        AudioFile.chapter == (chapter or "").strip(),
        AudioFile.role == (role or "").strip(),
        AudioFile.actor_name == (actor_name or "").strip(),
    )
    taken = {
        n for n in (fix_number(item.canonical_filename) for item in query.all())
        if n is not None and n >= 1
    }
    if int(wanted) >= 1 and int(wanted) not in taken:
        return int(wanted)
    candidate = 1
    while candidate in taken:
        candidate += 1
    return candidate


#: сколько занятых ключей подряд пропустить, прежде чем признать, что свободного не будет
MAX_KEY_ATTEMPTS = 1000

#: до скольких символов `safe_name` (`app/services/shared_runtime.py`) обрезает имя
STORAGE_NAME_LIMIT = 120

#: различающий хвост имени: `_fixN`, `_M` и расширение
_DISTINGUISHING_TAIL = re.compile(r"(?:_fix\d+)?(?:_\d+)?(?:\.[A-Za-z0-9]{1,8})?$", re.IGNORECASE)


def storage_file_name(canonical_filename: str, safe_name) -> str:
    """Имя файла в ключе хранилища: обрезается основа, а `_fixN`/`_M` и расширение остаются.

    `safe_name` режет имя до 120 символов. У длинной роли или актёра под нож шёл как раз
    хвост — номер и расширение, — и второй файл получал ключ первого: цикл поиска
    свободного ключа крутился вечно, а без него файл затёрся бы. Каноническое имя при
    этом не меняется — меняется только то, как оно ложится в ключ.
    """
    name = str(canonical_filename or "")
    match = _DISTINGUISHING_TAIL.search(name)
    tail = match.group(0) if match else ""
    stem = name[: len(name) - len(tail)] if tail else name
    base = safe_name(stem) if stem else ""
    return base[: max(1, STORAGE_NAME_LIMIT - len(tail))] + tail


def _storage_key_taken(db, key: str) -> bool:
    """Занят ли ключ хранилища — живой строкой или файлом на локальном диске."""
    if db.query(AudioFile.id).filter(AudioFile.stored_key == key).first() is not None:
        return True
    # Подменённые в тестах хранилища умеют не всё; без проверки диска остаётся проверка базы.
    exists = getattr(audio_storage, "file_exists", None)
    return bool(exists(key)) if callable(exists) else False


def store_audio_file(
    db,
    *,
    payload: bytes | None = None,
    source=None,
    mime_type: str,
    original_filename: str,
    book_code: str,
    chapter: str,
    role: str,
    actor_name: str,
    line_index: int | None = None,
    kind: str = TAKE,
    safe_name,
    size_hint: int = 0,
) -> AudioFile:
    # Короткая пересъёмка называется по своему номеру, а не встаёт в очередь дублей
    # роли: диктор прислал `..._fixN` — значит это правка одной реплики, не вторая
    # полная запись.
    fix = fix_number(original_filename) if kind != AUDITION else None
    if fix is not None:
        ordinal = 1
        fix = next_fix_number(
            db, book_code=book_code, chapter=chapter, role=role, actor_name=actor_name, wanted=fix,
        )
    else:
        ordinal = next_ordinal(
            db, kind=kind, book_code=book_code, chapter=chapter, role=role, actor_name=actor_name,
        )
    canonical_filename = build_canonical_audio_filename(
        book_code, chapter, role, actor_name, original_filename, kind=kind, ordinal=ordinal, fix=fix,
    )
    key = audio_mirror.storage_key(book_code, chapter, storage_file_name(canonical_filename, safe_name), kind)
    # Номер из базы — ещё не гарантия: на диске может лежать файл без строки (сирота
    # после неудачного удаления, ручная раскладка), а `write_stream` затирает цель
    # через `os.replace`, ничего не спрашивая. Пока ключ занят — берём следующий номер.
    attempts = 0
    while _storage_key_taken(db, key):
        attempts += 1
        if attempts > MAX_KEY_ATTEMPTS:
            # Громкий отказ вместо зависшего потока запроса: сюда можно попасть, только
            # если имя в ключе перестало различать номера (см. `storage_file_name`).
            raise RuntimeError(f"storage_key_exhausted: {key}")
        if fix is not None:
            fix += 1
        else:
            ordinal += 1
        canonical_filename = build_canonical_audio_filename(
            book_code, chapter, role, actor_name, original_filename, kind=kind, ordinal=ordinal, fix=fix,
        )
        key = audio_mirror.storage_key(book_code, chapter, storage_file_name(canonical_filename, safe_name), kind)
    # Диск сервера — теперь единственное хранилище приёма, и забитый под ноль
    # он роняет и сайт, и воркер. Проверяем резерв до записи: честный отказ
    # диктору дешевле, чем ронять процесс на полпути к концу файла.
    audio_mirror.ensure_room(
        size_hint,
        free=audio_storage.free_bytes(audio_storage.local_root()),
        reserve_bytes=int(settings.local_reserve_gb) * 1024**3,
    )
    # Только локальный диск: NFS в пути приёма нет, поэтому загрузка не может
    # ни повиснуть на мёртвом монтировании, ни провалиться из-за него. Копия
    # на NAS появляется следом, фоновым зеркалированием.
    if source is not None:
        size, digest = audio_storage.write_stream(key, source)
    elif payload is not None:
        digest = audio_storage.write_file(key, payload)
        size = len(payload)
    else:
        raise ValueError("payload_or_source")

    # Спрашиваем у самого файла, а не у его имени: треть секунды на восемьдесят мегабайт.
    probe = probe_audio_file(audio_storage.resolve_path(key))
    item = AudioFile(
        duration_seconds=float(probe.get("duration_seconds") or 0.0),
        sample_rate=int(probe.get("sample_rate") or 0),
        channels=int(probe.get("channels") or 0),
        codec=str(probe.get("codec") or ""),
        book_code=book_code.strip(),
        original_filename=original_filename,
        canonical_filename=canonical_filename,
        stored_key=key,
        mime_type=mime_type or "audio/wav",
        size_bytes=size,
        md5=digest,
        chapter=chapter.strip(),
        role=role.strip(),
        actor_name=actor_name.strip(),
        line_index=line_index,
        kind=kind,
        status="uploaded",
    )
    db.add(item)
    # Второй файл той же пачки должен увидеть уже принятый первый — иначе оба
    # посчитают себя первыми и получат один и тот же ключ хранилища, затерев друг друга.
    db.flush()
    return item

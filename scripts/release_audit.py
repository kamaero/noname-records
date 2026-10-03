#!/usr/bin/env python3
"""Сторож публичного релиза Noname Records: в репозитории не должно быть секретов,
реальных людей, путей и следов студии-прародителя — ни в рабочем дереве, ни в истории.

    python scripts/release_audit.py --tree --history --people-from /путь/к/базе.db

Список людей берётся из базы в момент проверки (имена учёток и Telegram id) и никуда не
записывается. Длинная русская проза (похожая на цитату книги) — отчёт для глаз, а не
блокировка: тексты интерфейса и промптов дают ложные срабатывания.
Код выхода: 0 — блокирующих находок нет, 1 — есть.
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

SECRET_PATTERNS = [
    re.compile(r"\b\d{8,10}:AA[A-Za-z0-9_-]{30,}"),                      # токен Telegram-бота
    re.compile(r"\bsk-(?:or-v1-|ant-|proj-)?[A-Za-z0-9_-]{32,}"),       # OpenAI/OpenRouter/Anthropic
    re.compile(r"\bAIza[0-9A-Za-z_-]{30,}"),                             # Google
    re.compile(r"\bghp_[A-Za-z0-9]{30,}"),                               # GitHub
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\b[A-Z][A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD|PASSWORD_HASH)\s*=\s*['\"]?([^\s'\"#]{20,})"),
]
#: значения-заглушки, которые законно стоят в примерах и тестах — ТОЧНО, а не по подстроке:
#: подстрока «example» пропустила бы настоящий ключ, в котором она случайно есть
PLACEHOLDERS = {"change-this-secret", "sk-supersecret-do-not-leak"}
#: «KEY=$VAR», «${VAR:-}», «$(...)» в скриптах установки — подстановка, а не значение.
#: Только настоящий синтаксис оболочки: «начинается с $ или {» пропустило бы секрет.
SHELL_SUBST = re.compile(r"\$(?:\{[A-Za-z_][A-Za-z0-9_]*(?::-[^}]*)?\}|\([^)]*\)|[A-Za-z_][A-Za-z0-9_]*)")

#: следы студии-прародителя и запрещённые к публикации пути/строки
FORBIDDEN_PATHS = (re.compile(r"(^|/)docs/superpowers/"), re.compile(r"\.(db|db-wal|db-shm|sqlite)$"),
                   re.compile(r"voices_import"), re.compile(r"pargoron", re.I))
FORBIDDEN_TEXT = [re.compile(p, re.I) for p in (
    r"buchteam", r"xkrbot", r"prodsvc", r"/srv/prod", r"/mnt/buchteam", r"/var/www/voices", r"\b11504\b",
    r"voices\.xkrbot",
)]

PROSE = re.compile(r"[А-Яа-яЁё][а-яё]+(?:[ ,—–-]+[А-Яа-яЁё]+){14,}")
PROSE_SKIP = ("app/prompts/", "frontend/src/", "README", "docs/", ".md")


@dataclass(frozen=True)
class Finding:
    kind: str      # secret | person | forbidden | book | prose
    path: str
    line: int
    note: str      # что найдено — без самого значения секрета

    def blocking(self) -> bool:
        return self.kind != "prose"


def blocking(findings: list[Finding]) -> list[Finding]:
    return [f for f in findings if f.blocking()]


def _column(con, table: str, column: str) -> set[str]:
    """Значения колонки; нет таблицы или колонки (другая версия базы) — пусто, а не падение."""
    try:
        return {str(r[0]).strip() for r in con.execute(f"select {column} from {table}") if r[0]}
    except sqlite3.OperationalError:
        return set()


#: логины, которые есть в любой установке и никого не выдают
GENERIC_LOGINS = {"admin", "owner", "author", "agent", "dictor", "test", "root", "user", "claude_svc"}


@dataclass
class People:
    """Реальные люди как множества слов: проверка строки — поиск в множествах, а не тысячи
    регулярок (склеенная регулярка из тысяч имён в Python работает минутами)."""
    fulls: set = field(default_factory=set)      # имя целиком, слова через пробел
    pairs: set = field(default_factory=set)      # (слово, слово) в обоих порядках
    handles: set = field(default_factory=set)    # ники и логины, в нижнем регистре
    ids: set = field(default_factory=set)
    longest: int = 0

    def __bool__(self) -> bool:
        return bool(self.fulls or self.pairs or self.handles or self.ids)

    def person_in(self, line: str) -> bool:
        words = _WORDS.findall(line)
        for i in range(len(words)):
            if i + 1 < len(words) and (words[i], words[i + 1]) in self.pairs:
                return True
            for n in range(2, self.longest + 1):
                if i + n <= len(words) and " ".join(words[i:i + n]) in self.fulls:
                    return True
        return any(tok.lower().lstrip("@") in self.handles for tok in _HANDLES.findall(line))

    def id_in(self, line: str) -> bool:
        return any(tok in self.ids for tok in _DIGITS.findall(line))


_WORDS = re.compile(r"[^\W\d_]+(?:[-'’][^\W\d_]+)*")
_HANDLES = re.compile(r"@?[\w][\w.]*[\w]")
_DIGITS = re.compile(r"(?<!\d)\d{6,}(?!\d)")


def load_people(db: Path | None) -> People:
    """Реальные люди из базы: учётки, Telegram, контакты бота и каст (актёр без учётки —
    тоже человек). Имя целиком ищется как есть; первые два слова — ещё и в обратном
    порядке («Ольга Ветрова» ↔ «Ветрова Ольга»), если оба не короче трёх букв: короче —
    ложные срабатывания."""
    people = People()
    if db is None:
        return people
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        names = (_column(con, "users", "display_name") | _column(con, "telegram_auth_accounts", "display_name")
                 | _column(con, "bot_contacts", "tg_name") | _column(con, "characters", "actor_name"))
        handles = (_column(con, "bot_contacts", "username") | _column(con, "dictor_profiles", "telegram_username")
                   | _column(con, "users", "login"))
        ids = (_column(con, "telegram_auth_accounts", "telegram_user_id") | _column(con, "bot_contacts", "telegram_user_id"))
    finally:
        con.close()
    for raw in names:
        words = _WORDS.findall(raw)
        if len(words) < 2 or len(" ".join(words)) < 5:
            continue  # одно слово — имя или ник, ищется слишком широко
        people.fulls.add(" ".join(words))
        people.longest = max(people.longest, len(words))
        a, b = words[0], words[1]
        if len(a) >= 3 and len(b) >= 3:
            people.pairs |= {(a, b), (b, a)}
    for handle in handles:
        handle = handle.lstrip("@").lower()
        if len(handle) >= 5 and handle not in GENERIC_LOGINS and not handle.startswith("tg_"):
            people.handles.add(handle)
    people.ids = {i for i in ids if len(i) >= 6 and i.isdigit()}
    return people


SHINGLE = 8
#: режим релиза: без декодера шрифтов — отказ, а не тихий пропуск
STRICT_FONTS = False
AUTHOR_TEXT = (("v2_segments", "text"), ("script_chapters", "source_text"), ("lore_articles", "body"),
               ("author_characters", "description"), ("book_illustrations", "context"))
OK_MARK = "release-audit: ok"


def _shingles(text: str):
    words = [w.lower().replace("ё", "е") for w in _WORDS.findall(text)]
    for i in range(len(words) - SHINGLE + 1):
        yield hash(" ".join(words[i:i + SHINGLE]))


def load_books(db: Path | None) -> set[int]:
    """Восемь слов подряд из любой книги базы — отпечатки (хеши), сам текст не хранится.

    Восемь слов не совпадают случайно, а цитата в фикстуре теста чаще всего — реплика из
    одной-двух строк, которую «длинная проза» не ловит."""
    if db is None:
        return set()
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        prints: set[int] = set()
        # Текст автора лежит не только в разметке: исходники глав, энциклопедия мира,
        # описания персонажей и подписи к иллюстрациям — всё это его проза.
        for table, column in AUTHOR_TEXT:
            for text in _column(con, table, column):
                prints.update(_shingles(text))
        return prints
    finally:
        con.close()


def _book_lines(path: str, text: str, books: set[int]) -> list[Finding]:
    """Окно в восемь слов идёт по всему файлу, а не по строке: цитату в тестах часто
    режут на несколько склеиваемых строковых литералов."""
    if not books:
        return []
    # Строку, которую человек проверил (числительные, общие обороты), помечают явно:
    # «# release-audit: ok». Её слова в окна не идут.
    checked = {no for no, line in enumerate(text.splitlines(), 1) if OK_MARK in line}
    marks = [(m.group(0).lower().replace("ё", "е"), text.count("\n", 0, m.start()) + 1)
             for m in _WORDS.finditer(text)]
    marks = [(w, no) for w, no in marks if no not in checked]
    lines = set()
    for i in range(len(marks) - SHINGLE + 1):
        if hash(" ".join(w for w, _ in marks[i:i + SHINGLE])) in books:
            lines.add(marks[i][1])
    return [Finding("book", path, no, "восемь слов подряд из книги в базе") for no in sorted(lines)]


def _scan_text(path: str, text: str, people, *, prose: bool = True) -> list[Finding]:
    found: list[Finding] = []
    for no, line in enumerate(text.splitlines(), 1):
        for pattern in SECRET_PATTERNS:
            m = pattern.search(line)
            if m:
                value = m.group(m.lastindex or 0) if m.lastindex else m.group(0)
                # «KEY=$VAR» в скриптах установки — подстановка, а не значение
                if not SHELL_SUBST.fullmatch(value) and value not in PLACEHOLDERS:
                    found.append(Finding("secret", path, no, f"похоже на секрет ({pattern.pattern[:24]}…)"))
                break
        for pattern in FORBIDDEN_TEXT:
            if pattern.search(line):
                found.append(Finding("forbidden", path, no, f"запрещённая строка «{pattern.pattern}»"))
        if people.person_in(line):
            found.append(Finding("person", path, no, "имя или ник реального человека из базы"))
        if people.id_in(line):
            found.append(Finding("person", path, no, "Telegram id реального человека из базы"))
        if prose and not path.startswith(PROSE_SKIP) and not path.endswith(".md") and PROSE.search(line):
            found.append(Finding("prose", path, no, "длинная русская проза — проверить, не цитата ли книги"))
    return found


def _forbidden_path(path: str) -> Finding | None:
    for pattern in FORBIDDEN_PATHS:
        if pattern.search(path):
            return Finding("forbidden", path, 0, f"запрещённый путь «{pattern.pattern}»")
    return None


def _binary_text(path: Path) -> str:
    return _bytes_text(path.read_bytes(), path.suffix)


def _bytes_text(raw: bytes, suffix: str = "") -> str:
    parts = [m.group(0).decode("ascii") for m in re.finditer(rb"[\x20-\x7e]{6,}", raw)]
    parts += [m.group(0).decode("utf-8", "ignore") for m in re.finditer(rb"(?:[\xd0\xd1][\x80-\xbf]|[\x20-\x7e]){6,}", raw)]
    if suffix.lower() in {".woff", ".woff2", ".ttf", ".otf"}:
        import io
        try:
            from fontTools.ttLib import TTFont  # сжатый woff2 снаружи не читается
        except ImportError:
            if STRICT_FONTS:
                raise SystemExit("Нужен fontTools (pip install fonttools brotli): без него имена шрифтов не проверить.")
        else:
            try:
                parts += [r.toUnicode() for r in TTFont(io.BytesIO(raw))["name"].names]
            except Exception:
                parts.append("")
    return "\n".join(parts)


def audit_tree(root: Path, *, people_db: Path | None = None, books_db: Path | None = None) -> list[Finding]:
    """Файлы рабочего дерева, кроме того, что git игнорирует."""
    root = Path(root)
    people = load_people(people_db)
    books = load_books(books_db)
    listed = subprocess.run(["git", "-C", str(root), "ls-files", "--cached", "--others", "--exclude-standard"],
                            check=True, capture_output=True, text=True).stdout.splitlines()
    found: list[Finding] = []
    for rel in listed:
        if rel == "scripts/release_audit.py" or rel == "tests/test_release_audit.py":
            continue  # сами шаблоны поиска — не находки
        path = root / rel
        if not path.is_file():
            continue
        bad = _forbidden_path(rel)
        if bad:
            found.append(bad)
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            # Бинарный файл: смотрим печатные куски и имена шрифта — бренд, ссылка или
            # имя в метаданных иначе прошли бы молча.
            text = _binary_text(path)
        except OSError:
            continue
        found += _scan_text(rel, text, people)
        found += _book_lines(rel, text, books)
    return found


def audit_history(root: Path, *, people_db: Path | None = None, books_db: Path | None = None) -> list[Finding]:
    """Все добавленные строки во всех коммитах всех веток — то, что увидит любой клон."""
    people = load_people(people_db)
    books = load_books(books_db)
    log = subprocess.run(["git", "-C", str(root), "log", "--all", "-p", "--format=@@commit %h", "--no-color"],
                         check=True, capture_output=True, text=True, errors="replace").stdout
    found: list[Finding] = []
    commit, path, added = "", "", []

    def flush():
        if path and added and path not in ("scripts/release_audit.py", "tests/test_release_audit.py"):
            for f in _scan_text(f"{commit}:{path}", "\n".join(added), people, prose=False):
                found.append(f)
            found.extend(_book_lines(f"{commit}:{path}", "\n".join(added), books))
            bad = _forbidden_path(path)
            if bad:
                found.append(Finding("forbidden", f"{commit}:{path}", 0, bad.note))

    # Патчи git не показывают двоичные файлы — их объекты читаем отдельно, все, что есть
    # в любой ветке: клон получит каждый.
    objects = subprocess.run(["git", "-C", str(root), "rev-list", "--all", "--objects"],
                             check=True, capture_output=True, text=True).stdout.splitlines()
    for line in objects:
        sha, _, name = line.partition(" ")
        if not name or name in ("scripts/release_audit.py", "tests/test_release_audit.py"):
            continue
        if subprocess.run(["git", "-C", str(root), "cat-file", "-t", sha], capture_output=True, text=True).stdout.strip() != "blob":
            continue
        raw = subprocess.run(["git", "-C", str(root), "cat-file", "blob", sha], capture_output=True).stdout
        try:
            raw.decode("utf-8")
            continue  # текст уже прошёл через патчи
        except UnicodeDecodeError:
            found += _scan_text(f"{sha[:7]}:{name}", _bytes_text(raw, Path(name).suffix), people, prose=False)

    messages = subprocess.run(["git", "-C", str(root), "log", "--all", "--format=@@msg %h%n%B"],
                              check=True, capture_output=True, text=True, errors="replace").stdout
    commit_id = ""
    for chunk in messages.split("@@msg ")[1:]:
        commit_id, _, body = chunk.partition("\n")
        found += _scan_text(f"{commit_id.strip()}:<сообщение коммита>", body, people, prose=False)

    for line in log.splitlines():
        if line.startswith("@@commit "):
            flush(); commit, path, added = line.split()[1], "", []
        elif line.startswith("+++ "):
            flush(); path = line[6:] if line.startswith("+++ b/") else ""; added = []
        elif line.startswith("+") and not line.startswith("+++"):
            added.append(line[1:])
    flush()
    return found


def format_report(findings: list[Finding]) -> str:
    if not findings:
        return "Находок нет."
    lines = [f"{f.kind:9} {f.path}:{f.line}  {f.note}" for f in sorted(findings, key=lambda f: (f.kind, f.path, f.line))]
    hard = len(blocking(findings))
    lines.append(f"\nВсего: {len(findings)}, блокирующих: {hard}.")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--tree", action="store_true")
    parser.add_argument("--history", action="store_true")
    parser.add_argument("--people-from", default="")
    parser.add_argument("--books-from", default="", help="база, из книг которой нельзя публиковать цитаты")
    parser.add_argument("--release", action="store_true",
                        help="перед публикацией: без списка людей и книг (или с пустыми) — отказ, а не «ноль находок»")
    args = parser.parse_args(argv)
    if not (args.tree or args.history):
        args.tree = args.history = True
    db = Path(args.people_from) if args.people_from else None
    books = Path(args.books_from) if args.books_from else None
    if args.release:
        global STRICT_FONTS
        STRICT_FONTS = True
        try:
            import fontTools  # noqa: F401
        except ImportError:
            print("Режим релиза: нужен fontTools (pip install fonttools brotli).", file=sys.stderr)
            return 2
        people, prints = load_people(db), load_books(books)
        if not people or not prints:
            print("Режим релиза: нужны --people-from и --books-from с непустыми данными — "
                  "без них «ноль находок» ничего не значит.", file=sys.stderr)
            return 2
        print(f"Проверено против: людей — имён {len(people.fulls)}, ников {len(people.handles)}, "
              f"Telegram id {len(people.ids)}; книг — отпечатков {len(prints)}.")
        con = sqlite3.connect(f"file:{books}?mode=ro", uri=True)
        for table, column in AUTHOR_TEXT:
            print(f"  источник текста {table}.{column}: строк {len(_column(con, table, column))}")
        con.close()
    findings = []
    if args.tree:
        findings += audit_tree(Path(args.root), people_db=db, books_db=books)
    if args.history:
        findings += audit_history(Path(args.root), people_db=db, books_db=books)
    print(format_report(findings))
    return 1 if blocking(findings) else 0


if __name__ == "__main__":
    sys.exit(main())

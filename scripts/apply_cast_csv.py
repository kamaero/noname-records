#!/usr/bin/env python
"""Приложить готовый каст (CSV «актёр,роли») к книге и/или к ростеру автора.

Зачем: цикл книг одного автора озвучивает один и тот же состав. Каст первой книги —
production ground truth, и для второй его не переназначают заново, а переносят.

Файл: строка на актёра, роли через запятую во второй ячейке:

    Михаил Пестов,"Рассказчик, Сорокопут"

Имена сверяются свёрткой `author_profile.normalize_name` (регистр, ударения, пробелы)
по имени персонажа и его алиасам, плюс запасной заход без скобочного пояснения
(«Астрадианец 1 (Владыка Всея Плесени)» → «Астрадианец 1»).

Ничего не перетирает: роль, у которой актёр уже стоит, остаётся как есть и попадает в
отчёт отдельной строкой. По умолчанию — только отчёт; запись включает `--apply`.

    python scripts/apply_cast_csv.py --csv "Каст СВ - Лист1.csv" --book-id <id>
    python scripts/apply_cast_csv.py --csv … --book-id <id> --apply
    python scripts/apply_cast_csv.py --csv … --author-slug belozerov --apply   # в ростер автора
"""
from __future__ import annotations

import argparse
import csv
import re
import sys

sys.path.insert(0, ".")

from app.db import SessionLocal  # noqa: E402
from app.models import Author, AuthorCharacter, BookBudget, Character, ScriptBook, ScriptLog  # noqa: E402
from app.services.author_profile import normalize_name  # noqa: E402
from app.services.telegram import names_match  # noqa: E402
from app.v2.cast_ops import split_aliases  # noqa: E402

#: рассказчик — не персонаж: его актёр живёт в смете книги (`book_budget`)
NARRATOR = "Рассказчик"


def _keys(name: str, aliases: list[str]) -> set[str]:
    """Ключи, по которым имя узнаётся: само имя, алиасы и вариант без скобок."""
    out: set[str] = set()
    for value in [name, *aliases]:
        key = normalize_name(value)
        if key:
            out.add(key)
        bare = normalize_name(re.sub(r"\([^)]*\)", " ", str(value or "")))
        if bare:
            out.add(bare)
    return out


def _tokens(keys: set[str]) -> set[str]:
    """Слова всех написаний имени — по ним ищется редкое, опознающее персонажа слово."""
    out: set[str] = set()
    for key in keys:
        out |= {word for word in re.split(r"[^\w\u0400-\u04ff]+", key) if len(word) >= 4}
    return out


def rare_word_match(role_keys: set[str], index: list[tuple]) -> object | None:
    """Второй заход: книга пишет роль короче или с другим пояснением.

    «пёс Тифон» → «Тифон», «Тётя Динхубия» → «Динхубия», «Балнодгг Похкупго» →
    «Балнодгг» (алиас «Похкупго»). Берётся только слово, которое в книге принадлежит
    РОВНО одному персонажу: «Дегатти» носит полсемьи, и по нему сходиться нельзя.
    """
    role_words = _tokens(role_keys)
    if not role_words:
        return None
    owners: dict[str, set[int]] = {}
    for position, (keys, _row) in enumerate(index):
        for word in _tokens(keys):
            owners.setdefault(word, set()).add(position)
    hits = {next(iter(owners[w])) for w in role_words if w in owners and len(owners[w]) == 1}
    if len(hits) != 1:
        return None
    return index[hits.pop()][1]


def read_cast(path: str) -> list[tuple[str, str]]:
    """[(роль, актёр)] в порядке файла."""
    pairs: list[tuple[str, str]] = []
    with open(path, encoding="utf-8-sig", newline="") as handle:
        for row in csv.reader(handle):
            if len(row) < 2:
                continue
            actor = row[0].strip()
            if not actor:
                continue
            for role in row[1].split(","):
                role = role.strip()
                if role:
                    pairs.append((role, actor))
    return pairs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", required=True)
    parser.add_argument("--book-id", default="", help="приложить к персонажам книги")
    parser.add_argument("--author-slug", default="", help="положить в ростер автора (для будущих книг)")
    parser.add_argument("--apply", action="store_true", help="записать (без него — только отчёт)")
    args = parser.parse_args()

    pairs = read_cast(args.csv)
    print(f"В файле: {len(pairs)} ролей у {len({a for _, a in pairs})} актёров\n")

    with SessionLocal() as db:
        if args.book_id:
            _apply_to_book(db, args.book_id, pairs, apply=args.apply)
        if args.author_slug:
            _apply_to_roster(db, args.author_slug, pairs, apply=args.apply)
        if args.apply:
            db.commit()
            print("\nЗаписано.")
        else:
            db.rollback()
            print("\nОтчёт без записи (--apply, чтобы записать).")
    return 0


def _apply_to_book(db, book_id: str, pairs: list[tuple[str, str]], *, apply: bool) -> None:
    book = db.get(ScriptBook, book_id)
    if book is None:
        print(f"Книги {book_id} нет", file=sys.stderr)
        return
    rows = db.query(Character).filter(Character.book_id == book_id).all()
    index = [(_keys(str(r.name or ""), split_aliases(getattr(r, "aliases", ""))), r) for r in rows]

    # Одного человека книга и файл пишут по-разному («Уткина Наташа» — «Наташа
    # Уткина»). `names_match` считает их одним, но в таблице они разошлись бы двумя
    # строками, а пересечения ролей и смета группируют по строке имени. Поэтому у уже
    # знакомого книге актёра берём её написание.
    known = sorted({str(r.actor_name or "").strip() for r in rows if str(r.actor_name or "").strip()})

    def spelling(actor: str) -> str:
        return next((k for k in known if names_match(actor, k)), actor)

    set_now: list[tuple[str, str, str]] = []
    guesses: list[tuple[str, str, str]] = []
    kept: list[tuple[str, str, str]] = []
    missing: list[tuple[str, str]] = []
    narrator_note = _narrator(db, book, pairs, apply=apply, spelling=spelling)
    for role, actor in pairs:
        if normalize_name(role) == normalize_name(NARRATOR):
            continue
        key = _keys(role, [])
        match = next((r for keys, r in index if keys & key), None)
        guessed = False
        if match is None:
            match = rare_word_match(key, index)
            guessed = match is not None
        if match is None:
            missing.append((role, actor))
            continue
        current = str(match.actor_name or "").strip()
        if current:
            kept.append((str(match.name), current, actor))
            continue
        written = spelling(actor)
        (guesses if guessed else set_now).append((str(match.name), written, role))
        if apply:
            match.actor_name = written
            db.add(match)

    print(f"=== книга «{book.display_title or book.title}» ===")
    if narrator_note:
        print(narrator_note)
    print(f"назначено по касту: {len(set_now)}")
    for name, actor, role in set_now:
        note = "" if normalize_name(name) == normalize_name(role) else f"  ← «{role}»"
        print(f"  + {name} — {actor}{note}")
    if guesses:
        print(f"\nсопоставлено по редкому слову — проверьте глазами: {len(guesses)}")
        for name, actor, role in guesses:
            print(f"  ~ {name} — {actor}  ← «{role}»")
    if kept:
        print(f"\nуже занято (не трогаю): {len(kept)}")
        for name, current, csv_actor in kept:
            mark = "" if normalize_name(current) == normalize_name(csv_actor) else f"  ≠ в касте {csv_actor}"
            print(f"  = {name} — {current}{mark}")
    if missing:
        print(f"\nроли из файла, которых нет в книге: {len(missing)}")
        for role, actor in missing:
            print(f"  ? {role} ({actor})")
    silent = [r for _, r in index if not str(r.actor_name or "").strip()]
    print(f"\nперсонажей книги без актёра после этого: {len(silent) - len(set_now) - len(guesses)} из {len(rows)}")
    if apply:
        db.add(ScriptLog(book_id=book.id, level="info",
                         message=f"Каст перенесён из файла: назначено {len(set_now) + len(guesses)} ролей"))


def _narrator(db, book, pairs: list[tuple[str, str]], *, apply: bool, spelling) -> str:
    """Актёр рассказчика живёт в смете книги, а не среди персонажей."""
    wanted = next((a for role, a in pairs if normalize_name(role) == normalize_name(NARRATOR)), "")
    if not wanted:
        return ""
    budget = db.query(BookBudget).filter(BookBudget.book_id == book.id).first()
    current = str(getattr(budget, "narrator_actor_name", "") or "").strip()
    if current:
        return f"рассказчик: уже {current} (в файле {wanted}) — не трогаю"
    written = spelling(wanted)
    if apply:
        if budget is None:
            budget = BookBudget(book_id=book.id)
            db.add(budget)
        budget.narrator_actor_name = written
    return f"рассказчик: {written} (в смете книги; бот об этом не пишет — позовите сами)"


def _apply_to_roster(db, slug: str, pairs: list[tuple[str, str]], *, apply: bool) -> None:
    author = db.query(Author).filter(Author.slug == slug.strip().lower()).first()
    if author is None:
        print(f"Автора «{slug}» нет", file=sys.stderr)
        return
    rows = db.query(AuthorCharacter).filter(AuthorCharacter.author_id == author.id).all()
    index = [(_keys(str(r.canonical_name or ""), split_aliases(r.aliases)), r) for r in rows]

    set_now, kept, created = [], [], []
    for role, actor in pairs:
        key = _keys(role, [])
        match = next((r for keys, r in index if keys & key), None)
        if match is None:
            created.append((role, actor))
            if apply:
                fresh = AuthorCharacter(author_id=author.id, canonical_name=role, aliases="[]",
                                        actor_name=actor, status="unconfirmed")
                db.add(fresh)
            continue
        current = str(match.actor_name or "").strip()
        if current:
            kept.append((str(match.canonical_name), current, actor))
            continue
        set_now.append((str(match.canonical_name), actor))
        if apply:
            match.actor_name = actor
            db.add(match)

    print(f"\n=== ростер автора «{author.name}» ===")
    print(f"проставлен актёр: {len(set_now)}; уже был: {len(kept)}; новых записей: {len(created)}")
    for name, actor in set_now:
        print(f"  + {name} — {actor}")
    for role, actor in created:
        print(f"  ☆ {role} — {actor} (новая запись, unconfirmed)")
    conflicts = [(n, c, a) for n, c, a in kept if normalize_name(c) != normalize_name(a)]
    if conflicts:
        print(f"\nв ростере другой актёр, оставил как было: {len(conflicts)}")
        for name, current, csv_actor in conflicts:
            print(f"  ! {name} — в ростере {current}, в файле {csv_actor}")


if __name__ == "__main__":
    raise SystemExit(main())

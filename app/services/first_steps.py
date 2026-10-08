"""«Первые шаги»: что администратору осталось сделать до первой размеченной книги.

Каждый шаг считается по настоящим данным, а не по кнопке «готово»: отметка, которую
ставит человек, врёт, как только он передумал (удалил ключ, сменил модель). Флаги в
`studio_settings` — только то, что из данных не вывести: «без лимита осознанно»,
«карточку скрыли», «результат открыли» и какая книга — пример.
"""
import threading
from pathlib import Path

from app.models import ScriptBook
from app.services import book_import, provider_keys, spend, step_models
from app.services.provider_checks import WORDS
from app.services.step_models import PROVIDER_LABELS
from app.services.studio_settings import studio_settings
from app.time_utils import utcnow_naive
from app.v2.models import V2Run

SAMPLE_PATH = Path(__file__).resolve().parents[1] / "samples" / "example_detective.txt"
SAMPLE_TITLE = "Номер двенадцатый"
SAMPLE_AUTHOR = "Noname Records"
#: шаги модели, без которых пример не разметить; консилиум, звук, эмбиент и ASR — потом
MARKUP_STEPS = ("attribution", "characters")
#: проваленная проверка — ключ есть, но работать не будет
FAILED_CHECKS = ("bad_key", "no_money")
#: два быстрых нажатия «Добавить пример» идут двумя запросами в пул потоков; без замка оба
#: увидят «примера нет» и импортируют две книги
_IMPORT_LOCK = threading.Lock()
TITLES = {
    "key": "Добавьте ключ нейросети", "models": "Проверьте модели", "limit": "Задайте месячный лимит",
    "sample": "Добавьте книгу-пример", "run": "Разметьте пример", "result": "Откройте результат",
}


def _step(key: str, done: bool, detail: str, state: str = "") -> dict:
    return {"key": key, "title": TITLES[key], "done": done, "state": state or ("done" if done else "todo"),
            "detail": detail}


def _label(provider: str) -> str:
    return PROVIDER_LABELS.get(provider, provider)


def _has_key(db, provider: str) -> bool:
    return provider_keys.key_info(db, provider)["source"] != "none"


def _key_step(db, providers: dict[str, str]) -> dict:
    provider = providers["attribution"]
    info = provider_keys.key_info(db, provider)
    if info["source"] == "none":
        return _step("key", False, f"Нет ключа {_label(provider)} — на нём идёт разметка книги.")
    # ключ из установщика лежит в .env и проверки не имеет — это не повод держать шаг открытым
    if info["check_status"] in FAILED_CHECKS:
        return _step("key", False, f"{_label(provider)}: {WORDS[info['check_status']]}")
    return _step("key", True, f"Ключ {_label(provider)} есть.")


def _models_step(db, providers: dict[str, str]) -> dict:
    missing = sorted({providers[s] for s in MARKUP_STEPS if not _has_key(db, providers[s])})
    if missing:
        return _step("models", False, "Нет ключа для " + ", ".join(_label(p) for p in missing)
                     + " — смените модель шага или добавьте ключ.")
    return _step("models", True, "Модели разметки и поиска персонажей готовы к работе.")


def _limit_step(row) -> dict:
    if row.monthly_limit_rub > 0:
        return _step("limit", True, f"Лимит — {row.monthly_limit_rub:,} ₽ в месяц.".replace(",", " "))
    if row.onboarding_no_limit:
        return _step("limit", True, "Вы работаете без лимита.")
    return _step("limit", False, "Новые прогоны не стартуют сверх лимита без подтверждения.")


def _sample(db, row) -> ScriptBook | None:
    return db.get(ScriptBook, row.sample_book_id) if row.sample_book_id else None


def _latest_run(db, book_id: str) -> V2Run | None:
    return db.query(V2Run).filter(V2Run.book_id == book_id).order_by(V2Run.updated_at.desc()).first()


def _run_step(run: V2Run | None) -> dict:
    if run is None:
        return _step("run", False, "Откройте книгу и запустите разметку — смета будет до старта.")
    if run.status in ("queued", "running"):
        return _step("run", False, "Разметка идёт.", "running")
    if run.status == "done":
        return _step("run", True, "Пример размечен.")
    # stopped — тоже «не удалось»: иначе шаг висел бы «идёт» вечно
    return _step("run", False, "Разметка не удалась — журнал на странице книги.", "failed")


def checklist(db) -> dict:
    row = studio_settings(db)
    providers = {s["step"]: s["provider"] for s in step_models.steps_view(db)}
    book = _sample(db, row)
    run = _latest_run(db, book.id) if book else None
    steps = [_key_step(db, providers), _models_step(db, providers), _limit_step(row)]
    if book is None:
        steps += [_step("sample", False, "Короткий детектив на две главы — чтобы увидеть всю цепочку."),
                  _step("run", False, "Сначала добавьте пример.", "locked"),
                  _step("result", False, "Сначала добавьте пример.", "locked")]
    else:
        seen = row.onboarding_result_seen_at is not None
        steps += [_step("sample", True, "Пример добавлен."), _run_step(run),
                  _step("result", seen, "Каст и Читалка примера." if seen
                        else "Посмотрите каст и Читалку — так выглядит готовая разметка.")]
    estimate, unknown = None, False
    # смета только пока не запускали: считать её на каждый заход на главную незачем
    if book is not None and run is None:
        estimate, unknown = spend.estimate_markup(db, book, ("cast", "attribute"))
    done = sum(1 for s in steps if s["done"])
    return {"steps": steps, "done_count": done, "total": len(steps), "hidden": bool(row.onboarding_hidden),
            "visible": not row.onboarding_hidden and done < len(steps),
            "sample_book_id": book.id if book else "", "sample_estimate_rub": estimate,
            "sample_estimate_unknown": unknown}


def import_sample(db, *, user_id: str, user_name: str) -> ScriptBook:
    with _IMPORT_LOCK:
        return _import_sample(db, user_id=user_id, user_name=user_name)


def _import_sample(db, *, user_id: str, user_name: str) -> ScriptBook:
    # чужая сессия могла записать id, пока мы ждали замок: читаем строку заново
    db.expire_all()
    row = studio_settings(db)
    existing = _sample(db, row)
    if existing is not None:
        return existing
    book = book_import.enqueue_book(db, SAMPLE_PATH.name, SAMPLE_PATH.read_bytes(), title=SAMPLE_TITLE,
                                    author=SAMPLE_AUTHOR, created_by_user_id=user_id,
                                    created_by_name=user_name)
    row = studio_settings(db)
    row.sample_book_id = book.id
    # новый пример — новый результат: прежняя отметка относилась к удалённой книге
    row.onboarding_result_seen_at = None
    db.commit()
    return book


def mark_seen(db, book_id: str) -> bool:
    row = studio_settings(db)
    if not book_id or book_id != row.sample_book_id:
        return False
    if row.onboarding_result_seen_at is None:
        row.onboarding_result_seen_at = utcnow_naive()
        db.commit()
    return True


def set_flags(db, *, hidden: bool | None = None, no_limit: bool | None = None) -> None:
    row = studio_settings(db)
    if hidden is not None:
        row.onboarding_hidden = bool(hidden)
    if no_limit is not None:
        row.onboarding_no_limit = bool(no_limit)
    db.commit()

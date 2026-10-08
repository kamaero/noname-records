"""Модель ударений (RUAccent, ~700 МБ) в настольной версии: скачивается только по кнопке.

На VPS она качается сама при первой загрузке — там это одна минута при установке. На
компьютере человека это выглядело бы как зависание первой разметки, поэтому в «одном
месте» модель — в папке данных, а признак готовности ставится только после полной загрузки:
оборванная загрузка не выдаёт себя за готовую.
"""
import shutil
import threading

from app.paths import data_path
from app.seat import one_seat

READY_MARK = ".ready"
_STATE = {"state": "", "error": ""}
_LOCK = threading.Lock()


def model_dir():
    return data_path("models", "ruaccent")


def staging_dir():
    """Сюда идёт загрузка; в model_dir переезжает только целиком. RUAccent решает «качать или
    нет» по наличию папки, а не по полноте файлов: оборванная загрузка прямо в model_dir
    оставила бы полупустую папку, и повтор по кнопке падал бы на недостающем файле."""
    return data_path("models", "ruaccent.partial")


def _downloaded_mb() -> int:
    total = 0
    for root in (model_dir(), staging_dir()):
        if root.exists():
            total += sum(p.stat().st_size for p in root.rglob("*") if p.is_file())
    return int(total / 1_000_000)


def is_ready() -> bool:
    return (model_dir() / READY_MARK).exists()


def state() -> dict:
    if not one_seat():
        return {"state": "auto", "downloaded_mb": 0, "error": ""}
    if is_ready():
        current = "ready"
    else:
        current = _STATE["state"] if _STATE["state"] in ("downloading", "failed") else "absent"
    return {"state": current, "downloaded_mb": _downloaded_mb(), "error": _STATE["error"]}


def ruaccent_package_dir():
    from importlib.util import find_spec
    from pathlib import Path

    spec = find_spec("ruaccent")
    if spec is None or not spec.origin:
        raise RuntimeError("В сборке программы нет RUAccent — напишите разработчику.")
    return Path(spec.origin).parent


def _default_loader(workdir: str) -> None:
    # RUAccent кладёт модули лемматизатора koziev не в workdir, а в папку своего пакета, и
    # импортирует их оттуда. В установленной программе пакет только для чтения, поэтому
    # koziev приходит со сборкой (проект 3); здесь только проверка — писать в пакет нельзя.
    if not (ruaccent_package_dir() / "koziev").is_dir():
        raise RuntimeError("В сборке программы нет словарей лемматизатора RUAccent (koziev) — "
                           "это ошибка сборки, а не сети. Напишите разработчику.")
    from ruaccent import RUAccent

    from app.v2.stress_ruaccent import OMOGRAPH_MODEL_SIZE
    RUAccent().load(omograph_model_size=OMOGRAPH_MODEL_SIZE, use_dictionary=True, tiny_mode=False, workdir=workdir)


def _claim() -> bool:
    with _LOCK:
        if _STATE["state"] == "downloading":
            return False
        _STATE.update(state="downloading", error="")
        return True


def _run(loader) -> None:
    try:
        staging = staging_dir()
        # остатки прошлой попытки (обрыв, закрытая программа) — не начало, а мусор
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True)
        (loader or _default_loader)(str(staging))
        (staging / READY_MARK).write_text("ok", encoding="utf-8")
        shutil.rmtree(model_dir(), ignore_errors=True)
        staging.rename(model_dir())
        _STATE.update(state="ready", error="")
    except Exception as exc:  # сеть, диск, Hugging Face — человеку нужна причина, не трассировка
        _STATE.update(state="failed", error=str(exc)[:300])


def download(*, loader=None) -> None:
    """Скачать здесь же, в вызывающем потоке."""
    if _claim():
        _run(loader)


def start_download(*, loader=None) -> threading.Thread | None:
    """Скачать в своём потоке. Не через общую линию очереди: 700 МБ на медленной сети —
    это десятки минут, и разметка книги всё это время стояла бы за загрузкой.
    None — загрузка уже идёт, вторая не нужна."""
    if not _claim():
        return None
    thread = threading.Thread(target=_run, args=(loader,), name="stress-model-download", daemon=True)
    thread.start()
    return thread

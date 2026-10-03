from __future__ import annotations

import logging
import os
import time

from app.config import settings

logger = logging.getLogger(__name__)

#: Свой файл, а не существующий `.write_test`: тот кто-то создавал руками при
#: настройке NFS и может так же руками удалить.
PROBE_FILENAME = ".nas_probe"

#: Последняя проба: жив ли NAS, когда мы это спрашивали и когда начали спрашивать
#: в последний раз. Монотонные часы, а не календарные: перевод времени не должен
#: объявлять пробу протухшей.
_state: dict[str, float | bool | None] = {"online": None, "checked_at": None, "probe_started_at": None}


def reset_state() -> None:
    _state["online"] = None
    _state["checked_at"] = None
    _state["probe_started_at"] = None


def begin_probe(*, now: float | None = None) -> None:
    """Отметить, что проба ушла в блокирующий вызов и ещё не вернулась.

    Зовётся ДО `probe_once`, потому что на `hard`-монтировании она может не вернуться
    никогда: без этой отметки повисшая проба неотличима от умершего сторожа. Отметку
    ставит поток сторожа, а читают её потоки запросов — присваивание одного значения
    в dict атомарно для CPython (GIL), так что блокировка тут не нужна и не заводится:
    лишний lock на пути каждой загрузки — плата ни за что.
    """
    _state["probe_started_at"] = time.monotonic() if now is None else float(now)


def record_probe(ok: bool, *, now: float | None = None) -> None:
    _state["online"] = bool(ok)
    _state["checked_at"] = time.monotonic() if now is None else float(now)
    # Проба вернулась — в полёте её больше нет.
    _state["probe_started_at"] = None


def nas_online(*, now: float | None = None, stale_seconds: int | None = None) -> bool | None:
    """`True` — жив, `False` — молчит, `None` — не знаем. Три состояния, а не два.

    `True` — последняя проба вернулась успехом и с тех пор прошло меньше
    `nas_probe_stale_seconds`.

    `False` — либо последняя свежая проба вернулась отказом, либо проба ушла в вызов
    и не вернулась дольше того же срока. Второе — сознательное отступление от §5.2
    спеки («протухшая проба → не знаем»): на `hard`-монтировании `open()` при пропаже
    NAS не отдаёт ошибку, а виснет, и повисшая проба — это не «непонятно», это ровно
    тот отказ, ради которого сторож и завели. Приём файлов от этого ответа не зависит —
    он всегда идёт на локальный диск, — а зеркалирование на «мёртвом» ответе просто
    откладывается до следующего круга, а не виснет само на мёртвом монтировании.

    `None` — пробы не было вовсе (процесс только поднялся) или последний результат
    протух, а новой пробы в полёте нет: поток сторожа умер. Это поломка наша, а не
    NAS, и отвечать на неё «мёртв» значило бы без причины откладывать зеркалирование
    каждого файла, пока сторож не оживёт.
    """
    limit = int(settings.nas_probe_stale_seconds if stale_seconds is None else stale_seconds)
    moment = time.monotonic() if now is None else float(now)
    started_at = _state.get("probe_started_at")
    if started_at is not None and moment - float(started_at) > limit:
        return False
    checked_at = _state.get("checked_at")
    if checked_at is None:
        return None
    if moment - float(checked_at) > limit:
        return None
    return bool(_state.get("online"))


#: Сколько ждать разовую пробу. Живой NFS отвечает за миллисекунды; пять секунд —
#: это уже «монтирование не отвечает».
REACHABLE_TIMEOUT_SECONDS = 5.0


def reachable_now(timeout: float | None = None) -> bool:
    """Разовая проба NAS, не вешая на себя вызывающего. Для процессов без сторожа.

    Флаг `nas_online()` живёт в памяти веб-процесса; воркер про NAS не знает ничего.
    Проба идёт в daemon-потоке: не вернувшийся поток умрёт вместе с процессом, а мы
    не ждём его дольше таймаута. «Да» — только подтверждённый успех в срок: молчание
    и быстрое «нет» одинаково значат «файлов сейчас не достать».
    """
    import threading

    from app.services import audio_storage

    answered: list[bool] = []

    def _probe() -> None:
        try:
            answered.append(bool(probe_once(audio_storage.nas_root())))
        except Exception:
            logger.exception("Разовая проба NAS упала")
            answered.append(False)

    thread = threading.Thread(target=_probe, name="nas-reachable-probe", daemon=True)
    thread.start()
    thread.join(timeout=float(REACHABLE_TIMEOUT_SECONDS if timeout is None else timeout))
    return bool(answered) and answered[0] is True


def probe_once(root: str) -> bool:
    """Записать метку времени в сторожевой файл на монтировании.

    Именно запись, а не `os.stat`: NFS кеширует атрибуты, и `stat` способен ответить
    из кеша, что NAS жив, когда он уже мёртв. Запись всегда идёт до сервера.
    """
    root = str(root or "").strip()
    if not root:
        # Без корня метка легла бы в рабочий каталог процесса, запись бы удалась,
        # и сторож отчитался бы, что зеркало живо. На этом ответе держится решение
        # «можно копировать» — врущий сторож хуже молчащего.
        return False
    path = os.path.join(root, PROBE_FILENAME)
    if not os.path.isdir(root):
        # Каталог НЕ создаём, хотя соблазн есть: корень зеркала живёт на стороне NFS,
        # и его отсутствие — единственный дешёвый признак того, что монтирование
        # отвалилось. Точка монтирования после `umount` остаётся пустым локальным
        # каталогом, так что создать внутри неё `rec` удастся — и мы начали бы писать
        # «на NAS» прямо на системный диск, отчитываясь, что зеркало живо. Молчаливая
        # запись мимо NAS хуже простоя.
        #
        # Но и молча возвращать «мертво» нельзя: при первой выкатке каталога ещё нет,
        # и зеркалирование встало бы без единого слова в журнале. Поэтому говорим
        # вслух — и не советуем создавать каталог, пока не ясно, какой из двух случаев
        # перед нами.
        logger.warning(
            "Корня зеркала нет на месте: %s. Либо каталог ещё не создан, либо "
            "монтирование отвалилось — зеркалирование стоит, пока это не выяснится.",
            root,
        )
        return False
    try:
        with open(path, "wb") as handle:
            handle.write(str(time.time()).encode("ascii"))
            # Без этой пары буфер Python и кеш ОС (а на NFS — ещё и кеш клиента)
            # могут отрапортовать «записано», не дойдя до сервера. Тогда успех
            # пробы не доказывает того, что докстрока обещает: NFS дошла до сервера.
            handle.flush()
            os.fsync(handle.fileno())
        return True
    except OSError:
        return False

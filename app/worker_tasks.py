"""RQ task entry points for the worker: the v2 book pipeline and DAW exports.

Each task starts, finishes or fails its BackgroundRun row so the web side can see
what the worker is doing; a failed task is re-raised so RQ records it too.
"""
import logging

from app.db import SessionLocal
from app.models import BackgroundRun
from app.time_utils import utcnow_naive
from app.workers.launcher import _finish_background_run, heartbeat_background_run

logger = logging.getLogger(__name__)


def _start_background_run(session_factory, run_id: str):
    with session_factory() as db:
        run = db.query(BackgroundRun).filter(BackgroundRun.id == run_id).first()
        if run:
            run.status = "running"
            run.started_at = utcnow_naive()
            run.heartbeat_at = utcnow_naive()
            db.commit()


def _fail_background_run(session_factory, run_id: str, exc: Exception):
    logger.error(f"Task failed (run_id={run_id}): {exc}", exc_info=True)
    _finish_background_run(
        session_factory,
        BackgroundRun,
        run_id,
        "failed",
        f"{type(exc).__name__}: {str(exc)[:220]}",
    )


def perform_asr_chapter_task(chapter_id: str, run_id: str = "", force: bool = False):
    """Распознать дубли главы. Идёт в фоне: минута на файл — не для запроса из браузера.

    `force` — кнопка «Распознать» в редакторе: слышать заново все дубли. Приём файлов
    ставит задачу без него и платит только за ещё не услышанное.
    """
    from app.services.asr_run import run_asr_for_chapter

    logger.info("Starting ASR for chapter %s (run_id=%s)", chapter_id, run_id)
    session_factory = SessionLocal
    try:
        _start_background_run(session_factory, run_id)
        with session_factory() as db:
            result = run_asr_for_chapter(db, chapter_id, force=bool(force))
            db.commit()
        _finish_background_run(session_factory, BackgroundRun, run_id, "done",
                               f"распознано {result['done']}, пересчитано {result.get('realigned', 0)}, "
                               f"не вышло {result['failed']}")
    except Exception as exc:
        _fail_background_run(session_factory, run_id, exc)
        raise


def perform_bot_intro_task(telegram_user_id: str, run_id: str = ""):
    """Новый диктор представился боту: его демо из базы голосов и назначения по книгам."""
    from app.services.bot_intro import intro_followup

    session_factory = SessionLocal
    try:
        _start_background_run(session_factory, run_id)
        with session_factory() as db:
            result = intro_followup(db, telegram_user_id)
            db.commit()
        _finish_background_run(session_factory, BackgroundRun, run_id, "done",
                               f"демо {result['demos']}, назначений {result['assignments']}")
    except Exception as exc:
        _fail_background_run(session_factory, run_id, exc)
        raise


def perform_realign_chapter_task(chapter_id: str, run_id: str = ""):
    """Пересчитать сверку главы после правки разметки. Бесплатно: из сохранённого услышанного."""
    from app.services.asr_replay import realign_chapter, send_moved_letters

    session_factory = SessionLocal
    try:
        _start_background_run(session_factory, run_id)
        with session_factory() as db:
            # Сначала коммит, потом письма: письмо до упавшего коммита ушло бы снова на
            # следующей правке — сверка в базе осталась бы прежней.
            result = realign_chapter(db, chapter_id, notify=False)
            db.commit()
            try:
                send_moved_letters(db, result["pending_letters"])
            except Exception:
                logger.exception("Не удалось отправить письма о перенесённых репликах, глава %s", chapter_id)
        _finish_background_run(session_factory, BackgroundRun, run_id, "done",
                               f"найдено у соседних ролей {result['borrowed']}, новых пропусков "
                               f"{sum(len(v) for v in result['new_missing'].values())}")
    except Exception as exc:
        _fail_background_run(session_factory, run_id, exc)
        raise


#: Сколько ждать разовую пробу NAS перед сверкой главы. Живой NFS отвечает за
#: миллисекунды; пять секунд — это уже «монтирование не отвечает».
NAS_PROBE_TIMEOUT_SECONDS = 5.0


def _nas_is_reachable() -> bool:
    """Доступно ли монтирование — не вешая на себя воркер.

    Флаг `nas_health` живёт в памяти процесса, а пробу крутит только веб: воркер про
    состояние NAS не знает ничего. На `hard`-монтировании первое же чтение файла не
    падает, а виснет, и сверка занимает единственный воркер очереди `high` до
    возвращения NAS — вместе со всей очередью распознавания за ней.

    Поэтому проба своя и в daemon-потоке: поток, который не вернулся, умрёт вместе с
    процессом, а мы не ждём его дольше таймаута.

    «Доступно» — это только подтверждённый успех пробы в пределах таймаута. И молчание
    (повисшее `hard`-монтирование), и быстрое «нет» (размонтировали, ENOENT, только
    чтение) значат одно: файлов сейчас не достать. Быстрый отказ раньше пускал сверку
    дальше, и она честно метила всю главу `missing`, а владелец получал уведомление о
    пропаже записей, которые целы и лежат на своём месте.
    """
    from app.services import nas_health

    return nas_health.reachable_now(timeout=NAS_PROBE_TIMEOUT_SECONDS)


def perform_verify_chapter_task(chapter_id: str, run_id: str = ""):
    """Перечитать дубли главы там, где они лежат, и сверить суммы. Идёт в фоне:
    глава на 350 МБ — это не для запроса из браузера, даже с локального диска.

    Приём давно переехал на локальный диск сервера — `location='nas'` остаётся
    только у наследия, записанного до переезда. NAS для сверки нужен, только если
    в главе такое наследие есть: проба зовётся ровно тогда, и её результат решает
    судьбу только этих строк (см. `audio_integrity.verify_chapter`), а не всей
    задачи — недоступность зеркала не должна отменять сверку файлов, которые
    физически лежат на сервере.
    """
    from app.services.audio_integrity import files_to_verify, verify_chapter
    from app.services.integrity_notice import notify_integrity_problem

    logger.info("Verifying chapter %s (run_id=%s)", chapter_id, run_id)
    session_factory = SessionLocal
    try:
        _start_background_run(session_factory, run_id)
        # Короткая сессия — только чтобы узнать, есть ли в главе строки на зеркале:
        # держать соединение с базой открытым на время пробы (до NAS_PROBE_TIMEOUT_SECONDS)
        # незачем, а сама проба всё равно идёт в отдельном потоке, а не через базу.
        with session_factory() as db:
            has_nas_rows = any(
                str(item.location or "local") == "nas" for item in files_to_verify(db, chapter_id)
            )
        # Пробуем NAS, только если в главе вообще есть строки, которых это касается —
        # у чисто локальной главы (новые дубли все такие) спрашивать монтирование незачем.
        nas_reachable = _nas_is_reachable() if has_nas_rows else True
        if has_nas_rows and not nas_reachable:
            logger.warning(
                "Сверка главы %s: NAS недоступен (проба не подтвердила успех за %s с) — "
                "строки на зеркале останутся skipped, остальные сверяются как обычно",
                chapter_id, NAS_PROBE_TIMEOUT_SECONDS,
            )
        with session_factory() as db:
            result = verify_chapter(db, chapter_id, nas_reachable=nas_reachable) or {}
            if result.get("mismatch") or result.get("missing"):
                notify_integrity_problem(db, chapter_id)
            db.commit()
        _finish_background_run(
            session_factory, BackgroundRun, run_id, "done",
            f"сошлось {result.get('ok', 0)}, битых {result.get('mismatch', 0)}, "
            f"пропало {result.get('missing', 0)}, пропущено {result.get('skipped', 0)}",
        )
    except Exception as exc:
        _fail_background_run(session_factory, run_id, exc)
        raise


def perform_consilium_task(book_id: str, mode: str = "recheck", run_id: str = ""):
    """Прогон консилиума по книге. Живёт в очереди `consilium` и в своей службе."""
    from app.services.consilium_engine import run_consilium

    session_factory = SessionLocal
    if not _start_consilium_run(session_factory, run_id):
        # Остановлен в очереди или уже отмечен мёртвым — прогонять нечего.
        logger.info("consilium skipped (book=%s, run_id=%s): run is no longer queued", book_id, run_id)
        return None
    try:
        run_consilium(session_factory=session_factory, book_id=book_id, run_id=run_id, mode=mode)
    except BaseException as exc:
        # run_consilium сам ставит failed с причиной; здесь — отметка на случай, если сбой
        # прошёл мимо него (не Exception, сбой самой отметки). Строку, уже закрытую
        # прогоном, не трогаем: его причина точнее.
        logger.error(f"consilium failed (book={book_id}, run_id={run_id}): {exc}", exc_info=True)
        try:
            if _run_is_open(session_factory, run_id):
                _fail_background_run(session_factory, run_id, exc)
        except Exception:  # noqa: BLE001 — сбой отметки не подменяет исходную ошибку
            logger.exception("consilium: could not mark run %s failed", run_id)
        raise


def perform_sound_task(book_id: str, mode: str = "rest", chapter_id: str = "", run_id: str = ""):
    """Звуковая разметка книги или главы. Живёт в очереди `consilium` — одна полоса долгих проходов."""
    from app.services.sound_engine import run_sound

    session_factory = SessionLocal
    if not _start_consilium_run(session_factory, run_id):
        logger.info("sound skipped (book=%s, run_id=%s): run is no longer queued", book_id, run_id)
        return None
    try:
        run_sound(session_factory=session_factory, book_id=book_id, run_id=run_id, mode=mode, chapter_id=chapter_id)
    except BaseException as exc:
        logger.error(f"sound failed (book={book_id}, run_id={run_id}): {exc}", exc_info=True)
        try:
            if _run_is_open(session_factory, run_id):
                _fail_background_run(session_factory, run_id, exc)
        except Exception:  # noqa: BLE001
            logger.exception("sound: could not mark run %s failed", run_id)
        raise


def perform_ambient_task(chapter_id: str, marker_id: str = "", prompt_override: str = "", run_id: str = ""):
    """Эмбиент главы (или одной сцены). Живёт в очереди `consilium` — одна полоса долгих проходов."""
    from app.services.ambient_engine import run_ambient

    session_factory = SessionLocal
    if not _start_consilium_run(session_factory, run_id):
        logger.info("ambient skipped (chapter=%s, run_id=%s): run is no longer queued", chapter_id, run_id)
        return None
    try:
        run_ambient(session_factory=session_factory, chapter_id=chapter_id, run_id=run_id,
                    marker_id=marker_id, prompt_override=prompt_override)
    except BaseException as exc:
        logger.error(f"ambient failed (chapter={chapter_id}, run_id={run_id}): {exc}", exc_info=True)
        try:
            if _run_is_open(session_factory, run_id):
                _fail_background_run(session_factory, run_id, exc)
        except Exception:  # noqa: BLE001
            logger.exception("ambient: could not mark run %s failed", run_id)
        raise


def _start_consilium_run(session_factory, run_id: str) -> bool:
    """Взять прогон в работу, только если он всё ещё ждёт или идёт. Условно — одной записью."""
    from sqlalchemy import update

    now = utcnow_naive()
    with session_factory() as db:
        taken = db.execute(
            update(BackgroundRun)
            .where(BackgroundRun.id == run_id, BackgroundRun.status.in_(("queued", "running")))
            .values(status="running", started_at=now, heartbeat_at=now, updated_at=now)
            .execution_options(synchronize_session=False)
        ).rowcount
        db.commit()
    return bool(taken)


def _run_is_open(session_factory, run_id: str) -> bool:
    with session_factory() as db:
        run = db.get(BackgroundRun, run_id)
        return run is not None and str(run.status) in ("queued", "running")


def perform_v2_pipeline_task(book_id: str, run_id: str = "", *, steps=None, force: bool = False,
                             v2_run_id: str | None = None, provider: str | None = None, model: str | None = None):
    """
    RQ task to run the v2 book pipeline (segment → cast → attribute → stress).

    The BackgroundRun `run_id` is started, heartbeated after every chapter and model
    batch, and finished or failed; a failed v2 run is raised so RQ and the
    background_runs table both say so.
    """
    from app.v2.pipeline import STEPS, run_book_pipeline

    logger.info(f"Starting v2 pipeline task for book {book_id} (run_id={run_id}, v2_run_id={v2_run_id})")
    session_factory = SessionLocal

    def heartbeat() -> None:
        heartbeat_background_run(
            session_factory,
            BackgroundRun,
            run_key=f"v2-pipeline:{book_id}",
            meta={"book_id": book_id, "v2_run_id": v2_run_id or ""},
        )

    try:
        if run_id:
            _start_background_run(session_factory, run_id)

        run = run_book_pipeline(
            book_id,
            steps=tuple(steps) if steps else STEPS,
            force=bool(force),
            provider=provider,
            model=model,
            run_id=v2_run_id,
            on_heartbeat=heartbeat,
        )
        if run.status == "failed":
            raise RuntimeError(run.error or "v2 pipeline failed")

        _finish_background_run(session_factory, BackgroundRun, run_id, "done", "" if run.status == "done" else run.status)

    except Exception as exc:
        _fail_background_run(session_factory, run_id, exc)
        raise

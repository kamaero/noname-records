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


def perform_asr_audit_task(chapter_id: str, run_id: str = "", *,
                           assembled_fingerprint: str = "", narrator_take_id: str = "",
                           narrator_input_fingerprint: str = "", force: bool = False,
                           language: str | None = None):
    """Проверяем снимок сборки и захватываем оплачиваемую задачу ровно один раз."""
    from app.services.asr_audit import run_asr_audit
    from app.services.asr_audit_lifecycle import assembled_audit_snapshot, claim_audit_run, record_payment_boundary

    session_factory = SessionLocal
    try:
        with session_factory() as db:
            if not claim_audit_run(db, run_id):
                return
            expected = {"assembled_fingerprint": assembled_fingerprint, "narrator_take_id": narrator_take_id,
                        "narrator_input_fingerprint": narrator_input_fingerprint}
            if language is not None:
                expected["language"] = language
            if assembled_audit_snapshot(db, chapter_id) != expected:
                db.rollback()
                record_payment_boundary(db, run_id, state="not_started", skip_reason="assembly_changed_before_payment")
                _finish_background_run(session_factory, BackgroundRun, run_id, "skipped",
                                       "Chapter inputs changed after assembly; no payment was made")
                return
            record_payment_boundary(db, run_id, state="possibly_paid")
            from app.models import ScriptChapter
            from app.services import spend
            chapter = db.get(ScriptChapter, chapter_id)
            book_id = str(chapter.book_id) if chapter else ""
            db.commit()
            with spend.context("asr_audit", book_id=book_id, chapter_id=chapter_id, run_id=run_id):
                audit = run_asr_audit(db, chapter_id, expected_narrator_fingerprint=narrator_input_fingerprint,
                                      **({"language": language} if language is not None else {}))
            db.commit()
            if audit.status == "skipped" and audit.error_message in {"narrator_input_changed", "no_narrator_take"}:
                record_payment_boundary(db, run_id, state="not_started", skip_reason=audit.error_message)
            audit_status, message = audit.status, audit.error_message or "no issues"
        _finish_background_run(session_factory, BackgroundRun, run_id, audit_status,
                               f"audit {audit_status}: {message}")
    except Exception as exc:
        _fail_background_run(session_factory, run_id, exc)
        raise


def perform_asr_chapter_task(chapter_id: str, run_id: str = "", force: bool = False,
                             language: str | None = None):
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
            result = run_asr_for_chapter(db, chapter_id, force=bool(force),
                                         **({"language": language} if language is not None else {}))
            db.commit()
        _finish_background_run(session_factory, BackgroundRun, run_id, "done",
                               f"transcribed {result['done']}, realigned {result.get('realigned', 0)}, "
                               f"failed {result['failed']}")
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
                               f"demos {result['demos']}, assignments {result['assignments']}")
    except Exception as exc:
        _fail_background_run(session_factory, run_id, exc)
        raise


def perform_audition_card_task(audio_id: str, file_id: str = "", run_id: str = ""):
    """Карточка новой пробы владельцу и автору — аудио с оценкой и «Утвердить»."""
    from app.services.audition_card import send_card
    from app.services.telegram import send_audio, send_telegram_message

    session_factory = SessionLocal
    try:
        _start_background_run(session_factory, run_id)
        with session_factory() as db:
            sent = send_card(db, audio_id, file_id=file_id, send_audio=send_audio,
                             send_text=lambda chat, text: bool(send_telegram_message(db, text, chat_ids=[chat],
                                                                                     direct=True)))
        _finish_background_run(session_factory, BackgroundRun, run_id, "done", f"cards {sent}")
    except Exception as exc:
        _fail_background_run(session_factory, run_id, exc)
        raise


def perform_bot_upload_task(upload_id: str, run_id: str = ""):
    """Проба, присланная боту: скачать, сохранить, сказать диктору, карточка владельцу и автору."""
    from app.services.bot_uploads import store
    from app.services.telegram import fetched_file, send_audio, send_telegram_message

    session_factory = SessionLocal
    try:
        _start_background_run(session_factory, run_id)
        with session_factory() as db:
            result = store(db, upload_id, fetch=fetched_file, send_audio=send_audio,
                           send_text=lambda chat, text: bool(send_telegram_message(db, text, chat_ids=[chat],
                                                                                   direct=True)))
        _finish_background_run(session_factory, BackgroundRun, run_id, "done", result)
    except Exception as exc:
        _fail_background_run(session_factory, run_id, exc)
        raise


def perform_bot_take_task(upload_id: str, run_id: str = "", language: str | None = None):
    """Дубль, присланный боту: распознать раз, разложить по главам, отчёт диктору и владельцу."""
    import os

    from app.models import BotUpload
    from app.services import asr_run, audio_storage
    from app.services.bot_takes import process, takes_workdir, snapshot_language
    from app.services.telegram import fetched_file, send_telegram_message

    session_factory = SessionLocal
    try:
        _start_background_run(session_factory, run_id)
        with session_factory() as db:
            row = db.get(BotUpload, str(upload_id or ""))
            language = snapshot_language(db, row, language=language) if row is not None else language
            phrases = asr_run.book_phrases(db, row.book_id) if row is not None else []
            # Распознавание — тот же сервис и те же подсказки, что у обычной сверки. Повтор —
            # только на «сервис занят» (`_transcribe_retrying`): таймаут считается «возможно
            # оплачено», и второй раз за него не платим.
            transcribe_part = lambda path: asr_run._transcribe_retrying(  # noqa: E731
                lambda: asr_run._transcribe_with_service(path, phrases=phrases, language=language))
            # Каталог постоянный для строки учёта: ручной повтор найдёт в нём оплаченные части.
            workdir = takes_workdir(str(upload_id))
            result = process(
                db, upload_id, fetch=fetched_file, transcribe_part=transcribe_part, workdir=workdir,
                language=language,
                send_text=lambda chat, text: bool(send_telegram_message(db, text, chat_ids=[chat], direct=True)),
                # Без `direct`: перехват `OWNER_TELEGRAM_ID` доставит сводку владельцу.
                notify_owner=lambda text: bool(send_telegram_message(db, text)),
            )
        _finish_background_run(session_factory, BackgroundRun, run_id, "done", result)
    except Exception as exc:
        _fail_background_run(session_factory, run_id, exc)
        raise


def perform_bot_demo_task(item_id: str, run_id: str = ""):
    """Демо, пересланное владельцем боту: скачать, положить в карточку диктора, сказать владельцу."""
    from app.services.bot_demos import store_item
    from app.services.telegram import fetched_file, send_telegram_message
    from app.services.demo_fetch import download

    session_factory = SessionLocal
    try:
        _start_background_run(session_factory, run_id)
        with session_factory() as db:
            result = store_item(db, item_id, fetch=fetched_file, download=download,
                                send_text=lambda chat, text: bool(send_telegram_message(db, text, chat_ids=[chat],
                                                                                        direct=True)))
        _finish_background_run(session_factory, BackgroundRun, run_id, "done", result)
    except Exception as exc:
        _fail_background_run(session_factory, run_id, exc)
        raise


def perform_session_template_task(file_id: str, chat_id: str = "", run_id: str = ""):
    """Шаблон сессии, присланный владельцем боту: скачать, проверить, положить, ответить."""
    from app.services.session_template import store_from_telegram
    from app.services.telegram import fetched_file, send_telegram_message

    session_factory = SessionLocal
    try:
        _start_background_run(session_factory, run_id)
        with session_factory() as db:
            from app.models import Character

            known = [str(name or "") for (name,) in db.query(Character.name).distinct().all()]
            result = store_from_telegram(file_id, chat_id, fetch=fetched_file, known_roles=known,
                                         send_text=lambda chat, text: bool(send_telegram_message(
                                             db, text, chat_ids=[chat], direct=True)))
        _finish_background_run(session_factory, BackgroundRun, run_id, "done", result)
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
            try:
                from app.services.released_alerts import send_owner_alert

                send_owner_alert(db, result.get("pending_owner_alert") or "")
            except Exception:
                logger.exception("Не удалось отправить тревогу владельцу, глава %s", chapter_id)
        _finish_background_run(session_factory, BackgroundRun, run_id, "done",
                               f"found in other roles {result['borrowed']}, new missing lines "
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
            f"matched {result.get('ok', 0)}, corrupt {result.get('mismatch', 0)}, "
            f"missing {result.get('missing', 0)}, skipped {result.get('skipped', 0)}",
        )
    except Exception as exc:
        _fail_background_run(session_factory, run_id, exc)
        raise


def perform_consilium_task(book_id: str, mode: str = "recheck", run_id: str = "", language: str | None = None):
    """Прогон консилиума по книге. Живёт в очереди `consilium` и в своей службе."""
    from app.services.consilium_engine import run_consilium

    session_factory = SessionLocal
    if not _start_consilium_run(session_factory, run_id):
        # Остановлен в очереди или уже отмечен мёртвым — прогонять нечего.
        logger.info("consilium skipped (book=%s, run_id=%s): run is no longer queued", book_id, run_id)
        return None
    try:
        from app.v2.job_profile import background_run_language
        language = background_run_language(session_factory, run_id, language)
        run_consilium(session_factory=session_factory, book_id=book_id, run_id=run_id, mode=mode, language=language)
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


def perform_sound_task(book_id: str, mode: str = "rest", chapter_id: str = "", run_id: str = "",
                       language: str | None = None):
    """Звуковая разметка книги или главы. Живёт в очереди `consilium` — одна полоса долгих проходов."""
    from app.services.sound_engine import run_sound

    session_factory = SessionLocal
    if not _start_consilium_run(session_factory, run_id):
        logger.info("sound skipped (book=%s, run_id=%s): run is no longer queued", book_id, run_id)
        return None
    try:
        from app.v2.job_profile import background_run_language
        language = background_run_language(session_factory, run_id, language)
        run_sound(session_factory=session_factory, book_id=book_id, run_id=run_id, mode=mode,
                  chapter_id=chapter_id, language=language)
    except BaseException as exc:
        logger.error(f"sound failed (book={book_id}, run_id={run_id}): {exc}", exc_info=True)
        try:
            if _run_is_open(session_factory, run_id):
                _fail_background_run(session_factory, run_id, exc)
        except Exception:  # noqa: BLE001
            logger.exception("sound: could not mark run %s failed", run_id)
        raise


def perform_ambient_task(chapter_id: str, marker_id: str = "", prompt_override: str = "", run_id: str = "",
                         language: str | None = None):
    """Эмбиент главы (или одной сцены). Живёт в очереди `consilium` — одна полоса долгих проходов."""
    from app.services.ambient_engine import run_ambient

    session_factory = SessionLocal
    if not _start_consilium_run(session_factory, run_id):
        logger.info("ambient skipped (chapter=%s, run_id=%s): run is no longer queued", chapter_id, run_id)
        return None
    try:
        from app.v2.job_profile import background_run_language
        language = background_run_language(session_factory, run_id, language)
        run_ambient(session_factory=session_factory, chapter_id=chapter_id, run_id=run_id,
                    marker_id=marker_id, prompt_override=prompt_override, language=language)
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
                             v2_run_id: str | None = None, provider: str | None = None, model: str | None = None,
                             language: str | None = None):
    """
    RQ task to run the v2 book pipeline (segment → cast → attribute → stress).

    The BackgroundRun `run_id` is started, heartbeated after every chapter and model
    batch, and finished or failed; a failed v2 run is raised so RQ and the
    background_runs table both say so.
    """
    from app.v2.pipeline import STEPS, RunContext, run_book_pipeline

    logger.info(f"Starting v2 pipeline task for book {book_id} (run_id={run_id}, v2_run_id={v2_run_id})")
    session_factory = SessionLocal
    context = RunContext(language=language)

    def heartbeat() -> None:
        heartbeat_background_run(
            session_factory,
            BackgroundRun,
            run_key=f"v2-pipeline:{book_id}",
            meta={"book_id": book_id, "v2_run_id": v2_run_id or "", "language": context.language},
        )

    try:
        if run_id:
            _start_background_run(session_factory, run_id)

        if run_id and not v2_run_id:
            raise RuntimeError("language_snapshot_missing: legacy pipeline job has no snapshotted run")

        run = run_book_pipeline(
            book_id,
            steps=tuple(steps) if steps else STEPS,
            force=bool(force),
            provider=provider,
            model=model,
            run_id=v2_run_id,
            on_heartbeat=heartbeat,
            language=language,
            ctx=context,
            session_factory=session_factory,
        )
        if run.status == "failed":
            raise RuntimeError(run.error or "v2 pipeline failed")

        _finish_background_run(session_factory, BackgroundRun, run_id, "done", "" if run.status == "done" else run.status)

    except Exception as exc:
        _fail_background_run(session_factory, run_id, exc)
        raise

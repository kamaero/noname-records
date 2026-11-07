"""POST /api/telegram/webhook — входящие боту. Логика — `bot_dialog`, здесь только приём.

Telegram получает 200 на всё, кроме чужого секрета: на любой другой ответ он повторяет
то же обновление, и одна ошибка превратилась бы в очередь повторов.
"""
import hmac
import logging

from fastapi import Request
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool

from app.config import settings
from app.db import SessionLocal
from app.services.bot_dialog import handle_update
from app.services.bot_broadcast import deliver as deliver_broadcast
from app.services.bot_intro import enqueue_intro_followup
from app.services.bot_reminder import deliver as deliver_reminder
from app.services.bot_demos import enqueue_store_demo
from app.services.bot_takes import enqueue_take
from app.services.bot_uploads import enqueue_store
from app.services.session_template import enqueue_store_template
from app.services.telegram import call_bot_api
from app.time_utils import utcnow_naive

logger = logging.getLogger(__name__)


def _broadcast(payload: dict) -> None:
    """Разослать подтверждённую рассылку и прислать админу итог. Шаг «sent» уже записан
    при нажатии «Отправить», поэтому повтор обновления от Telegram второй волны не даст."""
    def send(chat, text):
        return call_bot_api("sendMessage", {"chat_id": chat, "text": text, "disable_web_page_preview": True})

    try:
        with SessionLocal() as db:
            summary = deliver_broadcast(db, payload["id"], send=send)
            db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Бот: рассылка %s не прошла: %s", payload.get("id"), type(exc).__name__)
        summary = "The broadcast was interrupted. Check the server log."
    call_bot_api("sendMessage", {"chat_id": payload["admin_chat"], "text": summary})


def _remind(payload: dict) -> None:
    """«⏰ Напомнить» — та же доставка, что у рассылки: по письму каждому, итог админу."""
    def send(chat_id, text) -> bool:
        return call_bot_api("sendMessage", {"chat_id": chat_id, "text": text, "disable_web_page_preview": True})

    try:
        with SessionLocal() as db:
            summary = deliver_reminder(db, payload["id"], send=send)
            db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Бот: напоминание %s не прошло: %s", payload.get("id"), type(exc).__name__)
        summary = "The reminder was interrupted. Check the server log."
    call_bot_api("sendMessage", {"chat_id": payload["admin_chat"], "text": summary})


def _upload_not_queued(upload_id: str) -> None:
    """Очередь не приняла задачу — строка не должна висеть «queued» молча: диктору честно."""
    from app.models import BotUpload
    from app.services.bot_uploads import _site_link

    with SessionLocal() as db:
        row = db.get(BotUpload, upload_id)
        if row is None:
            return
        row.state = "failed"
        db.commit()
        call_bot_api("sendMessage", {"chat_id": row.telegram_user_id,
                                     "text": "Could not save the audition. Try again or upload it on the "
                                             f"website: {_site_link()}"})


def _demo_not_queued(item_id: str) -> None:
    """Очередь не приняла демо — позиция не должна висеть «queued», а владелец ждать ответа."""
    from app.models import BotDemoItem

    with SessionLocal() as db:
        row = db.get(BotDemoItem, item_id)
        if row is None:
            return
        row.state = "failed"
        db.commit()
    call_bot_api("sendMessage", {"chat_id": str(settings.owner_telegram_id),
                                 "text": "Could not queue the demo. Please forward it again."})


def _take_not_queued(upload_id: str) -> None:
    """Очередь не приняла дубль — строка не должна висеть «queued»: иначе повтор того же файла
    бот отклонит как «уже принят», а отчёт не придёт никогда."""
    from app.models import BotUpload

    with SessionLocal() as db:
        row = db.get(BotUpload, upload_id)
        if row is None:
            return
        row.state = "failed"
        db.commit()
        call_bot_api("sendMessage", {"chat_id": row.telegram_user_id,
                                     "text": "Could not queue the file. Please send it again."})


def process_update(update: dict) -> None:
    """База и Bot API — синхронные, поэтому отдельным потоком: повисший Telegram не должен
    держать цикл событий, а с ним и весь сайт."""
    try:
        with SessionLocal() as db:
            outgoing = handle_update(db, update, now=utcnow_naive())
            db.commit()
    except Exception as exc:  # noqa: BLE001
        # Только тип: в тексте могли оказаться пароль или чужое сообщение.
        logger.warning("Бот: обновление не обработано: %s", type(exc).__name__)
        return
    for item in outgoing:
        if item.method == "broadcast":
            _broadcast(item.payload)
            continue
        if item.method == "reminder":
            _remind(item.payload)
            continue
        if item.method == "enqueue_bot_upload":
            upload_id = item.payload["id"]
            try:
                job_id = enqueue_store(upload_id)
            except Exception as exc:  # noqa: BLE001
                job_id = None
                logger.warning("Бот: проба %s не поставлена: %s", upload_id, type(exc).__name__)
            if not job_id:
                _upload_not_queued(upload_id)
            continue
        if item.method == "enqueue_bot_take":
            upload_id = item.payload["id"]
            try:
                job_id = enqueue_take(upload_id)
            except Exception as exc:  # noqa: BLE001
                job_id = None
                logger.warning("Бот: дубль %s не поставлен: %s", upload_id, type(exc).__name__)
            if not job_id:
                _take_not_queued(upload_id)
            continue
        if item.method == "enqueue_bot_demo":
            item_id = item.payload["id"]
            try:
                job_id = enqueue_store_demo(item_id)
            except Exception as exc:  # noqa: BLE001
                job_id = None
                logger.warning("Бот: демо %s не поставлено: %s", item_id, type(exc).__name__)
            if not job_id:
                _demo_not_queued(item_id)
            continue
        if item.method == "enqueue_session_template":
            file_id, chat_id = item.payload["file_id"], item.payload["chat_id"]
            try:
                job_id = enqueue_store_template(file_id, chat_id)
            except Exception as exc:  # noqa: BLE001
                job_id = None
                logger.warning("Бот: шаблон сессии не поставлен: %s", type(exc).__name__)
            if not job_id:
                call_bot_api("sendMessage", {"chat_id": str(chat_id),
                                             "text": "Could not queue the session template. Please send it again."})
            continue
        if item.method == "enqueue_intro":
            tid = item.payload["telegram_user_id"]
            try:
                job_id = enqueue_intro_followup(tid)
            except Exception as exc:  # noqa: BLE001 — учётка уже есть, человеку уже ответили
                logger.warning("Бот: доводка знакомства %s не поставлена: %s", tid, type(exc).__name__)
                continue
            if not job_id:
                # enqueue_tracked_task не бросает: сбой очереди или дубль — это None. Повтора
                # нет — демо дотягивает ручной запуск (DEPLOYMENT.md, «Знакомство»).
                logger.warning("Бот: доводка знакомства %s не поставлена (очередь или дубль)", tid)
            continue
        call_bot_api(item.method, item.payload)


async def api_telegram_webhook(request: Request):
    secret = (settings.telegram_webhook_secret or "").strip()
    # Ответить нечем — адреса нет: иначе кнопка меняла бы пароль, а письмо с ним не уходило.
    can_answer = settings.telegram_notify_enabled and (settings.telegram_bot_token or "").strip()
    if not secret or not can_answer:
        return Response(status_code=404)
    given = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not hmac.compare_digest(given.encode(), secret.encode()):
        return Response(status_code=403)
    try:
        update = await request.json()
    except Exception:  # noqa: BLE001 — не JSON: отвечать нечего
        return JSONResponse({"ok": True})
    if isinstance(update, dict):
        await run_in_threadpool(process_update, update)
    return JSONResponse({"ok": True})

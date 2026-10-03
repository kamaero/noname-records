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
        summary = "Рассылка прервалась — посмотрите журнал сервера."
    call_bot_api("sendMessage", {"chat_id": payload["admin_chat"], "text": summary})


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

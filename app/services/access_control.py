"""Владелец управления доступами: Telegram студии, установочный логин или локальное место."""
from app.auth import session_payload
from app.config import settings
from app.models import User, UserRole
from app.seat import OWNER_ID, one_seat
from app.services.payouts import is_owner_request


def can_manage_users(db, request) -> bool:
    uid = str(session_payload(request).get("uid") or "")
    if not uid:
        return False
    if one_seat():
        return uid == OWNER_ID
    if str(settings.owner_telegram_id or "").strip():
        return is_owner_request(db, request)
    # Самостоятельная установка работает и без Telegram: владелец — её исходный логин.
    user = db.get(User, uid)
    return (user is not None and str(user.is_active or "") == "true"
            and user.login == settings.admin_login
            and db.query(UserRole).filter_by(user_id=uid, role="admin").first() is not None)

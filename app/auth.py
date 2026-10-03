import hashlib
import hmac
import time

from fastapi import Request
from itsdangerous import BadSignature, URLSafeSerializer

from app.config import settings
from app.constants import DICTOR_ROLES
from app.models import UserRole

session_serializer = URLSafeSerializer(settings.secret_key, salt="session")


def session_payload(request: Request) -> dict:
    token = request.cookies.get("session")
    if not token:
        return {}
    try:
        payload = session_serializer.loads(token)
    except BadSignature:
        return {}
    return payload if isinstance(payload, dict) else {}


def session_roles(request: Request) -> set[str]:
    payload = session_payload(request)
    roles = payload.get("roles", [])
    if not isinstance(roles, list):
        return set()
    return {str(role) for role in roles}


def session_auth_source(request: Request) -> str:
    payload = session_payload(request)
    return str(payload.get("auth_source") or "")


def session_display_name(request: Request) -> str:
    payload = session_payload(request)
    return str(payload.get("display_name") or payload.get("sub") or "").strip()


def session_telegram_user_id(request: Request) -> str:
    payload = session_payload(request)
    return str(payload.get("telegram_user_id") or "").strip()


def is_owner_telegram(request: Request) -> bool:
    return session_auth_source(request) == "telegram" and session_telegram_user_id(request) == str(settings.owner_telegram_id or "").strip()


def is_authenticated(request: Request) -> bool:
    payload = session_payload(request)
    return bool(payload.get("uid") and payload.get("sub"))


#: every role that gets the workspace; `admin` is implied everywhere else by has_any_role
WORKSPACE_ROLES = {"admin", "author", "agent"} | set(DICTOR_ROLES)


def has_any_role(request: Request, allowed_roles: set[str]) -> bool:
    roles = session_roles(request)
    return bool("admin" in roles or roles.intersection(allowed_roles))


def has_workspace_full_access(request: Request) -> bool:
    """Everyone the studio lets in works in the same workspace.

    Второго, узкого — «neo-only» — больше нет: он открывался ролью `dictor_neo`, которой
    не было ни у кого, а диктору нужны читалка и панель записи как всем остальным.
    """
    roles = session_roles(request)
    if roles & WORKSPACE_ROLES:
        return True
    return session_auth_source(request) == "telegram"


def is_agent(request: Request) -> bool:
    """Агент — и никто больше: носитель роли, у которого нет прав редактора.

    Роль Агента отнимает разделы, а не выдаёт их, поэтому вопрос к ней всегда в этой
    форме. Тому, кто носит её вместе с `author` или `admin`, отнимать нечего, и
    `has_any_role` здесь не годится: он считает `admin` за всех сразу.

    Единственное определение на всю студию. Загрузка за актёров, запрет удаления и
    щель в назначении спрашивают именно его.
    """
    roles = session_roles(request)
    return "agent" in roles and not roles & {"admin", "author"}


def verify_telegram_payload(payload: dict) -> tuple[bool, str]:
    bot_token = settings.telegram_bot_token.strip()
    if not bot_token:
        return False, "TELEGRAM_BOT_TOKEN не настроен"
    if "hash" not in payload or "auth_date" not in payload or "id" not in payload:
        return False, "Неполные данные Telegram login"

    auth_hash = str(payload.get("hash", ""))
    data_check_arr = []
    for key in sorted(k for k in payload.keys() if k != "hash"):
        value = payload.get(key)
        # Пустое поле — значит, у человека его нет (фамилии, @username, фото): Telegram такие
        # поля не присылает и в подпись не включает, а форма входа шлёт их пустыми. Без этого
        # у всех без фамилии или ника подпись не сходилась никогда.
        if value is None or str(value) == "":
            continue
        data_check_arr.append(f"{key}={value}")
    data_check_string = "\n".join(data_check_arr)

    secret_key = hashlib.sha256(bot_token.encode("utf-8")).digest()
    calc_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calc_hash, auth_hash):
        return False, "Неверная подпись Telegram login"

    try:
        auth_date = int(str(payload.get("auth_date", "0")))
    except ValueError:
        return False, "Некорректный auth_date"
    if abs(int(time.time()) - auth_date) > settings.telegram_auth_max_age_seconds:
        return False, "Telegram login устарел"

    return True, ""


def get_user_roles(db, user_id: str) -> list[str]:
    return [row.role for row in db.query(UserRole).filter(UserRole.user_id == user_id).all()]


def needs_password_setup() -> bool:
    return not settings.admin_password_hash.strip()


def can_react_to_auditions(request: Request) -> bool:
    """👍/👎 на пробу. Автор — с 26.09; владелец студии (admin) — с 03.10, «как у автора»:
    реакция одна на пробу, действует последний голос, 👎 через 15 минут шлёт вежливый отказ.
    Агент и диктор — нет: за 👎 следует письмо человеку."""
    roles = session_roles(request)
    return "author" in roles or "admin" in roles or is_owner_telegram(request)

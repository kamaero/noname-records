from typing import Any, Callable
import logging
from urllib.parse import quote_plus

from fastapi import FastAPI, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse

from app.auth import session_serializer
from app.config import settings
from app.db import SessionLocal
from app.models import TelegramAuthAccount, User, UserRole
from app.rate_limit import limiter
from app.services.login_identity import account_can_sign_in, resolve_telegram_user

logger = logging.getLogger(__name__)


#: Причины отказа, которые страница входа умеет назвать. В адресе едет код, а не фраза:
#: фразу из адреса любой мог бы подменить своей («сервис закрыт, пишите туда-то») на
#: настоящей странице входа. Текст к коду держит фронт (`LoginPage.tsx`, LOGIN_ERRORS),
#: подробная причина — только в журнале.
LOGIN_ERROR_CODES = {"setup", "credentials", "disabled", "no_roles", "telegram", "not_whitelisted"}


def _login_error_redirect(code: str, *, setup_missing: bool = False) -> RedirectResponse:
    assert code in LOGIN_ERROR_CODES, code
    params = [f"error={quote_plus(code)}"]
    if setup_missing:
        params.append("setup_missing=1")
    return RedirectResponse(url=f"/app/login?{'&'.join(params)}", status_code=status.HTTP_302_FOUND)


def register_auth_routes(
    app: FastAPI,
    *,
    verify_password_cb: Callable[[str, str], bool],
    audit_cb: Callable[..., None],
    get_user_roles_cb: Callable[..., list[str]],
    needs_password_setup_cb: Callable[[], bool],
    verify_telegram_payload_cb: Callable[[dict[str, Any]], tuple[bool, str]],
) -> None:
    @app.post("/login", response_class=HTMLResponse)
    @limiter.limit(settings.auth_rate_limit)
    def login(request: Request, login: str = Form(...), password: str = Form(...)):
        if needs_password_setup_cb():
            return _login_error_redirect("setup", setup_missing=True)

        session_uid = ""
        session_sub = ""
        session_display_name = ""
        session_roles: list[str] = []

        with SessionLocal() as db:
            user = db.query(User).filter(User.login == login).first()
            if not user and login == settings.admin_login and settings.admin_password_hash:
                user = User(
                    login=settings.admin_login,
                    password_hash=settings.admin_password_hash,
                    display_name="Administrator",
                    is_active="true",
                )
                db.add(user)
                db.flush()
                db.add(UserRole(user_id=user.id, role="admin"))
                db.commit()
                db.refresh(user)

            valid_password = verify_password_cb(password, user.password_hash) if user else False
            if not (user and valid_password):
                return _login_error_redirect("credentials")
            # «Выключить» on the accounts screen has to mean something: the password
            # alone used to be enough, so a disabled account kept signing in
            if not account_can_sign_in(user):
                logger.warning("Login rejected: account disabled login=%s", user.login)
                return _login_error_redirect("disabled")

            roles = get_user_roles_cb(db, user.id)
            if not roles:
                if user.login == settings.admin_login:
                    roles = ["admin"]
                    db.add(UserRole(user_id=user.id, role="admin"))
                    db.commit()
                else:
                    return _login_error_redirect("no_roles")

            session_uid = user.id
            session_sub = user.login
            session_display_name = (user.display_name or user.login or "").strip()
            session_roles = list(roles)
            audit_cb(
                db,
                None,
                entity_type="auth_session",
                entity_id=user.id,
                action="login_password",
                payload={"login": user.login, "display_name": session_display_name, "roles": session_roles},
                user_id=user.id,
            )
            db.commit()

        response = RedirectResponse(url="/app/books", status_code=status.HTTP_302_FOUND)
        response.set_cookie(
            "session",
            session_serializer.dumps(
                {"uid": session_uid, "sub": session_sub, "roles": session_roles, "auth_source": "password"}
                | {"display_name": session_display_name}
            ),
            httponly=True,
            secure=settings.cookie_secure,
            samesite="lax",
            max_age=60 * 60 * 12,
        )
        return response

    @app.post("/auth/telegram")
    @limiter.limit(settings.auth_rate_limit)
    def login_telegram(
        request: Request,
        id: str = Form(...),
        auth_date: str = Form(...),
        hash: str = Form(...),
        first_name: str = Form(""),
        last_name: str = Form(""),
        username: str = Form(""),
        photo_url: str = Form(""),
    ):
        payload = {
            "id": id,
            "auth_date": auth_date,
            "hash": hash,
            "first_name": first_name,
            "last_name": last_name,
            "username": username,
            "photo_url": photo_url,
        }
        ok, error = verify_telegram_payload_cb(payload)
        if not ok:
            logger.warning(
                "Telegram login rejected: reason=%s id=%s username=%s first_name=%s ip=%s ua=%s",
                error,
                str(id),
                username or "",
                first_name or "",
                request.client.host if request.client else "",
                request.headers.get("user-agent", "")[:200],
            )
            return _login_error_redirect("telegram", setup_missing=needs_password_setup_cb())

        session_uid = ""
        session_sub = ""
        session_display_name = ""
        session_roles: list[str] = []

        with SessionLocal() as db:
            account = db.query(TelegramAuthAccount).filter(
                TelegramAuthAccount.telegram_user_id == str(id),
                TelegramAuthAccount.is_active == "true",
            ).first()
            if not account:
                logger.warning(
                    "Telegram login rejected: whitelist miss id=%s username=%s first_name=%s ip=%s",
                    str(id),
                    username or "",
                    first_name or "",
                    request.client.host if request.client else "",
                )
                return _login_error_redirect("not_whitelisted", setup_missing=needs_password_setup_cb())
            access_scope = (account.access_scope or "").strip() or "full"
            # The whitelist says whether this identity may come in; the account it is
            # linked to says who the person is. Resolution repairs the link on the way
            # through, so accounts made before the link keep working untouched.
            user = resolve_telegram_user(
                db,
                telegram_user_id=str(id),
                account=account,
                display_name=(f"{first_name} {last_name}".strip() or username or ""),
            )
            if not account_can_sign_in(user):
                logger.warning("Telegram login rejected: account disabled id=%s login=%s", str(id), user.login)
                db.commit()
                return _login_error_redirect("disabled")
            roles = set(get_user_roles_cb(db, user.id))
            db.commit()
            session_uid = user.id
            session_sub = user.login
            session_display_name = (user.display_name or username or f"{first_name} {last_name}".strip() or user.login).strip()
            session_roles = sorted(roles)
            audit_cb(
                db,
                None,
                entity_type="auth_session",
                entity_id=user.id,
                action="login_telegram",
                payload={
                    "telegram_user_id": str(id),
                    "display_name": session_display_name,
                    "roles": session_roles,
                    "access_scope": access_scope,
                },
                user_id=user.id,
            )
            db.commit()

        response = RedirectResponse(url="/app/books", status_code=status.HTTP_302_FOUND)
        response.set_cookie(
            "session",
            session_serializer.dumps(
                {"uid": session_uid, "sub": session_sub, "roles": session_roles, "auth_source": "telegram", "access_scope": access_scope, "telegram_user_id": str(id)}
                | {"display_name": session_display_name}
            ),
            httponly=True,
            secure=settings.cookie_secure,
            samesite="lax",
            max_age=60 * 60 * 12,
        )
        return response

    @app.post("/logout")
    def logout():
        response = RedirectResponse(url="/app/login", status_code=status.HTTP_302_FOUND)
        response.delete_cookie("session")
        return response

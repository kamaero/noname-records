from __future__ import annotations

from typing import Any, Callable

from fastapi import Request, status
from fastapi.responses import JSONResponse, PlainTextResponse

# A leaf service: models and the password context, no settings, no app imports. Taking
# it directly keeps the account operations out of the handler-factory dependency chain,
# where every new name has to be declared at four levels before it arrives here.
from app.seat import one_seat
from app.services import onboarding, user_admin
from app.time_utils import utcnow_naive


def build_frontend_core_handlers(deps: dict[str, Any]) -> dict[str, Callable[..., Any]]:
    """Session (`/api/me`), public auth config, users + Telegram whitelist, and the activity log."""
    is_authenticated = deps["is_authenticated"]
    is_owner_telegram = deps["is_owner_telegram"]
    session_payload = deps["session_payload"]
    session_roles = deps["session_roles"]
    session_auth_source = deps["session_auth_source"]
    session_telegram_user_id = deps["session_telegram_user_id"]
    owner_api_allowed = deps["owner_api_allowed"]
    has_workspace_full_access = deps["has_workspace_full_access"]
    workspace_tabs_for_request = deps["workspace_tabs_for_request"]
    session_local = deps["SessionLocal"]
    build_owner_dashboard_data = deps["build_owner_dashboard_data"]
    user_model = deps["User"]
    whitelist_model = deps["TelegramAuthAccount"]
    get_user_roles = deps["get_user_roles"]
    format_dt = deps["format_dt"]
    audit = deps["audit"]
    settings = deps["settings"]
    workspace_tabs = deps["WORKSPACE_TABS"]
    needs_password_setup = deps["needs_password_setup"]

    def unauthorized():
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=status.HTTP_401_UNAUTHORIZED)

    def forbidden():
        return JSONResponse({"ok": False, "error": "forbidden"}, status_code=status.HTTP_403_FORBIDDEN)

    def require_authenticated(request: Request):
        if not is_authenticated(request):
            return unauthorized()
        return None

    def require_owner_api(request: Request):
        auth_error = require_authenticated(request)
        if auth_error:
            return auth_error
        if not owner_api_allowed(request):
            return forbidden()
        return None

    def _bot_reachable_for(request: Request, *, fresh: bool = False):
        """`None` — вход по паролю (нечего проверять) или Telegram не ответил."""
        if session_auth_source(request) != "telegram":
            return None
        telegram_user_id = session_telegram_user_id(request)
        if not telegram_user_id:
            return None
        return onboarding.bot_reachable(telegram_user_id, fresh=fresh)

    def api_me(request: Request):
        payload = session_payload(request)
        if not is_authenticated(request):
            return JSONResponse(
                {
                    "authenticated": False,
                    "uid": "",
                    "login": "",
                    "display_name": "",
                    "roles": [],
                    "auth_source": "",
                    "workspace_tabs": [],
                    "full_access": False,
                    "bot_reachable": None,
                    "show_onboarding": False,
                },
                status_code=status.HTTP_401_UNAUTHORIZED,
            )
        uid = str(payload.get("uid") or "")
        roles = session_roles(request)
        auth_source = session_auth_source(request)
        is_dictor = "dictor" in roles
        with session_local() as db:
            user = db.query(user_model).filter(user_model.id == uid).first()
        shown_at = user.onboarding_shown_at if user is not None else None
        # Достижимость бота нужна только дикторам — окно есть только у них. Не-диктору
        # (в том числе владельцу, который чаще всех входит через Telegram) это стоило бы
        # похода в Telegram на каждый /api/me мимо всякого кэша.
        reachable = _bot_reachable_for(request) if is_dictor else None
        show_onboarding = onboarding.should_show(
            is_dictor=is_dictor,
            auth_source=auth_source,
            shown_at=shown_at,
            bot_reachable=reachable,
            now=utcnow_naive(),
        )
        return {
            "authenticated": True,
            "uid": uid,
            "login": str(payload.get("sub") or ""),
            "display_name": str(payload.get("display_name") or payload.get("sub") or ""),
            "roles": sorted(roles),
            "auth_source": auth_source,
            "is_owner_telegram": is_owner_telegram(request),
            "workspace_tabs": [{"id": tab_id, "label": workspace_tabs[tab_id]["label"]} for tab_id in workspace_tabs_for_request(request)],
            "full_access": has_workspace_full_access(request),
            "bot_reachable": reachable,
            "show_onboarding": show_onboarding,
            # studio — VPS; one — настольная программа: фронт прячет учётки и пробы
            "seat_mode": "one" if one_seat() else "studio",
        }

    def api_me_bot_reach(request: Request):
        """Кнопка «Проверить ещё раз» в окне онбординга: свежий ответ мимо кэша."""
        auth_error = require_authenticated(request)
        if auth_error:
            return auth_error
        return {"ok": True, "bot_reachable": _bot_reachable_for(request, fresh=True)}

    def api_onboarding_shown(request: Request):
        """Автопоказ фиксируется сюда — ручное открытие из шапки этого не делает."""
        auth_error = require_authenticated(request)
        if auth_error:
            return auth_error
        uid = str(session_payload(request).get("uid") or "")
        with session_local() as db:
            user = db.query(user_model).filter(user_model.id == uid).first()
            if user is None:
                return unauthorized()
            user.onboarding_shown_at = utcnow_naive()
            db.commit()
        return {"ok": True}

    def api_public_auth_config(_request: Request):
        return {
            "ok": True,
            "app_name": settings.app_name,
            "studio_name": settings.studio_name,
            "telegram_bot_username": settings.telegram_bot_username or "",
            "setup_missing": needs_password_setup(),
            # странице входа: в «одном месте» формы нет — сессию даёт только запуск программы
            "seat_mode": "one" if one_seat() else "studio",
        }

    async def api_attach_telegram(request: Request, user_id: str):
        """Привязать телеграм к учётке — там, где владелец на неё и смотрит."""
        access_error = require_owner_api(request)
        if access_error:
            return access_error
        payload = await request.json()
        with session_local() as db:
            try:
                result = user_admin.attach_telegram(
                    db, user_id=user_id,
                    telegram_user_id=str((payload or {}).get("telegram_user_id") or ""),
                )
            except user_admin.UserAdminError as exc:
                return JSONResponse({"ok": False, "error": str(exc)}, status_code=status.HTTP_400_BAD_REQUEST)
            db.commit()
        return JSONResponse({"ok": True, **result})

    def api_bot_reach(request: Request):
        """Кому бот может написать. Вход через виджет и письма бота — разные каналы."""
        access_error = require_owner_api(request)
        if access_error:
            return access_error
        from app.services.bot_reach import check_bot_reach

        with session_local() as db:
            rows = user_admin.whitelist_rows(db)
        return JSONResponse({"ok": True, **check_bot_reach(rows, token=settings.telegram_bot_token or "")})

    def api_users_export(request: Request):
        """Реестр доступов текстом. Паролей в нём нет — их нет и в системе."""
        access_error = require_owner_api(request)
        if access_error:
            return access_error
        with session_local() as db:
            text = user_admin.export_roster(db)
        return PlainTextResponse(
            text,
            headers={"Content-Disposition": "attachment; filename*=UTF-8''%D0%B4%D0%BE%D1%81%D1%82%D1%83%D0%BF%D1%8B.txt"},
        )

    async def api_users_issue_passwords(request: Request):
        """Выдать пароли тем, у кого их нет, и вернуть реестр вместе с ними.

        Тем, у кого пароль есть, новый не выдаётся: люди им пользуются, и «выдать всем
        новые» означало бы запереть их снаружи.
        """
        access_error = require_owner_api(request)
        if access_error:
            return access_error
        with session_local() as db:
            targets = [user.id for user in user_admin.accounts_without_password(db)]
            issued = user_admin.issue_passwords(db, targets)
            text = user_admin.export_roster(db, issued=issued)
            db.commit()
        return JSONResponse({"ok": True, "issued": len(issued), "roster": text})

    def api_users(request: Request):
        access_error = require_owner_api(request)
        if access_error:
            return access_error
        with session_local() as db:
            owner_dashboard = build_owner_dashboard_data(db, None)
            by_id = {user.id: user for user in db.query(user_model).all()}
            rows = user_admin.list_users(db)
            for row in rows:
                user = by_id.get(row["id"])
                row["created_at"] = format_dt(user.created_at) if user else None
                row["updated_at"] = format_dt(user.updated_at) if user else None
            return {
                "users": rows,
                # Каждая запись говорит, к чьей учётке она привязана: без этого она
                # выглядит ничьей, и понять, дошла ли привязка, нельзя.
                "owner_whitelist_accounts": user_admin.whitelist_rows(db),
            }

    def _admin_payload(db, user_id: str) -> dict:
        return next((row for row in user_admin.list_users(db) if row["id"] == user_id), {})

    def _refusal(code: str):
        return JSONResponse({"ok": False, "error": code}, status_code=status.HTTP_400_BAD_REQUEST)

    async def api_create_user(request: Request):
        """A new account. The generated password comes back once and is never readable again."""
        access_error = require_owner_api(request)
        if access_error:
            return access_error
        body = await request.json()
        with session_local() as db:
            try:
                made = user_admin.create_user(
                    db,
                    display_name=str((body or {}).get("display_name") or ""),
                    roles=[str(role) for role in ((body or {}).get("roles") or [])],
                )
            except user_admin.UserAdminError as exc:
                return _refusal(exc.code)
            audit(db, request, entity_type="user", entity_id=made["user"]["id"], action="create_user",
                  payload={"login": made["user"]["login"], "roles": made["user"]["roles"]})
            db.commit()
            return JSONResponse({"ok": True, "user": made["user"], "password": made["password"]})

    async def api_update_user(request: Request, user_id: str):
        access_error = require_owner_api(request)
        if access_error:
            return access_error
        body = await request.json() or {}
        with session_local() as db:
            before = _admin_payload(db, user_id)
            try:
                row = user_admin.update_user(
                    db,
                    user_id,
                    display_name=None if body.get("display_name") is None else str(body.get("display_name")),
                    is_active=None if body.get("is_active") is None else bool(body.get("is_active")),
                    roles=None if body.get("roles") is None else [str(role) for role in body.get("roles")],
                )
            except user_admin.UserAdminError as exc:
                return _refusal(exc.code)
            audit(db, request, entity_type="user", entity_id=user_id, action="update_user",
                  payload={"before": before, "after": row})
            db.commit()
            return JSONResponse({"ok": True, "user": row})

    async def api_reset_user_password(request: Request, user_id: str):
        access_error = require_owner_api(request)
        if access_error:
            return access_error
        with session_local() as db:
            try:
                password = user_admin.reset_password(db, user_id)
            except user_admin.UserAdminError as exc:
                return _refusal(exc.code)
            # the password itself never enters the log
            audit(db, request, entity_type="user", entity_id=user_id, action="reset_password", payload={})
            db.commit()
            return JSONResponse({"ok": True, "password": password})

    async def api_delete_user(request: Request, user_id: str):
        access_error = require_owner_api(request)
        if access_error:
            return access_error
        with session_local() as db:
            before = _admin_payload(db, user_id)
            try:
                user_admin.delete_user(db, user_id, actor_user_id=str(session_payload(request).get("uid") or ""))
            except user_admin.UserAdminError as exc:
                return _refusal(exc.code)
            audit(db, request, entity_type="user", entity_id=user_id, action="delete_user", payload={"before": before})
            db.commit()
            return JSONResponse({"ok": True})

    async def api_merge_users(request: Request):
        """Two accounts of one person become one. The keeper is named by the caller."""
        access_error = require_owner_api(request)
        if access_error:
            return access_error
        body = await request.json() or {}
        with session_local() as db:
            try:
                result = user_admin.merge_accounts(
                    db,
                    keep_id=str(body.get("keep_id") or ""),
                    drop_id=str(body.get("drop_id") or ""),
                    actor_user_id=str(session_payload(request).get("uid") or ""),
                )
            except user_admin.UserAdminError as exc:
                return _refusal(exc.code)
            audit(db, request, entity_type="user", entity_id=result["user"]["id"], action="merge_users",
                  payload={"kept": result["user"]["login"], "dropped": result["dropped_login"], "moved": result["moved"]})
            db.commit()
            return JSONResponse({"ok": True, **result})

    async def api_bulk_user_roles(request: Request):
        """One role for many accounts — the 49 dictors who were all given `author`."""
        access_error = require_owner_api(request)
        if access_error:
            return access_error
        body = await request.json() or {}
        ids = [str(item) for item in (body.get("ids") or []) if str(item or "").strip()]
        roles = [str(role) for role in (body.get("roles") or [])]
        if not ids or not roles:
            return _refusal("bad_payload")
        changed = 0
        with session_local() as db:
            for user_id in ids:
                try:
                    user_admin.update_user(db, user_id, roles=roles)
                except user_admin.UserAdminError:
                    # one refusal (the last admin) must not undo the rest
                    continue
                changed += 1
            audit(db, request, entity_type="user", entity_id="", action="bulk_user_roles",
                  payload={"ids": ids, "roles": roles, "changed": changed})
            db.commit()
            return JSONResponse({"ok": True, "changed": changed})

    async def api_save_telegram_whitelist(request: Request):
        auth_error = require_authenticated(request)
        if auth_error:
            return auth_error
        if not is_owner_telegram(request):
            return forbidden()
        payload = await request.json()
        entry_id = str((payload or {}).get("entry_id") or "").strip()
        telegram_user_id = str((payload or {}).get("telegram_user_id") or "").strip()
        display_name = str((payload or {}).get("display_name") or "").strip()
        role = str((payload or {}).get("role") or "").strip()
        access_scope = str((payload or {}).get("access_scope") or "full").strip() or "full"
        is_active = "true" if str((payload or {}).get("is_active") or "true").strip().lower() in {"1", "true", "yes", "on"} else "false"
        if not telegram_user_id or not display_name or not role:
            return JSONResponse({"ok": False, "error": "bad_payload"}, status_code=status.HTTP_400_BAD_REQUEST)
        with session_local() as db:
            row = None
            if entry_id:
                row = db.query(whitelist_model).filter(whitelist_model.id == entry_id).first()
            if not row:
                row = db.query(whitelist_model).filter(whitelist_model.telegram_user_id == telegram_user_id).first()
            before = {
                "display_name": row.display_name if row else "",
                "role": row.role if row else "",
                "access_scope": row.access_scope if row else "",
                "is_active": row.is_active if row else "",
            }
            if not row:
                row = whitelist_model(
                    telegram_user_id=telegram_user_id,
                    display_name=display_name,
                    role=role,
                    access_scope=access_scope,
                    is_active=is_active,
                )
                db.add(row)
                action = "create_whitelist"
                db.flush()
            else:
                row.telegram_user_id = telegram_user_id
                row.display_name = display_name
                row.role = role
                row.access_scope = access_scope
                row.is_active = is_active
                action = "update_whitelist"
            audit(
                db,
                request,
                entity_type="telegram_whitelist",
                entity_id=row.telegram_user_id,
                action=action,
                payload={
                    "before": before,
                    "after": {
                        "display_name": row.display_name,
                        "role": row.role,
                        "access_scope": row.access_scope,
                        "is_active": row.is_active,
                    },
                },
            )
            # Учётка заводится вместе с записью: человека вносят один раз, а не дважды.
            # Если такой человек уже есть — привязываемся к нему, а не плодим второго.
            created = None
            if not str(row.user_id or "").strip():
                created = user_admin.attach_account_to_whitelist(db, row)
            db.commit()
            return {
                "ok": True,
                **({"created_account": created} if created else {}),
                "entry": {
                    "id": row.id,
                    "telegram_user_id": row.telegram_user_id,
                    "display_name": row.display_name,
                    "role": row.role,
                    "access_scope": row.access_scope,
                    "is_active": row.is_active == "true",
                    "created_at": format_dt(row.created_at),
                },
            }

    def api_log(request: Request):
        access_error = require_owner_api(request)
        if access_error:
            return access_error
        with session_local() as db:
            owner_dashboard = build_owner_dashboard_data(db, None)
            return {
                "usage_by_actor": owner_dashboard.get("usage_by_actor") or [],
                "usage_by_stage": owner_dashboard.get("usage_by_stage") or [],
                "recent_activity": [
                    {
                        **item,
                        "created_at": format_dt(item.get("created_at")),
                    }
                    for item in (owner_dashboard.get("recent_activity") or [])
                ],
            }

    return {
        "api_me": api_me,
        "api_me_bot_reach": api_me_bot_reach,
        "api_onboarding_shown": api_onboarding_shown,
        "api_public_auth_config": api_public_auth_config,
        "api_users": api_users,
        "api_attach_telegram": api_attach_telegram,
        "api_bot_reach": api_bot_reach,
        "api_users_export": api_users_export,
        "api_users_issue_passwords": api_users_issue_passwords,
        "api_create_user": api_create_user,
        "api_update_user": api_update_user,
        "api_reset_user_password": api_reset_user_password,
        "api_delete_user": api_delete_user,
        "api_bulk_user_roles": api_bulk_user_roles,
        "api_merge_users": api_merge_users,
        "api_save_telegram_whitelist": api_save_telegram_whitelist,
        "api_log": api_log,
    }

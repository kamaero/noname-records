"""
Shared response helpers for API handlers.
All functions return JSONResponse with a consistent {"ok": bool, ...} envelope.
"""
from __future__ import annotations

from typing import Any

from fastapi import status
from fastapi.responses import JSONResponse


def ok_response(data: dict[str, Any] | None = None) -> JSONResponse:
    payload: dict[str, Any] = {"ok": True}
    if data:
        payload.update(data)
    return JSONResponse(payload)


def error_response(
    error: str,
    *,
    status_code: int = status.HTTP_400_BAD_REQUEST,
    detail: str | None = None,
    extra: dict[str, Any] | None = None,
) -> JSONResponse:
    payload: dict[str, Any] = {"ok": False, "error": error}
    if detail:
        payload["detail"] = detail
    if extra:
        payload.update(extra)
    return JSONResponse(payload, status_code=status_code)


def not_found_response(error: str = "not_found") -> JSONResponse:
    return error_response(error, status_code=status.HTTP_404_NOT_FOUND)


def forbidden_response(error: str = "forbidden") -> JSONResponse:
    return error_response(error, status_code=status.HTTP_403_FORBIDDEN)


def unauthorized_response(error: str = "unauthorized") -> JSONResponse:
    return error_response(error, status_code=status.HTTP_401_UNAUTHORIZED)


def bad_request_response(error: str = "bad_payload", detail: str | None = None) -> JSONResponse:
    return error_response(error, status_code=status.HTTP_400_BAD_REQUEST, detail=detail)

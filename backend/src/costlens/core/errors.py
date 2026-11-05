"""Errors as RFC 9457 problem details (``application/problem+json``)."""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from costlens.core.logging import request_id_var

PROBLEM_CONTENT_TYPE = "application/problem+json"


class AppError(Exception):
    """An expected error with a stable machine-readable ``code``."""

    status: int = 400
    code: str = "bad_request"

    def __init__(self, detail: str, *, status: int | None = None, code: str | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        if status is not None:
            self.status = status
        if code is not None:
            self.code = code


class NotFoundError(AppError):
    status = 404
    code = "not_found"


class PermissionDeniedError(AppError):
    status = 403
    code = "forbidden"


class ConflictError(AppError):
    status = 409
    code = "conflict"


def _title(status: int) -> str:
    try:
        return HTTPStatus(status).phrase
    except ValueError:
        return "Error"


def problem(
    status: int,
    *,
    detail: str | None = None,
    code: str | None = None,
    instance: str | None = None,
    headers: dict[str, str] | None = None,
    **extra: Any,
) -> JSONResponse:
    body: dict[str, Any] = {
        "type": "about:blank",
        "title": _title(status),
        "status": status,
        "code": code or _title(status).lower().replace(" ", "_").replace("-", "_"),
    }
    if detail:
        body["detail"] = detail
    if instance:
        body["instance"] = instance
    request_id = request_id_var.get()
    if request_id:
        body["request_id"] = request_id
    body.update(extra)
    return JSONResponse(body, status_code=status, media_type=PROBLEM_CONTENT_TYPE, headers=headers)


def internal_error_response() -> JSONResponse:
    return problem(
        500,
        detail="Something went wrong on our side. Quote the request id if you contact support.",
        code="internal_error",
    )


async def _http_exception(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, StarletteHTTPException):  # pragma: no cover - registered per type
        raise exc
    detail = (
        exc.detail
        if isinstance(exc.detail, str) and exc.detail != _title(exc.status_code)
        else None
    )
    return problem(
        exc.status_code,
        detail=detail,
        instance=request.url.path,
        headers=getattr(exc, "headers", None),
    )


async def _validation(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, RequestValidationError):  # pragma: no cover - registered per type
        raise exc
    errors = [
        {"loc": list(e.get("loc", ())), "msg": e.get("msg", ""), "type": e.get("type", "")}
        for e in exc.errors()
    ]
    return problem(
        422,
        detail="The request is not valid.",
        code="validation_error",
        instance=request.url.path,
        errors=errors,
    )


async def _app_error(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, AppError):  # pragma: no cover - registered per type
        raise exc
    return problem(exc.status, detail=exc.detail, code=exc.code, instance=request.url.path)


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(StarletteHTTPException, _http_exception)
    app.add_exception_handler(RequestValidationError, _validation)
    app.add_exception_handler(AppError, _app_error)

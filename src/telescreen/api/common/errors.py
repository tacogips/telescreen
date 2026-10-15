"""Safe HTTP error mapping for the Reporting Bureau API."""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from telescreen.core.errors import TelescreenError
from telescreen.core.ids import new_id
from telescreen.core.vocabulary import ErrorCode

logger: logging.Logger = logging.getLogger(__name__)


def http_status_for(code: ErrorCode) -> int:
    """Map stable application errors to HTTP status codes."""
    if code in (
        ErrorCode.VALIDATION_ERROR,
        ErrorCode.RAW_VALUE_DETECTED,
        ErrorCode.EVIDENCE_REJECTED,
        ErrorCode.UNKNOWN_TEMPLATE,
        ErrorCode.UNKNOWN_VARIABLE,
        ErrorCode.TEMPLATE_EVENT_MISMATCH,
        ErrorCode.REQUIRED_FIELD_MISSING,
        ErrorCode.INVALID_FIELD_VALUE,
    ):
        return 422
    if code in (
        ErrorCode.UNAUTHORIZED,
        ErrorCode.TOKEN_INVALID,
        ErrorCode.SIGNATURE_INVALID,
        ErrorCode.INVALID_TOKEN,
    ):
        return 401
    if code in (ErrorCode.FORBIDDEN, ErrorCode.NO_ROLE, ErrorCode.NOT_PROVISIONED):
        return 403
    if code in (ErrorCode.NOT_FOUND, ErrorCode.UNKNOWN_SOURCE):
        return 404
    if code in (
        ErrorCode.CONFLICT,
        ErrorCode.INVALID_TRANSITION,
        ErrorCode.EVIDENCE_REQUIRED,
        ErrorCode.IDENTITY_CONFLICT,
        ErrorCode.LAST_ADMIN,
        ErrorCode.FIELD_IN_USE,
    ):
        return 409
    if code is ErrorCode.PAYLOAD_TOO_LARGE:
        return 413
    if code is ErrorCode.UNSUPPORTED_MEDIA_TYPE:
        return 415
    if code is ErrorCode.RATE_LIMITED:
        return 429
    if code in (
        ErrorCode.EVIDENCE_SCANNER_UNAVAILABLE,
        ErrorCode.IDP_UNAVAILABLE,
        ErrorCode.SERVICE_UNAVAILABLE,
    ):
        return 503
    return 500


def install_error_handlers(app: FastAPI) -> None:
    """Register handlers that never return request values or exception messages."""

    @app.exception_handler(TelescreenError)
    async def telescreen_error_handler(request: Request, error: TelescreenError) -> JSONResponse:
        """Render a stable application error with its static detail."""
        del request
        return JSONResponse(
            status_code=http_status_for(error.code),
            content={"error": {"code": error.code.value, "detail": error.detail}},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, error: RequestValidationError
    ) -> JSONResponse:
        """Render only safe validation locations and error types."""
        del request
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": ErrorCode.VALIDATION_ERROR.value,
                    "detail": "request validation failed",
                    "errors": [
                        {"loc": list(item["loc"]), "type": item["type"]} for item in error.errors()
                    ],
                }
            },
        )

    @app.exception_handler(Exception)
    async def unexpected_error_handler(request: Request, error: Exception) -> JSONResponse:
        """Hide unexpected exception details behind a traceable opaque error id."""
        del request
        error_id: str = new_id()
        logger.error(
            "Unhandled API exception error_id=%s exception_type=%s",
            error_id,
            type(error).__name__,
        )
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": ErrorCode.INTERNAL_ERROR.value,
                    "detail": "internal error",
                    "error_id": error_id,
                }
            },
        )

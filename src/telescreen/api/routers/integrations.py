"""Unregistered integrations status and test endpoints."""

from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, Response

from telescreen.api.common.auth import Principal, require_permission
from telescreen.api.common.context import ApiContext
from telescreen.core.clock import ensure_utc
from telescreen.core.errors import TelescreenError
from telescreen.core.json import (
    JsonValue,
    dump_json,
    json_array,
    json_bool,
    json_null,
    json_object,
    json_str,
)
from telescreen.core.permissions import Permission
from telescreen.core.vocabulary import ErrorCode, IntegrationKind
from telescreen.integrations.repository import IntegrationCheckRepository
from telescreen.integrations.status import IntegrationStatus, IntegrationStatusService
from telescreen.integrations.tests import IntegrationCheckResult, IntegrationTester


@dataclass(frozen=True)
class IntegrationsRouterDeps:
    """Dependencies required by integration routes."""

    ctx: ApiContext
    status: IntegrationStatusService
    tester: IntegrationTester
    checks: IntegrationCheckRepository


def build_integrations_router(deps: IntegrationsRouterDeps) -> APIRouter:
    """Build status and integration-test routes for later wiring."""
    router: APIRouter = APIRouter()

    @router.get("/api/integrations")
    async def list_integrations(
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.INTEGRATIONS_READ))
        ],
    ) -> Response:
        """Return configured flags and last safe check results."""
        del principal
        async with deps.ctx.sessions() as session:
            items: tuple[IntegrationStatus, ...] = await deps.status.list(session)
        return Response(
            content=dump_json(json_array(tuple(_status_json(item) for item in items))),
            media_type="application/json",
        )

    @router.post("/api/integrations/{kind}/test")
    async def test_integration(
        kind: str,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.INTEGRATIONS_TEST))
        ],
    ) -> Response:
        """Run a configured integration test and return its safe result."""
        try:
            integration: IntegrationKind = IntegrationKind(kind)
        except ValueError:
            raise TelescreenError(ErrorCode.NOT_FOUND, "integration not found") from None
        if (
            integration in (IntegrationKind.SLACK, IntegrationKind.EMAIL)
            and principal.member_id is None
        ):
            raise TelescreenError(ErrorCode.FORBIDDEN, "member principal required")
        async with deps.ctx.sessions.begin() as session:
            try:
                result: IntegrationCheckResult = await deps.tester.run(
                    integration, principal.member_id or "", session
                )
            except TelescreenError as error:
                if error.code is ErrorCode.INTEGRATION_UNCONFIGURED:
                    return JSONResponse(
                        status_code=503,
                        content={
                            "error": {
                                "code": ErrorCode.INTEGRATION_UNCONFIGURED.value,
                                "detail": "integration is not configured",
                            }
                        },
                    )
                raise
        return Response(content=dump_json(_result_json(result)), media_type="application/json")

    return router


def _status_json(item: IntegrationStatus) -> JsonValue:
    """Serialize a status using booleans, timestamps, enum codes and docs path."""
    return json_object(
        (
            ("kind", json_str(item.kind.value)),
            ("configured", json_bool(item.configured)),
            (
                "last_check_at",
                json_str(ensure_utc(item.last_check_at).isoformat())
                if item.last_check_at is not None
                else json_null(),
            ),
            (
                "last_check_ok",
                json_bool(item.last_check_ok) if item.last_check_ok is not None else json_null(),
            ),
            (
                "error_code",
                json_str(item.error_code.value) if item.error_code is not None else json_null(),
            ),
            ("docs_path", json_str(item.docs_path)),
        )
    )


def _result_json(item: IntegrationCheckResult) -> JsonValue:
    """Serialize a test outcome without provider details or connection data."""
    return json_object(
        (
            ("kind", json_str(item.kind.value)),
            ("checked_at", json_str(ensure_utc(item.checked_at).isoformat())),
            ("ok", json_bool(item.ok)),
            (
                "error_code",
                json_str(item.error_code.value) if item.error_code is not None else json_null(),
            ),
        )
    )

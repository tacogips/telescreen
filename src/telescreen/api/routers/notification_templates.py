"""REST endpoints for notification template versions."""

from dataclasses import dataclass
from typing import Annotated

import pydantic
from fastapi import APIRouter, Depends, Response
from fastapi.responses import JSONResponse

from telescreen.api.common.auth import Principal, require_permission
from telescreen.api.common.context import ApiContext
from telescreen.core.clock import ensure_utc
from telescreen.core.errors import TelescreenError
from telescreen.core.json import (
    JsonValue,
    dump_json,
    json_array,
    json_bool,
    json_int,
    json_null,
    json_object,
    json_str,
)
from telescreen.core.permissions import Permission
from telescreen.core.vocabulary import ErrorCode, NotificationEvent
from telescreen.notifications.templates.model import (
    PreviewResult,
    TemplateIssue,
    TemplateTexts,
    TemplateValidationError,
)
from telescreen.notifications.templates.service import (
    TemplateDetail,
    TemplateService,
    TemplateSummary,
    TemplateVersionView,
)


@pydantic.dataclasses.dataclass(frozen=True)
class TemplateBody:
    """Candidate template text submitted for validation or creation."""

    event_kind: NotificationEvent
    subject: str
    body: str
    email_subject: str | None = None
    email_body: str | None = None
    slack_body: str | None = None
    description: str | None = None


@pydantic.dataclasses.dataclass(frozen=True)
class TemplateCreateBody:
    """A named candidate template version."""

    name: str
    event_kind: NotificationEvent
    subject: str
    body: str
    email_subject: str | None = None
    email_body: str | None = None
    slack_body: str | None = None
    description: str | None = None


@pydantic.dataclasses.dataclass(frozen=True)
class TemplateActivateBody:
    """Version to activate for a template name."""

    version: int


@pydantic.dataclasses.dataclass(frozen=True)
class TemplatePreviewBody:
    """Candidate text or an existing template version to preview."""

    event_kind: NotificationEvent | None = None
    subject: str | None = None
    body: str | None = None
    email_subject: str | None = None
    email_body: str | None = None
    slack_body: str | None = None
    description: str | None = None
    name: str | None = None
    version: int | None = None


@dataclass(frozen=True)
class NotificationTemplatesRouterDeps:
    """Dependencies required by notification template routes."""

    ctx: ApiContext
    service: TemplateService


def _texts(
    subject: str,
    body: str,
    email_subject: str | None,
    email_body: str | None,
    slack_body: str | None,
    description: str | None,
) -> TemplateTexts:
    """Convert validated API text fields into a service value."""
    return TemplateTexts(
        subject=subject,
        body=body,
        email_subject=email_subject,
        email_body=email_body,
        slack_body=slack_body,
        description=description,
    )


def _issues(issues: tuple[TemplateIssue, ...]) -> JsonValue:
    """Serialize template diagnostics without including candidate text."""
    return json_array(
        tuple(
            json_object((("field", json_str(issue.field)), ("code", json_str(issue.code.value))))
            for issue in issues
        )
    )


def _preview(result: PreviewResult) -> JsonValue:
    """Serialize rendered, DLP-gated preview variants."""
    return json_object(
        (
            (
                "email",
                json_object(
                    (
                        ("subject", json_str(result.email.subject)),
                        ("body", json_str(result.email.body)),
                        ("dlp_verdict", json_str(result.email.dlp_verdict.value)),
                    )
                ),
            ),
            (
                "slack",
                json_object(
                    (
                        ("subject", json_str(result.slack.subject)),
                        ("body", json_str(result.slack.body)),
                        ("dlp_verdict", json_str(result.slack.dlp_verdict.value)),
                    )
                ),
            ),
        )
    )


def _version(view: TemplateVersionView) -> JsonValue:
    """Serialize a requested template version and its authored text."""
    return json_object(
        (
            ("name", json_str(view.name)),
            ("version", json_int(view.version)),
            ("event_kind", json_str(view.event_kind.value)),
            ("active", json_bool(view.active)),
            ("subject", json_str(view.texts.subject)),
            ("body", json_str(view.texts.body)),
            (
                "email_subject",
                json_str(view.texts.email_subject)
                if view.texts.email_subject is not None
                else json_null(),
            ),
            (
                "email_body",
                json_str(view.texts.email_body)
                if view.texts.email_body is not None
                else json_null(),
            ),
            (
                "slack_body",
                json_str(view.texts.slack_body)
                if view.texts.slack_body is not None
                else json_null(),
            ),
            (
                "description",
                json_str(view.texts.description)
                if view.texts.description is not None
                else json_null(),
            ),
        )
    )


def build_notification_templates_router(deps: NotificationTemplatesRouterDeps) -> APIRouter:
    """Build the authenticated notification-template API."""
    router: APIRouter = APIRouter()

    @router.get("/api/notification-templates")
    async def list_templates(
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.TEMPLATES_READ))
        ],
    ) -> JSONResponse:
        """List built-in and database template names."""
        del principal
        async with deps.ctx.sessions() as session:
            templates: tuple[TemplateSummary, ...] = await deps.service.list_templates(session)
        return JSONResponse(
            content={
                "items": [
                    {
                        "name": item.name,
                        "event_kind": item.event_kind.value,
                        "builtin": item.builtin,
                        "description": item.description,
                        "active_version": item.active_version,
                        "latest_version": item.latest_version,
                        "updated_at": (
                            ensure_utc(item.updated_at).isoformat()
                            if item.updated_at is not None
                            else None
                        ),
                    }
                    for item in templates
                ],
                "next_cursor": None,
            }
        )

    @router.post("/api/notification-templates/validate")
    async def validate_template(
        body: TemplateBody,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.TEMPLATES_READ))
        ],
    ) -> Response:
        """Validate candidate text and return only field/code diagnostics."""
        del principal
        async with deps.ctx.sessions() as session:
            issues: tuple[TemplateIssue, ...] = await deps.service.validate(
                session,
                body.event_kind,
                _texts(
                    body.subject,
                    body.body,
                    body.email_subject,
                    body.email_body,
                    body.slack_body,
                    body.description,
                ),
            )
        return Response(
            content=dump_json(
                json_object((("valid", json_bool(not issues)), ("errors", _issues(issues))))
            ),
            media_type="application/json",
        )

    @router.post("/api/notification-templates/preview")
    async def preview_template(
        body: TemplatePreviewBody,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.TEMPLATES_READ))
        ],
    ) -> Response:
        """Render either candidate text or an existing version using synthetic context."""
        del principal
        if body.name is not None or body.version is not None:
            if (
                body.name is None
                or body.version is None
                or body.event_kind is not None
                or body.subject is not None
                or body.body is not None
                or body.email_subject is not None
                or body.email_body is not None
                or body.slack_body is not None
                or body.description is not None
            ):
                raise TelescreenError(
                    ErrorCode.VALIDATION_ERROR, "preview requires exactly one input form"
                )
            async with deps.ctx.sessions() as session:
                view: TemplateVersionView = await deps.service.version_text(
                    session, body.name, body.version
                )
            result: PreviewResult = await deps.service.preview(view.event_kind, view.texts)
        else:
            if body.event_kind is None or body.subject is None or body.body is None:
                raise TelescreenError(
                    ErrorCode.VALIDATION_ERROR, "preview requires exactly one input form"
                )
            result = await deps.service.preview(
                body.event_kind,
                _texts(
                    body.subject,
                    body.body,
                    body.email_subject,
                    body.email_body,
                    body.slack_body,
                    body.description,
                ),
            )
        return Response(content=dump_json(_preview(result)), media_type="application/json")

    @router.post("/api/notification-templates", status_code=201)
    async def create_template(
        body: TemplateCreateBody,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.TEMPLATES_WRITE))
        ],
    ) -> Response:
        """Create an inactive version without echoing invalid template text."""
        name: str = body.name
        version: int = 0
        rejected: TemplateValidationError | None = None
        async with deps.ctx.sessions.begin() as session:
            try:
                name, version = await deps.service.create_version(
                    session,
                    body.name,
                    body.event_kind,
                    _texts(
                        body.subject,
                        body.body,
                        body.email_subject,
                        body.email_body,
                        body.slack_body,
                        body.description,
                    ),
                    principal.actor_ref(),
                )
            except TemplateValidationError as error:
                # Commit the template.rejected audit record appended by the service.
                rejected = error
        if rejected is not None:
            return Response(
                status_code=422,
                content=dump_json(
                    json_object(
                        (
                            (
                                "error",
                                json_object(
                                    (
                                        ("code", json_str(ErrorCode.VALIDATION_ERROR.value)),
                                        ("detail", json_str("template validation failed")),
                                    )
                                ),
                            ),
                            ("errors", _issues(rejected.issues)),
                        )
                    )
                ),
                media_type="application/json",
            )
        return JSONResponse(status_code=201, content={"name": name, "version": version})

    @router.post("/api/notification-templates/{name}/activate")
    async def activate_template(
        name: str,
        body: TemplateActivateBody,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.TEMPLATES_WRITE))
        ],
    ) -> JSONResponse:
        """Activate a template version and return the resulting safe metadata."""
        async with deps.ctx.sessions.begin() as session:
            await deps.service.activate(session, name, body.version, principal.actor_ref())
        return JSONResponse(content={"name": name, "active_version": body.version})

    @router.get("/api/notification-templates/{name}/versions/{version}")
    async def get_template_version(
        name: str,
        version: int,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.TEMPLATES_READ))
        ],
    ) -> Response:
        """Return one version's editable text fields."""
        del principal
        async with deps.ctx.sessions() as session:
            view: TemplateVersionView = await deps.service.version_text(session, name, version)
        return Response(content=dump_json(_version(view)), media_type="application/json")

    @router.get("/api/notification-templates/{name}")
    async def get_template(
        name: str,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.TEMPLATES_READ))
        ],
    ) -> JSONResponse:
        """Return template versions and the event allow-list."""
        del principal
        async with deps.ctx.sessions() as session:
            detail: TemplateDetail = await deps.service.get(session, name)
        return JSONResponse(
            content={
                "name": detail.name,
                "event_kind": detail.event_kind.value,
                "builtin": detail.builtin,
                "active_version": detail.active_version,
                "versions": [
                    {
                        "version": item.version,
                        "active": item.active,
                        "created_by": item.created_by,
                        "created_at": (
                            ensure_utc(item.created_at).isoformat()
                            if item.created_at is not None
                            else None
                        ),
                    }
                    for item in detail.versions
                ],
                "variables": list(detail.variables),
            }
        )

    return router

"""Composition helpers for v1.2 templates, roles, OIDC and routers."""

from __future__ import annotations

from dataclasses import dataclass

import httpx
from fastapi import APIRouter

from telescreen.api.common.context import ApiContext
from telescreen.api.routers.auth import AuthRouterDeps, build_auth_router
from telescreen.api.routers.users import UsersRouterDeps, build_users_router
from telescreen.core.config import TelescreenSettings
from telescreen.core.oidc_settings import OidcSettings
from telescreen.directory.provisioning import DirectoryProvisioner
from telescreen.directory.roles import RoleService
from telescreen.directory.tokens import TokenService
from telescreen.identity.discovery import OidcProviderClient
from telescreen.identity.signin import SignInService
from telescreen.identity.verifier import OidcBearerVerifier
from telescreen.notifications.dlp import DlpGate
from telescreen.ports.identity import MemberProvisioner
from telescreen.ports.notifications import NotificationSender
from telescreen.ports.templates import TemplateCatalog
from telescreen.ports.workflow import TaskHandler
from telescreen.wiring_builders import RuntimePrimitives


@dataclass(frozen=True)
class WiringV12Parts:
    """V1.2 services needed by the composition root to finish container assembly."""

    templates: TemplateCatalog
    roles: RoleService
    provisioner: DirectoryProvisioner
    verifier: OidcBearerVerifier | None
    sign_in: SignInService | None
    routers: tuple[APIRouter, ...]


@dataclass(frozen=True)
class WiringV12:
    """Public v1.2 container services used by the CLI and API host."""

    templates: TemplateCatalog
    notifications: NotificationSender
    delivery: TaskHandler
    roles: RoleService
    sign_in: SignInService | None


def build_v12(
    settings: TelescreenSettings,
    primitives: RuntimePrimitives,
    gate: DlpGate,
    http: httpx.AsyncClient,
    token_service: TokenService,
    template_catalog: TemplateCatalog | None = None,
    split: bool = False,
) -> WiringV12Parts:
    """Build v1.2 template, role and optional OIDC services and API routers."""
    local_templates: TemplateCatalog | None = None
    templates: TemplateCatalog
    if template_catalog is None:
        from telescreen.notifications.templates.repository import TemplateRepository
        from telescreen.notifications.templates.service import TemplateService

        template_repository: TemplateRepository = TemplateRepository()
        local_templates = TemplateService(
            template_repository,
            primitives.redactor,
            gate,
            primitives.audit,
            primitives.clock,
        )
        templates = local_templates
    else:
        templates = template_catalog
    roles: RoleService = RoleService(primitives.audit, primitives.clock)
    provisioner: DirectoryProvisioner = DirectoryProvisioner(
        primitives.fingerprinter, roles, primitives.audit, primitives.clock
    )
    oidc: OidcSettings | None = settings.oidc
    verifier: OidcBearerVerifier | None = None
    sign_in: SignInService | None = None
    identities: MemberProvisioner | None = None
    if oidc is not None:
        provider: OidcProviderClient = OidcProviderClient(http, oidc, primitives.clock)
        verifier = OidcBearerVerifier(provider, oidc, primitives.clock)
        sign_in = SignInService(
            primitives.sessions,
            verifier,
            provisioner,
            oidc,
            primitives.audit,
            primitives.fingerprinter,
        )
        identities = provisioner
    context: ApiContext = ApiContext(
        primitives.sessions,
        primitives.clock,
        primitives.audit,
        verifier,
        identities,
    )
    routers: tuple[APIRouter, ...] = (
        build_users_router(UsersRouterDeps(context, roles, token_service, oidc is not None)),
        build_auth_router(
            AuthRouterDeps(context, oidc, settings.ui_token_login, verifier, sign_in)
        ),
    )
    if not split:
        from telescreen.notifications.templates.service import TemplateService

        if local_templates is None:
            raise RuntimeError("local template service is unavailable")
        if not isinstance(local_templates, TemplateService):
            raise RuntimeError("local template service has an unsupported implementation")
        from telescreen.api.routers.notification_templates import (
            NotificationTemplatesRouterDeps,
            build_notification_templates_router,
        )

        routers = (
            build_notification_templates_router(
                NotificationTemplatesRouterDeps(context, local_templates)
            ),
            *routers,
        )
    return WiringV12Parts(templates, roles, provisioner, verifier, sign_in, routers)

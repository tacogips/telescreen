"""Router ordering and permission declaration checks for p07 endpoints."""

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.api.common import install_error_handlers
from telescreen.api.common.auth import PermissionDependency, Principal
from telescreen.api.common.context import ApiContext
from telescreen.api.routers.data_asset_values import (
    DataAssetValuesRouterDeps,
    build_data_asset_values_router,
)
from telescreen.api.routers.data_fields import DataFieldsRouterDeps, build_data_fields_router
from telescreen.core.clock import FakeClock
from telescreen.core.errors import TelescreenError
from telescreen.core.permissions import Permission, role_permissions
from telescreen.core.vocabulary import ActorDisplayKind, ErrorCode, PrincipalKind, Role
from telescreen.db.models import DataAssetRow, DataFieldRow, InventoryCounterRow
from telescreen.inventory.fields import FieldService
from telescreen.inventory.queries import AssetQueries
from telescreen.inventory.registration import RegistrationService
from telescreen.inventory.repository import InventoryRepository
from telescreen.inventory.values import ValueService, ValueValidator
from telescreen.masking.fingerprint import Fingerprinter
from telescreen.masking.redact import Redactor
from telescreen.ports.asset_context import AssetIssueLookup
from telescreen.ports.audit import AuditEntry, AuditLog
from telescreen.ports.directory import (
    ActorDisplay,
    AssigneeIds,
    ResolvedRecipients,
)
from telescreen.ports.identity_context import ActorLookup
from telescreen.ports.issues import ResourceIssueRef
from tests.support.db import SqliteHarness, make_sqlite_harness


class EmptyAudit(AuditLog):
    """Accept audit writes during route construction."""

    async def append(self, session: AsyncSession, entry: AuditEntry) -> int:
        """Return a stable placeholder sequence."""
        del session, entry
        return 1


class EmptyRecipients:
    """Provide the resolver dependency without external directory calls."""

    async def resolve(self, session: AsyncSession, ref: str) -> ResolvedRecipients:
        """Return an empty recipient projection."""
        del session, ref
        return ResolvedRecipients((), (), (), ())

    async def is_member_of(self, session: AsyncSession, member_id: str, ref: str) -> bool:
        """Return false for an unused membership check."""
        del session, member_id, ref
        return False

    async def assignee_of(self, ref: str) -> AssigneeIds:
        """Raise the static unknown-recipient boundary."""
        del ref
        from telescreen.core.errors import TelescreenError

        raise TelescreenError(ErrorCode.RECIPIENT_UNKNOWN, "recipient is unknown")


class EmptyIssueReader(AssetIssueLookup):
    """Return no issue references."""

    async def open_issues_for_resources(
        self, resource_keys: tuple[str, ...]
    ) -> tuple[ResourceIssueRef, ...]:
        """Return no issue references for every resource key."""
        del resource_keys
        return ()


class EmptyActors(ActorLookup):
    """Return an empty actor view for each requested ref."""

    async def display(self, refs: tuple[str, ...]) -> tuple[ActorDisplay, ...]:
        """Provide a typed empty sequence for router construction only."""
        return tuple(
            ActorDisplay(ref, ActorDisplayKind.UNKNOWN, "Unknown", None, False) for ref in refs
        )


@dataclass(frozen=True)
class RolePermissionProvider:
    """Return an authenticated test principal with the selected role."""

    role: Role
    permission: Permission

    async def __call__(self) -> Principal:
        """Build the principal supplied by a route's permission dependency."""
        permissions: frozenset[Permission] = role_permissions(self.role)
        if self.permission not in permissions:
            raise TelescreenError(ErrorCode.FORBIDDEN, "permission is not granted")
        return Principal(
            PrincipalKind.OIDC,
            None,
            "admin",
            self.role,
            permissions,
        )


@pytest.mark.asyncio
async def test_p07_routes_have_one_permission_and_static_routes_precede_id_routes() -> None:
    """All ten field/value routes declare one permission and static field routes come first."""
    harness: SqliteHarness = await make_sqlite_harness()
    now: datetime = datetime(2026, 10, 13, tzinfo=UTC)
    clock: FakeClock = FakeClock(now)
    audit: EmptyAudit = EmptyAudit()
    redactor: Redactor = Redactor.default()
    context: ApiContext = ApiContext(harness.sessions, clock, audit)
    repository: InventoryRepository = InventoryRepository()
    recipients: EmptyRecipients = EmptyRecipients()
    validator: ValueValidator = ValueValidator(recipients, redactor)
    values: ValueService = ValueService(repository, validator, audit, clock)
    registration: RegistrationService = RegistrationService(
        repository, values, Fingerprinter(b"f" * 32, "test"), redactor, audit, clock
    )
    fields: FieldService = FieldService(audit, redactor, clock)
    queries: AssetQueries = AssetQueries(EmptyIssueReader(), EmptyActors())
    routers: tuple[APIRouter, ...] = (
        build_data_fields_router(DataFieldsRouterDeps(context, fields, validator)),
        build_data_asset_values_router(
            DataAssetValuesRouterDeps(context, values, registration, queries)
        ),
    )
    try:
        routes: tuple[APIRoute, ...] = tuple(
            route for router in routers for route in router.routes if isinstance(route, APIRoute)
        )
        assert len(routes) == 10
        for route in routes:
            permissions: tuple[PermissionDependency, ...] = tuple(
                dependency.call
                for dependency in route.dependant.dependencies
                if isinstance(dependency.call, PermissionDependency)
            )
            assert len(permissions) == 1
        field_routes: tuple[APIRoute, ...] = tuple(
            route for route in routes if route.path.startswith("/api/data/fields/")
        )
        paths: tuple[str, ...] = tuple(route.path for route in field_routes)
        assert paths.index("/api/data/fields/validate-values") < paths.index(
            "/api/data/fields/{field_id}"
        )
        assert paths.index("/api/data/fields/reorder") < paths.index("/api/data/fields/{field_id}")
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_values_validation_put_and_registration() -> None:
    """The API reports safe validation errors and supports values and registration."""
    harness: SqliteHarness = await make_sqlite_harness()
    now: datetime = datetime(2026, 10, 13, tzinfo=UTC)
    clock: FakeClock = FakeClock(now)
    audit: EmptyAudit = EmptyAudit()
    redactor: Redactor = Redactor.default()
    context: ApiContext = ApiContext(harness.sessions, clock, audit)
    repository: InventoryRepository = InventoryRepository()
    recipients: EmptyRecipients = EmptyRecipients()
    validator: ValueValidator = ValueValidator(recipients, redactor)
    values: ValueService = ValueService(repository, validator, audit, clock)
    registration: RegistrationService = RegistrationService(
        repository, values, Fingerprinter(b"f" * 32, "test"), redactor, audit, clock
    )
    fields: FieldService = FieldService(audit, redactor, clock)
    queries: AssetQueries = AssetQueries(EmptyIssueReader(), EmptyActors())
    routers: tuple[APIRouter, ...] = (
        build_data_fields_router(DataFieldsRouterDeps(context, fields, validator)),
        build_data_asset_values_router(
            DataAssetValuesRouterDeps(context, values, registration, queries)
        ),
    )
    admin: FastAPI = _app(routers, Role.ADMIN)
    viewer: FastAPI = _app(routers, Role.VIEWER)
    try:
        async with harness.sessions.begin() as session:
            field_row: DataFieldRow = DataFieldRow(
                id="field-purpose",
                key="purpose",
                label="Purpose",
                description=None,
                field_type="text",
                options_json=None,
                required=True,
                position=0,
                role="purpose",
                archived_at=None,
                created_by="member:admin",
                created_at=now,
                updated_at=now,
            )
            asset: DataAssetRow = DataAssetRow(
                id="asset-1",
                number=1,
                resource_key="a" * 64,
                source="drive",
                origin="discovered",
                kind="file",
                state="present",
                name="Asset",
                display_locator="https://example.test/asset",
                collector_config_id=None,
                sweep_covered=False,
                item_count=None,
                first_seen_at=now,
                last_seen_at=now,
                deleted_at=None,
                overdue_notified_for=None,
                created_by=None,
                created_at=now,
                updated_at=now,
            )
            counter: InventoryCounterRow = InventoryCounterRow(name="data_asset_number", value=1)
            session.add_all((field_row, asset, counter))
        async with AsyncClient(
            transport=ASGITransport(app=admin), base_url="http://test"
        ) as client:
            validation: Response = await client.post(
                "/api/data/fields/validate-values",
                json={"values": [{"key": "unknown", "value_json": '"hidden"'}]},
            )
            assert validation.status_code == 200
            assert validation.json() == {
                "valid": False,
                "errors": [
                    {"field": "unknown", "code": "unknown_field"},
                    {"field": "purpose", "code": "required"},
                ],
            }
            update: Response = await client.put(
                "/api/data/assets/asset-1/fields",
                json={"values": [{"key": "purpose", "value_json": '"Finance"'}]},
            )
            assert update.status_code == 200
            assert update.json()["fields"][0]["value_json"] == '"Finance"'
            registration_response: Response = await client.post(
                "/api/data/assets",
                json={
                    "name": "New report",
                    "source": "manual",
                    "locator": "https://example.test/new",
                    "kind": "file",
                    "values": [{"key": "purpose", "value_json": '"Finance"'}],
                },
            )
            assert registration_response.status_code == 201
            assert registration_response.json()["fields"][0]["value_json"] == '"Finance"'
        async with AsyncClient(
            transport=ASGITransport(app=viewer), base_url="http://test"
        ) as client:
            denied_admin: Response = await client.post(
                "/api/data/fields",
                json={"key": "owner", "label": "Owner", "field_type": "text"},
            )
            denied_write: Response = await client.put(
                "/api/data/assets/asset-1/fields",
                json={"values": [{"key": "purpose", "value_json": '"Finance"'}]},
            )
            assert denied_admin.status_code == 403
            assert denied_write.status_code == 403
    finally:
        await harness.close()


def _app(routers: tuple[APIRouter, ...], role: Role) -> FastAPI:
    """Build an app and override route guards with real role permission sets."""
    app: FastAPI = FastAPI()
    for router in routers:
        app.include_router(router)
    install_error_handlers(app)
    for router in routers:
        for route in router.routes:
            if isinstance(route, APIRoute):
                for dependency in route.dependant.dependencies:
                    if isinstance(dependency.call, PermissionDependency):
                        app.dependency_overrides[dependency.call] = RolePermissionProvider(
                            role, dependency.call.permission
                        )
    return app

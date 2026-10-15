"""HTTP implementations of the existing detached owner contracts."""

from dataclasses import dataclass

from fastapi import Request
from pydantic import TypeAdapter

from telescreen.inventory_service.transport import ServiceHttp
from telescreen.ports.asset_context import ResourceIssueRef
from telescreen.ports.audit_commands import AuditCommand, AuditReceipt, encode_audit_command
from telescreen.ports.identity_context import ActorDisplay, AssigneeIds
from telescreen.ports.inventory_commands import (
    InventoryCommand,
    InventoryReceipt,
    encode_inventory_command,
)
from telescreen.ports.principal import Principal
from telescreen.ports.retention import (
    RetentionCommand,
    RetentionReceipt,
    RetentionStatus,
    encode_retention_command,
)


@dataclass(frozen=True)
class BureauClient:
    """Read bureau-owned context and publish audit/notification intents over HTTP."""

    transport: ServiceHttp

    async def authenticate(self, request: Request) -> Principal:
        """Delegate credential verification, keeping permission enforcement local."""
        return await self.transport.post(
            "/authenticate",
            "null",
            TypeAdapter(Principal),
            request.headers.get("authorization", ""),
        )

    async def open_issues_for_resources(
        self, resource_keys: tuple[str, ...]
    ) -> tuple[ResourceIssueRef, ...]:
        """Read only detached safe issue references."""
        return await self.transport.post(
            "/issues",
            TypeAdapter(tuple[str, ...]).dump_json(resource_keys).decode(),
            TypeAdapter(tuple[ResourceIssueRef, ...]),
        )

    async def display(self, refs: tuple[str, ...]) -> tuple[ActorDisplay, ...]:
        """Resolve actor displays with the bureau's identity owner."""
        return await self.transport.post(
            "/actors",
            TypeAdapter(tuple[str, ...]).dump_json(refs).decode(),
            TypeAdapter(tuple[ActorDisplay, ...]),
        )

    async def assignee_of(self, ref: str) -> AssigneeIds:
        """Validate assignment without identity table access."""
        return await self.transport.post(
            "/assignee", TypeAdapter(str).dump_json(ref).decode(), TypeAdapter(AssigneeIds)
        )


@dataclass(frozen=True)
class InventoryClient:
    """Inventory command and eligibility adapters used by the bureau."""

    transport: ServiceHttp

    async def receive(self, command: InventoryCommand) -> InventoryReceipt:
        """Return only validated committed receipt values."""
        return await self.transport.post(
            "/commands", encode_inventory_command(command), TypeAdapter(InventoryReceipt)
        )

    async def status(self, command: RetentionCommand) -> RetentionStatus:
        """Revalidate the exact due-date revision at its owning service."""
        return await self.transport.post(
            "/retention-status", encode_retention_command(command), TypeAdapter(RetentionStatus)
        )


@dataclass(frozen=True)
class AuditClient:
    """Implement the audit receipt port without audit persistence access."""

    transport: ServiceHttp

    async def receive(self, command: AuditCommand) -> AuditReceipt:
        """Preserve command identity across transport retries."""
        return await self.transport.post(
            "/audit", encode_audit_command(command), TypeAdapter(AuditReceipt)
        )


@dataclass(frozen=True)
class RetentionClient:
    """Implement notification handoff without recipient or channel logic."""

    transport: ServiceHttp

    async def receive(self, command: RetentionCommand) -> RetentionReceipt:
        """Acknowledge only a committed notification-owner receipt."""
        return await self.transport.post(
            "/retention", encode_retention_command(command), TypeAdapter(RetentionReceipt)
        )

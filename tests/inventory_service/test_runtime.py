"""Standalone inventory routes and real retention dispatch use local ownership."""

from collections.abc import Awaitable, Callable
from dataclasses import replace

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import set_committed_value

from telescreen.audit.chain import INVENTORY_AUDIT_MODELS
from telescreen.audit.service import AuditLogService
from telescreen.audit.verify import ChainVerification, verify_chain
from telescreen.core.json import json_null
from telescreen.core.vocabulary import (
    DataAssetKind,
    DataFieldRole,
    DataFieldType,
    EngineTaskKind,
    IdentityKind,
    NotificationChannel,
)
from telescreen.db.models import DataAssetRow, DataFieldRow
from telescreen.db.models.audit_handoff import InventoryAuditOutboxRow
from telescreen.db.models.notification_handoff import InventoryNotificationOutboxRow
from telescreen.db.models.retention_handoff import InventoryRetentionOutboxRow
from telescreen.db.models.service_audit import InventoryAuditRecordRow
from telescreen.inventory.registration import RegistrationBody
from telescreen.inventory.retention import DataOverdueHandler, _DueAsset
from telescreen.inventory.retention_outbox import RetentionOutbox
from telescreen.inventory.values import ValueInput
from telescreen.inventory_service.cli import _audit_command
from telescreen.inventory_service.transport import PREFIX
from telescreen.ports.audit import AuditEntry
from telescreen.ports.directory_projection import (
    DirectoryEntity,
    DirectoryEntityKind,
    IdentityEntity,
    MemberEntity,
    OwnershipRuleEntry,
    OwnershipRulesEntity,
    TeamEntity,
)
from telescreen.ports.inventory_commands import (
    DirectoryUpdate,
    InventoryCommand,
    IssueLinkState,
    encode_inventory_command,
)
from telescreen.ports.notification_commands import (
    MemberSendOperation,
    NotificationCommand,
    NotificationCommandReceipt,
    NotificationOperation,
    SendOperation,
    decode_notification_command,
)
from telescreen.ports.retention import RetentionCommand, decode_retention_command
from telescreen.ports.workflow import ClaimedTask
from tests.inventory_service.conftest import InventoryWorld
from tests.support.samples import github_token

type ProjectionEntity = MemberEntity | TeamEntity | IdentityEntity | OwnershipRulesEntity
type ProjectionOperation = DirectoryUpdate | IssueLinkState


async def test_inventory_routes_use_local_token_replica(inventory_world: InventoryWorld) -> None:
    """Public inventory reads work with replica auth and retain service auth."""
    world: InventoryWorld = inventory_world
    response: httpx.Response = await world.client.get("/api/data/assets", headers=world.headers)
    assert response.status_code == 200, response.text
    assert response.json()["items"] == []
    health: httpx.Response = await world.client.get(PREFIX + "/health", headers=world.headers)
    assert health.status_code == 200
    assert health.json() == {"status": "ok", "directory_revision": 1}
    no_service_token: httpx.Response = await world.client.get(
        "/api/data/assets", headers=(("authorization", f"Bearer {world.token}"),)
    )
    assert no_service_token.status_code == 401


async def test_inventory_has_no_retention_callback_or_bureau_destination(
    inventory_world: InventoryWorld,
) -> None:
    """Inventory exposes no eligibility callback and has no Telescreen origin configured."""
    world: InventoryWorld = inventory_world
    response: httpx.Response = await world.client.post(
        PREFIX + "/retention-status", headers=world.headers, content="{}"
    )
    assert response.status_code == 404
    assert world.runtime.context.authentication is not None
    assert all("bureau" not in url for url in (world.network.requests or ()))


async def test_retention_dispatches_member_and_team_through_notification(
    inventory_world: InventoryWorld,
) -> None:
    """Member and team obligations commit local outboxes, audit and remote receipts."""
    world: InventoryWorld = inventory_world
    await _push_directory(world, _member(), _team())
    member_asset: str = await _seed_overdue_asset(world, "member:alice", "member")
    member_event: str = await _publish_retention(world, member_asset)
    member_command: NotificationCommand = await _dispatch(world, member_event)
    assert member_command.event_id == member_event
    assert len(member_command.operations) == 1
    member_operation: NotificationOperation = member_command.operations[0]
    assert isinstance(member_operation, MemberSendOperation)
    assert member_operation.member_id == "member-1"
    assert await _has_audit(
        world, member_asset, "data_asset.overdue_notified", '"recipient_kind":"member"'
    )
    assert await _has_audit(
        world, member_asset, "data_asset.overdue_notified", '"notification_count":1'
    )

    team_asset: str = await _seed_overdue_asset(world, "team:ops", "team")
    team_event: str = await _publish_retention(world, team_asset)
    team_command: NotificationCommand = await _dispatch(world, team_event)
    team_operations: tuple[SendOperation, ...] = tuple(
        item for item in team_command.operations if isinstance(item, SendOperation)
    )
    assert len(team_operations) == 2
    assert tuple(item.channel for item in team_operations) == (
        NotificationChannel.EMAIL,
        NotificationChannel.SLACK,
    )
    assert all(item.recipients == ("team:ops",) for item in team_operations)
    assert all(item.record_unreachable for item in team_operations)
    assert await _has_audit(
        world, team_asset, "data_asset.overdue_notified", '"recipient_kind":"team"'
    )
    assert await _has_audit(
        world, team_asset, "data_asset.overdue_notified", '"notification_count":2'
    )
    _assert_notification_only(world)


async def test_retention_dispatch_superseded_obligation_is_audited_without_command(
    inventory_world: InventoryWorld,
) -> None:
    """Clearing a due value before dispatch acknowledges only the superseded obligation."""
    world: InventoryWorld = inventory_world
    asset_id: str = await _seed_overdue_asset(world, None, "superseded")
    event_id: str = await _publish_retention(world, asset_id)
    async with world.harness.sessions.begin() as session:
        asset: DataAssetRow | None = await session.get(DataAssetRow, asset_id)
        fields: tuple[DataFieldRow, ...] = tuple(
            (await session.scalars(select(DataFieldRow))).all()
        )
        assert asset is not None
        await world.runtime.values.put(
            session,
            asset,
            fields,
            (ValueInput("deletion_due", None),),
            "member:admin",
        )
    await world.runtime.worker.retention_tick()
    async with world.harness.sessions() as session:
        retention: InventoryRetentionOutboxRow | None = await session.get(
            InventoryRetentionOutboxRow, event_id
        )
        notification: InventoryNotificationOutboxRow | None = await session.get(
            InventoryNotificationOutboxRow, event_id
        )
    assert retention is not None and retention.delivered_at is not None
    assert notification is None
    assert await _has_audit(
        world, asset_id, "data_asset.overdue_superseded", f'"obligation_id":"{event_id}"'
    )


async def test_retention_dispatch_falls_back_through_issue_identity_then_rules_team(
    inventory_world: InventoryWorld,
) -> None:
    """Issue identity ownership wins before a matching ownership-rule team."""
    world: InventoryWorld = inventory_world
    member: MemberEntity = _member()
    team: TeamEntity = _team()
    identity_key: str = "a" * 64
    identity_ref: str = f"{IdentityKind.EMAIL.value}:{identity_key}"
    identity: IdentityEntity = IdentityEntity(
        "identity-1", member.id, IdentityKind.EMAIL.value, identity_key
    )
    rule: OwnershipRuleEntry = OwnershipRuleEntry(
        "rule-1", 0, "manual", "https://example.invalid/**", team.id, True
    )
    await _push_directory(world, member, team, identity, OwnershipRulesEntity((rule,)))
    linked_asset: str = await _seed_overdue_asset(world, None, "linked")
    resource_key: str = await _resource_key(world, linked_asset)
    await _push_link(world, "issue-owner", resource_key, identity_ref)
    linked_event: str = await _publish_retention(world, linked_asset)
    linked_command: NotificationCommand = await _dispatch(world, linked_event)
    linked_operation: NotificationOperation = linked_command.operations[0]
    assert isinstance(linked_operation, MemberSendOperation)
    assert linked_operation.member_id == member.id

    rules_asset: str = await _seed_overdue_asset(world, None, "rules")
    rules_event: str = await _publish_retention(world, rules_asset)
    rules_command: NotificationCommand = await _dispatch(world, rules_event)
    rules_operations: tuple[SendOperation, ...] = tuple(
        item for item in rules_command.operations if isinstance(item, SendOperation)
    )
    assert len(rules_operations) == 2
    assert all(item.recipients == ("team:ops",) for item in rules_operations)


async def test_retention_context_is_redacted_and_notification_receipts_control_ack(
    inventory_world: InventoryWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Raw context is absent, hit counts travel with it, and deferred remains pending."""
    world: InventoryWorld = inventory_world
    await _push_directory(world, _member())
    asset_id: str = await _seed_overdue_asset(world, "member:alice", "redaction")
    sample: str = github_token().value
    original_publish: Callable[[DataOverdueHandler, AsyncSession, _DueAsset], Awaitable[None]] = (
        DataOverdueHandler._publish
    )

    async def publish_with_secret_locator(
        handler: DataOverdueHandler, session: AsyncSession, selected: _DueAsset
    ) -> None:
        asset: DataAssetRow | None = await session.get(DataAssetRow, selected.id)
        assert asset is not None
        safe_locator: str = asset.display_locator
        set_committed_value(asset, "display_locator", f"https://example.invalid/{sample}")
        try:
            await original_publish(handler, session, selected)
        finally:
            set_committed_value(asset, "display_locator", safe_locator)

    monkeypatch.setattr(DataOverdueHandler, "_publish", publish_with_secret_locator)
    event_id: str = await _publish_retention(world, asset_id)
    monkeypatch.setattr(DataOverdueHandler, "_publish", original_publish)
    async with world.harness.sessions() as session:
        asset_after_publish: DataAssetRow | None = await session.get(DataAssetRow, asset_id)
    assert asset_after_publish is not None
    assert sample not in asset_after_publish.display_locator
    original: Callable[[RetentionOutbox, AsyncSession, str], Awaitable[RetentionCommand | None]] = (
        RetentionOutbox.get_pending
    )

    async def pending_with_context(
        outbox: RetentionOutbox, session: AsyncSession, selected_id: str
    ) -> RetentionCommand | None:
        command: RetentionCommand | None = await original(outbox, session, selected_id)
        if command is None or selected_id != event_id:
            return command
        return replace(
            command,
            context=replace(command.context, data_locator=f"https://example.invalid/{sample}"),
        )

    monkeypatch.setattr(RetentionOutbox, "get_pending", pending_with_context)
    await world.runtime.worker.retention_tick()
    async with world.harness.sessions() as session:
        row: InventoryNotificationOutboxRow | None = await session.get(
            InventoryNotificationOutboxRow, event_id
        )
    assert row is not None
    assert sample not in row.payload_json
    command: NotificationCommand = decode_notification_command(row.payload_json)
    operation: NotificationOperation = command.operations[0]
    assert isinstance(operation, MemberSendOperation)
    assert operation.context.data_locator is not None
    assert operation.redaction.context
    assert operation.redaction.context[0].count > 0

    world.network.notification_receipt = NotificationCommandReceipt.DEFERRED
    await world.runtime.worker.notification_tick()
    async with world.harness.sessions() as session:
        deferred: InventoryNotificationOutboxRow | None = await session.get(
            InventoryNotificationOutboxRow, event_id
        )
    assert deferred is not None and deferred.delivered_at is None
    world.network.notification_receipt = NotificationCommandReceipt.APPLIED
    await world.runtime.worker.notification_tick()
    async with world.harness.sessions() as session:
        applied: InventoryNotificationOutboxRow | None = await session.get(
            InventoryNotificationOutboxRow, event_id
        )
    assert applied is not None and applied.delivered_at is not None
    assert world.network.notification_bodies is not None
    assert sample not in "".join(world.network.notification_bodies)
    _assert_notification_only(world)


async def test_retention_audit_failure_rolls_back_command_and_ack(
    inventory_world: InventoryWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An audit append failure rolls back both outbox writes and retention acknowledgment."""
    world: InventoryWorld = inventory_world
    await _push_directory(world, _member())
    asset_id: str = await _seed_overdue_asset(world, "member:alice", "rollback")
    event_id: str = await _publish_retention(world, asset_id)
    original: Callable[[AuditLogService, AsyncSession, AuditEntry], Awaitable[int]] = (
        AuditLogService.append
    )

    async def fail_notified(
        service: AuditLogService, session: AsyncSession, entry: AuditEntry
    ) -> int:
        if entry.action == "data_asset.overdue_notified":
            raise RuntimeError("injected audit append failure")
        return await original(service, session, entry)

    monkeypatch.setattr(AuditLogService, "append", fail_notified)
    await world.runtime.worker.retention_tick()
    async with world.harness.sessions() as session:
        pending: InventoryRetentionOutboxRow | None = await session.get(
            InventoryRetentionOutboxRow, event_id
        )
        notification: InventoryNotificationOutboxRow | None = await session.get(
            InventoryNotificationOutboxRow, event_id
        )
    assert pending is not None and pending.delivered_at is None
    assert notification is None
    monkeypatch.setattr(AuditLogService, "append", original)
    await world.runtime.worker.retention_tick()
    async with world.harness.sessions() as session:
        delivered: InventoryRetentionOutboxRow | None = await session.get(
            InventoryRetentionOutboxRow, event_id
        )
        saved: InventoryNotificationOutboxRow | None = await session.get(
            InventoryNotificationOutboxRow, event_id
        )
    assert delivered is not None and delivered.delivered_at is not None
    assert saved is not None
    assert await world.runtime.worker.retention_tick() is None
    await world.runtime.worker.notification_tick()
    _assert_notification_only(world)


async def test_inventory_audit_drain_verifies_and_cli_reports_valid_chain(
    inventory_world: InventoryWorld, capsys: pytest.CaptureFixture[str]
) -> None:
    """The worker commits local audit commands to a verifiable chain and CLI verifies it."""
    world: InventoryWorld = inventory_world
    await _seed_overdue_asset(world, None, "audit")
    await world.runtime.worker.audit_tick()
    async with world.harness.sessions() as session:
        commands: tuple[InventoryAuditOutboxRow, ...] = tuple(
            (await session.scalars(select(InventoryAuditOutboxRow))).all()
        )
        verification: ChainVerification = await verify_chain(session, INVENTORY_AUDIT_MODELS)
    assert commands
    assert all(command.delivered_at is not None for command in commands)
    assert verification.ok and verification.checked > 0
    database_url: str = world.harness.engine.url.render_as_string(hide_password=False)
    exit_code: int = await _audit_command(database_url, "verify")
    output: str = capsys.readouterr().out
    assert exit_code == 0
    assert "seq=" in output and "checked=" in output


def _member() -> MemberEntity:
    return MemberEntity("member-1", "alice", "Alice Example", True, "responder", None, None)


def _team() -> TeamEntity:
    return TeamEntity("team-1", "ops", "Operations", None, (), None, None)


async def _push_directory(world: InventoryWorld, *entities: ProjectionEntity) -> None:
    for sequence, payload in enumerate(entities, start=1):
        if isinstance(payload, OwnershipRulesEntity):
            entity: DirectoryEntity = DirectoryEntity(
                DirectoryEntityKind.OWNERSHIP_RULES, "rules", sequence, True, payload
            )
        else:
            entity = DirectoryEntity(payload.kind, payload.id, sequence, True, payload)
        await _post_operation(world, sequence, DirectoryUpdate(entity), "r5-directory")


async def _push_link(
    world: InventoryWorld, issue_id: str, resource_key: str, owner_ref: str
) -> None:
    await _post_operation(
        world,
        1,
        IssueLinkState(issue_id, resource_key, "ISSUE-1", "unresolved", owner_ref, True),
        f"r5-link-{issue_id}",
    )


async def _post_operation(
    world: InventoryWorld,
    sequence: int,
    operation: ProjectionOperation,
    stream_id: str,
) -> None:
    command: InventoryCommand = InventoryCommand(
        f"r5-{stream_id}-{sequence}", stream_id, sequence, world.clock.now(), operation
    )
    response: httpx.Response = await world.client.post(
        PREFIX + "/commands",
        headers=world.headers,
        content=encode_inventory_command(command),
    )
    assert response.status_code == 200, response.text
    assert response.json() == "applied"


async def _seed_overdue_asset(world: InventoryWorld, owner_ref: str | None, suffix: str) -> str:
    async with world.harness.sessions.begin() as session:
        fields: tuple[DataFieldRow, ...] = await world.runtime.fields.list(session)
        if not fields:
            await world.runtime.fields.create(
                session,
                "deletion_due",
                "Deletion due",
                None,
                DataFieldType.DATE,
                (),
                False,
                DataFieldRole.DELETION_DUE,
                "member:admin",
            )
            await world.runtime.fields.create(
                session,
                "owner",
                "Owner",
                None,
                DataFieldType.REFERENCE,
                (),
                False,
                DataFieldRole.OWNER,
                "member:admin",
            )
        values: tuple[ValueInput, ...] = (ValueInput("deletion_due", '"2026-10-12"'),)
        if owner_ref is not None:
            values += (ValueInput("owner", f'"{owner_ref}"'),)
        return await world.runtime.registration.register(
            session,
            RegistrationBody(
                f"Retention {suffix}",
                "manual",
                f"https://example.invalid/{suffix}",
                DataAssetKind.FILE,
                1,
                values,
            ),
            "member:admin",
        )


async def _publish_retention(world: InventoryWorld, asset_id: str) -> str:
    await world.runtime.worker.overdue.handle(
        ClaimedTask("test", EngineTaskKind.DATA_OVERDUE_CHECK, "overdue", json_null(), 1, 1, "")
    )
    async with world.harness.sessions() as session:
        rows: tuple[InventoryRetentionOutboxRow, ...] = tuple(
            (await session.scalars(select(InventoryRetentionOutboxRow))).all()
        )
    selected: RetentionCommand | None = next(
        (
            decode_retention_command(row.payload_json)
            for row in rows
            if decode_retention_command(row.payload_json).asset_id == asset_id
        ),
        None,
    )
    assert selected is not None
    return selected.event_id


async def _dispatch(world: InventoryWorld, event_id: str) -> NotificationCommand:
    await world.runtime.worker.retention_tick()
    async with world.harness.sessions() as session:
        row: InventoryNotificationOutboxRow | None = await session.get(
            InventoryNotificationOutboxRow, event_id
        )
    assert row is not None
    command: NotificationCommand = decode_notification_command(row.payload_json)
    await world.runtime.worker.notification_tick()
    return command


async def _resource_key(world: InventoryWorld, asset_id: str) -> str:
    async with world.harness.sessions() as session:
        asset: DataAssetRow | None = await session.get(DataAssetRow, asset_id)
    assert asset is not None
    return asset.resource_key


async def _has_audit(
    world: InventoryWorld, asset_id: str, action: str, details_fragment: str
) -> bool:
    async with world.harness.sessions() as session:
        records: tuple[InventoryAuditRecordRow, ...] = tuple(
            (
                await session.scalars(
                    select(INVENTORY_AUDIT_MODELS.record).where(
                        INVENTORY_AUDIT_MODELS.record.target_id == asset_id,
                        INVENTORY_AUDIT_MODELS.record.action == action,
                    )
                )
            ).all()
        )
    return any(details_fragment in item.details_json for item in records)


def _assert_notification_only(world: InventoryWorld) -> None:
    urls: tuple[str, ...] = tuple(world.network.requests or ())
    assert all(url.startswith(("http://inventory/", "http://notification/")) for url in urls)
    notification_urls: tuple[str, ...] = tuple(
        url for url in urls if url.startswith("http://notification/")
    )
    assert notification_urls
    assert all(url.startswith("http://notification/") for url in notification_urls)

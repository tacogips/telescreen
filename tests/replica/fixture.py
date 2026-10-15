"""Projection-only directory fixture shared by replica behavior tests."""

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.api.common.auth import hash_token
from telescreen.db.models import (
    ApiTokenRow,
    CollectorConfigRow,
    MemberIdentityRow,
    MemberRow,
    OwnershipRuleRow,
    TeamMembershipRow,
    TeamRow,
)
from telescreen.ports.directory_projection import (
    ApiTokenEntity,
    CollectorEntity,
    DirectoryEntity,
    DirectoryEntityKind,
    IdentityEntity,
    MemberEntity,
    OwnershipRuleEntry,
    OwnershipRulesEntity,
    TeamEntity,
)

MEMBER_ID: str = "00000000-0000-0000-0000-000000000001"
TEAM_ID: str = "00000000-0000-0000-0000-000000000002"
TOKEN_ID: str = "00000000-0000-0000-0000-000000000003"
TOKEN_VALUE: str = "replica-test-token"
IDENTITY_KEY: str = "a" * 64


def directory_entities() -> tuple[DirectoryEntity, ...]:
    """Build matching directory entity values without projection publishers."""
    return (
        DirectoryEntity(
            DirectoryEntityKind.MEMBER,
            MEMBER_ID,
            1,
            True,
            MemberEntity(
                MEMBER_ID,
                "alice",
                "Alice Example",
                True,
                "admin",
                "alice@example.test",
                "U12345678",
            ),
        ),
        DirectoryEntity(
            DirectoryEntityKind.TEAM,
            TEAM_ID,
            1,
            True,
            TeamEntity(
                TEAM_ID,
                "responders",
                "Responders",
                MEMBER_ID,
                (MEMBER_ID,),
                "team@example.test",
                "C12345678",
            ),
        ),
        DirectoryEntity(
            DirectoryEntityKind.IDENTITY,
            "00000000-0000-0000-0000-000000000004",
            1,
            True,
            IdentityEntity(
                "00000000-0000-0000-0000-000000000004", MEMBER_ID, "oidc_subject", IDENTITY_KEY
            ),
        ),
        DirectoryEntity(
            DirectoryEntityKind.API_TOKEN,
            TOKEN_ID,
            1,
            True,
            ApiTokenEntity(
                TOKEN_ID, "replica token", hash_token(TOKEN_VALUE), ("read",), MEMBER_ID, False
            ),
        ),
        DirectoryEntity(
            DirectoryEntityKind.OWNERSHIP_RULES,
            "rules",
            1,
            True,
            OwnershipRulesEntity(
                (OwnershipRuleEntry("rule-1", 1, "github", "org/**", TEAM_ID, True),)
            ),
        ),
        DirectoryEntity(
            DirectoryEntityKind.COLLECTOR,
            "00000000-0000-0000-0000-000000000005",
            1,
            True,
            CollectorEntity("00000000-0000-0000-0000-000000000005", "GitHub"),
        ),
    )


async def populate_main_directory(session: AsyncSession) -> None:
    """Seed equivalent main-store ORM rows for adapter parity checks."""
    now: datetime = datetime(2026, 10, 16, tzinfo=UTC)
    session.add(
        MemberRow(
            id=MEMBER_ID,
            handle="alice",
            display_name="Alice Example",
            email="alice@example.test",
            slack_user_id="U12345678",
            active=True,
            created_at=now,
            updated_at=now,
            role="admin",
        )
    )
    session.add(
        TeamRow(
            id=TEAM_ID,
            slug="responders",
            name="Responders",
            email="team@example.test",
            slack_channel="C12345678",
            created_at=now,
            updated_at=now,
            lead_member_id=MEMBER_ID,
        )
    )
    session.add(TeamMembershipRow(team_id=TEAM_ID, member_id=MEMBER_ID))
    session.add(
        MemberIdentityRow(
            id="00000000-0000-0000-0000-000000000004",
            member_id=MEMBER_ID,
            kind="oidc_subject",
            identity_key=IDENTITY_KEY,
            origin="oidc",
            created_at=now,
        )
    )
    session.add(
        ApiTokenRow(
            id=TOKEN_ID,
            label="replica token",
            token_hash=hash_token(TOKEN_VALUE),
            scopes="read",
            member_id=MEMBER_ID,
            created_at=now,
            revoked_at=None,
        )
    )
    session.add(
        OwnershipRuleRow(
            id="rule-1",
            position=1,
            source="github",
            pattern="org/**",
            team_id=TEAM_ID,
            enabled=True,
            created_by="system:test",
            created_at=now,
            updated_at=now,
        )
    )
    session.add(
        CollectorConfigRow(
            id="00000000-0000-0000-0000-000000000005",
            name="GitHub",
            kind="github",
            trigger_mode="both",
            selector_json='{"owner":"org","repos":[],"branches":[],"path_globs":[]}',
            created_at=now,
            updated_at=now,
        )
    )
    await session.flush()

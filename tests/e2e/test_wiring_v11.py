"""End-to-end coverage for v1.1 application composition."""

import json
from dataclasses import dataclass

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from telescreen.api.app import create_app
from telescreen.core.config import FingerprintKeyRef, TelescreenSettings
from telescreen.core.secrets import EnvSource, SecretValue
from telescreen.core.vocabulary import ApiScope, EngineTaskKind, StepType
from telescreen.db.models import FindingRow, IssueRow
from telescreen.directory.tokens import CreatedToken, TokenService
from telescreen.issues.policy.decider import PlacementPolicyDecider
from telescreen.masking.fingerprint import Fingerprinter
from telescreen.wiring import Container, build_container, close_container
from telescreen.wiring_v11 import UtcRiskScorer
from telescreen.workflow.executors.human import HumanTaskExecutor
from telescreen.workflow.step_api import StepExecutor
from tests.support.db import SqliteHarness, make_sqlite_harness
from tests.support.samples import Sample, aws_access_key_id


@dataclass(frozen=True)
class _TestEnv(EnvSource):
    """Resolve only the two test fingerprint keys."""

    def get(self, key: str, /) -> str | None:
        """Resolve a configured fake key reference."""
        return {
            "TELESCREEN_TEST_KEY_V1": "v1-test-fingerprint-key-" + "a" * 20,
            "TELESCREEN_TEST_KEY_V2": "v2-test-fingerprint-key-" + "b" * 20,
        }.get(key)


@pytest.mark.asyncio
async def test_v11_services_and_active_fingerprint_are_wired() -> None:
    """Bind v1.1 operations and retain v1 resource identity during key rotation."""
    database: SqliteHarness = await make_sqlite_harness()
    settings: TelescreenSettings = TelescreenSettings(
        database_url="sqlite+aiosqlite:///:memory:",
        fingerprint_keys=(
            FingerprintKeyRef(1, "env:TELESCREEN_TEST_KEY_V1"),
            FingerprintKeyRef(2, "env:TELESCREEN_TEST_KEY_V2"),
        ),
        fingerprint_active_version=2,
        owner_routing=True,
    )
    env: _TestEnv = _TestEnv()
    outbound: httpx.AsyncClient = httpx.AsyncClient()
    container: Container = build_container(settings, env, outbound, database.sessions)
    try:
        assert isinstance(container.issue_service.decider, PlacementPolicyDecider)
        assert isinstance(container.issue_service.scorer, UtcRiskScorer)
        assert container.issue_service.owners is not None
        assert container.issue_service.owner_routing is True
        provider_names: tuple[str, ...] = tuple(
            type(provider).__name__ for provider in container.worker.scheduler.providers
        )
        assert "IssuePolicyMaintenance" in provider_names
        assert "ExceptionMaintenanceProvider" in provider_names
        assert "CollectorHealthProvider" in provider_names
        assert "PoliceHealthProvider" in provider_names
        registered_kinds: tuple[EngineTaskKind, ...] = tuple(
            kind for kind, _handler in container.worker.registry.handlers
        )
        assert EngineTaskKind.VERIFY_RESCAN in registered_kinds
        assert EngineTaskKind.POLICY_REEVALUATE in registered_kinds
        assert EngineTaskKind.OWNER_DIGEST in registered_kinds
        human_executor: StepExecutor = container.run_engine.executors.get(StepType.HUMAN_TASK)
        assert isinstance(human_executor, HumanTaskExecutor)
        assert human_executor.issues is container.issue_service

        async with database.sessions.begin() as session:
            token: CreatedToken = await TokenService(container.audit, container.clock).create(
                session,
                "v11-ingest",
                frozenset((ApiScope.ADMIN, ApiScope.INGEST)),
                None,
            )
        app: FastAPI = create_app(container)
        transport: ASGITransport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            create_collector: httpx.Response = await client.post(
                "/api/collectors",
                json={
                    "name": "v11-wiring",
                    "kind": "custom",
                    "trigger_mode": "event",
                    "cron": None,
                    "sweep_cron": None,
                    "selector_json": '{"paths":["**"]}',
                    "credential_ref": None,
                    "enabled": True,
                },
                headers={"Authorization": f"Bearer {token.token}"},
            )
        assert create_collector.status_code == 200, create_collector.text
        sample: Sample = aws_access_key_id()
        payload: str = json.dumps(
            {
                "source": "custom:v11-wiring",
                "resources": [
                    {
                        "locator": "repo/settings.py",
                        "segments": [{"locator": "L1", "text": sample.value}],
                    }
                ],
            }
        )
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response: httpx.Response = await client.post(
                "/api/ingest/v1/scan",
                content=payload,
                headers={"Authorization": f"Bearer {token.token}"},
            )
        assert response.status_code == 200, response.text
        async with database.sessions() as session:
            finding: FindingRow | None = await session.scalar(select(FindingRow))
            issue: IssueRow | None = await session.scalar(select(IssueRow))
        assert finding is not None and finding.fingerprint_key_version == 2
        assert issue is not None
        v1_fingerprinter: Fingerprinter = Fingerprinter.from_secret(
            SecretValue("v1-test-fingerprint-key-" + "a" * 20)
        )
        assert issue.resource_key == v1_fingerprinter.resource_key(
            "custom:v11-wiring", "repo/settings.py"
        )
    finally:
        await close_container(container)
        await database.close()
        await outbound.aclose()

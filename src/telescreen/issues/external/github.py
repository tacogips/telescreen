"""DLP-gated GitHub issue creation from allow-listed masked fields."""

from dataclasses import dataclass

import httpx
import pydantic
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.clock import Clock, ensure_utc
from telescreen.core.config import EnvSource
from telescreen.core.errors import ConfigError, TelescreenError
from telescreen.core.ids import new_id
from telescreen.core.json import JsonValue, json_object, json_str
from telescreen.core.secrets import resolve_secret
from telescreen.core.vocabulary import ActorKind, ErrorCode
from telescreen.db.models import ExternalIssueLinkRow, FindingRow, IssueRow
from telescreen.masking.redact import Redactor
from telescreen.ports.audit import AuditEntry, AuditLog
from telescreen.ports.notifications import GuardVerdict, OutboundContentGuard


@pydantic.dataclasses.dataclass(frozen=True)
class GithubIssueResponse:
    """Validated subset of the GitHub create issue response."""

    number: int
    html_url: str


@dataclass(frozen=True)
class GithubIssueCreator:
    """Create GitHub issues using a dedicated secret and allow-listed repositories."""

    transport: httpx.AsyncBaseTransport | None
    api_url: str
    token_ref: str | None
    repos: tuple[str, ...]
    env: EnvSource
    guard: OutboundContentGuard
    redactor: Redactor
    audit: AuditLog
    clock: Clock
    public_base_url: str | None = None

    async def create(
        self, session: AsyncSession, issue: IssueRow, repo: str, title: str | None, actor: str
    ) -> ExternalIssueLinkRow:
        """Create an issue only after allow-list, masking and DLP checks pass."""
        if self.token_ref is None or not self.repos:
            raise TelescreenError(ErrorCode.CONFIG_ERROR, "integration_unconfigured")
        if repo not in self.repos:
            raise TelescreenError(ErrorCode.VALIDATION_ERROR, "repository is not allowed")
        latest: FindingRow | None = await session.scalar(
            select(FindingRow)
            .where(FindingRow.issue_id == issue.id)
            .order_by(FindingRow.observed_at.desc(), FindingRow.id.desc())
        )
        selected_title: str = title or f"TELE-{issue.number}: {issue.display_title}"
        safe_title: str = self.redactor.redact(selected_title).text[:200]
        body: str = build_body(issue, latest, self.public_base_url)
        safe_body: str = self.redactor.redact(body).text
        if (
            not safe_title.strip()
            or self.redactor.has_hits(safe_title)
            or self.redactor.has_hits(safe_body)
        ):
            raise TelescreenError(ErrorCode.RAW_VALUE_DETECTED, "external issue content rejected")
        title_verdict: GuardVerdict = await self.guard.inspect(safe_title)
        body_verdict: GuardVerdict = await self.guard.inspect(safe_body)
        if not title_verdict.allowed or not body_verdict.allowed:
            await self._audit(
                session, actor, "external_issue.blocked", issue.id, "External issue blocked", repo
            )
            raise TelescreenError(ErrorCode.VALIDATION_ERROR, "dlp_blocked")
        try:
            token: str = resolve_secret(self.token_ref, self.env).value
        except ConfigError:
            raise TelescreenError(ErrorCode.CONFIG_ERROR, "integration_unconfigured") from None
        try:
            async with httpx.AsyncClient(
                transport=self.transport, timeout=10.0, base_url=self.api_url.rstrip("/")
            ) as client:
                response: httpx.Response = await client.post(
                    f"/repos/{repo}/issues",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/vnd.github+json",
                    },
                    json={"title": safe_title, "body": safe_body},
                )
        except httpx.HTTPError:
            raise TelescreenError(
                ErrorCode.PROVIDER_ERROR, "GitHub issue creation failed"
            ) from None
        if response.status_code < 200 or response.status_code >= 300:
            raise TelescreenError(ErrorCode.PROVIDER_ERROR, "GitHub issue creation failed")
        try:
            result: GithubIssueResponse = TypeAdapter(GithubIssueResponse).validate_json(
                response.content
            )
        except (ValidationError, ValueError):
            raise TelescreenError(
                ErrorCode.PROVIDER_ERROR, "GitHub issue creation failed"
            ) from None
        external_ref: str = f"{repo}#{result.number}"
        row: ExternalIssueLinkRow = ExternalIssueLinkRow(
            id=new_id(),
            issue_id=issue.id,
            kind="github",
            url=result.html_url,
            title=safe_title,
            external_ref=external_ref,
            created_by=actor,
            created_at=ensure_utc(self.clock.now()),
        )
        session.add(row)
        await self._audit(
            session,
            actor,
            "external_issue.created",
            issue.id,
            "GitHub issue created",
            repo,
            result.number,
        )
        return row

    async def _audit(
        self,
        session: AsyncSession,
        actor: str,
        action: str,
        target_id: str,
        summary: str,
        repo: str,
        number: int | None = None,
    ) -> None:
        """Record only safe provider identifiers in the audit log."""
        kind: ActorKind = ActorKind.MEMBER if actor.startswith("member:") else ActorKind.TOKEN
        members: list[tuple[str, JsonValue]] = [("repo", json_str(repo))]
        if number is not None:
            members.append(("number", json_str(str(number))))
        await self.audit.append(
            session,
            AuditEntry(
                kind,
                actor,
                action,
                "external_issue",
                target_id,
                summary,
                json_object(tuple(members)),
            ),
        )


def build_body(issue: IssueRow, latest: FindingRow | None, public_base_url: str | None) -> str:
    """Build a GitHub body exclusively from persisted masked allow-listed fields."""
    lines: list[str] = [
        f"Telescreen issue TELE-{issue.number}",
        f"Title: {issue.display_title}",
        f"Rule class: {issue.rule_class}",
        f"Severity: {issue.severity}",
        f"Priority: {issue.priority}",
        f"Exposure: {issue.exposure_visibility}",
        f"Location: {latest.display_locator if latest is not None else issue.display_locator}",
        f"Latest finding: {latest.masked_snippet if latest is not None else 'No finding excerpt'}",
    ]
    if public_base_url is not None:
        lines.append(f"Telescreen: {public_base_url.rstrip('/')}/issues/{issue.id}")
    return "\n".join(lines)

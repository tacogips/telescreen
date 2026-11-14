"""FastMCP server exposing the shared, redacted assistant tools."""

from datetime import datetime

from mcp.server.fastmcp import FastMCP

from telescreen.assistant.tools import (
    AssistantTools,
    AuditList,
    GroupBy,
    IncidentCounts,
    IssueDetail,
    IssueList,
    RunList,
)
from telescreen.core.errors import TelescreenError
from telescreen.core.vocabulary import ErrorCode, IssueStatus, RunStatus


def build_mcp(tools: AssistantTools) -> FastMCP[None]:
    """Build the six-tool MCP server used by stdio and tests."""
    server: FastMCP[None] = FastMCP("Telescreen Assistant")

    @server.tool(name="search_issues")
    async def search_issues(query: str, status: str | None = None, limit: int = 20) -> str:
        """Search issues by masked title or resource locator."""
        issue_status: IssueStatus | None = _issue_status(status)
        result: IssueList = await tools.search_issues(query, issue_status, limit)
        return tools.render_json(result)

    @server.tool(name="get_issue")
    async def get_issue(issue_id: str) -> str:
        """Get one issue with recent masked events and lifecycle history."""
        result: IssueDetail | None = await tools.get_issue(issue_id)
        return tools.render_json(result) if result is not None else "null"

    @server.tool(name="count_incidents")
    async def count_incidents(start: str, end: str, group_by: str) -> str:
        """Count incidents in a half-open ISO-8601 time interval."""
        start_at: datetime = _parse_datetime(start)
        end_at: datetime = _parse_datetime(end)
        grouping: GroupBy = _group_by(group_by)
        result: IncidentCounts = await tools.count_incidents(start_at, end_at, grouping)
        return tools.render_json(result)

    @server.tool(name="incident_report")
    async def incident_report(start: str, end: str) -> str:
        """Create a masked markdown report for a half-open time interval."""
        return await tools.incident_report(_parse_datetime(start), _parse_datetime(end))

    @server.tool(name="list_workflow_runs")
    async def list_workflow_runs(status: str | None = None, limit: int = 20) -> str:
        """List recent workflow runs, optionally filtered by status."""
        run_status: RunStatus | None = _run_status(status)
        result: RunList = await tools.list_workflow_runs(run_status, limit)
        return tools.render_json(result)

    @server.tool(name="audit_search")
    async def audit_search(query: str, limit: int = 20) -> str:
        """Search masked audit actions and summaries."""
        result: AuditList = await tools.audit_search(query, limit)
        return tools.render_json(result)

    return server


def run_stdio(tools: AssistantTools) -> None:
    """Run the Telescreen assistant MCP server over stdio."""
    build_mcp(tools).run(transport="stdio")


def _parse_datetime(value: str) -> datetime:
    """Parse an ISO-8601 timestamp and hide malformed input from errors."""
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid timestamp") from None


def _issue_status(value: str | None) -> IssueStatus | None:
    """Parse an optional issue status without echoing invalid input."""
    try:
        return IssueStatus(value) if value is not None else None
    except ValueError:
        raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid issue status") from None


def _run_status(value: str | None) -> RunStatus | None:
    """Parse an optional workflow run status without echoing invalid input."""
    try:
        return RunStatus(value) if value is not None else None
    except ValueError:
        raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid run status") from None


def _group_by(value: str) -> GroupBy:
    """Parse an approved incident grouping field."""
    try:
        return GroupBy(value)
    except ValueError:
        raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid incident grouping") from None

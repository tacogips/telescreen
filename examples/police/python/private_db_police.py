"""Example pull-mode Police that checks a private SQLite database locally."""

from __future__ import annotations

import asyncio
import os
import re
import sqlite3

import httpx

from telescreen_police_sdk import _json
from telescreen_police_sdk.client import BureauClient
from telescreen_police_sdk.masking import external_fingerprint, mask_value
from telescreen_police_sdk.models import (
    Manifest,
    MaskedFinding,
    Mode,
    Outcome,
    Registration,
    ResultEnvelope,
    ResultError,
    ResultStatus,
    RuleClass,
    TaskEnvelope,
)
from telescreen_police_sdk.worker import PullWorker

_IDENTIFIER: re.Pattern[str] = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_EMAIL: re.Pattern[str] = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")


def _input_string(value: _json.Json, name: str) -> str | None:
    """Read one string from a closed JSON object."""
    item: _json.Json | None = value.get(name)
    return item.as_str() if item else None


async def verify_task(
    task: TaskEnvelope, database_path: str, fingerprint_key: bytes
) -> ResultEnvelope:
    """Inspect a configured private table and return only masked findings."""
    issue: _json.Json | None = task.input.get("issue")
    locator: str | None = _input_string(issue, "display_locator") if issue else None
    if locator is None or not locator.startswith("sqlite://"):
        return _failed(task.task_id, "invalid_locator")
    names: tuple[str, ...] = tuple(locator.removeprefix("sqlite://").split("/", maxsplit=1))
    if len(names) != 2 or any(_IDENTIFIER.fullmatch(name) is None for name in names):
        return _failed(task.task_id, "invalid_locator")
    table: str = names[0]
    column: str = names[1]
    findings: list[MaskedFinding] = []
    try:
        with sqlite3.connect(database_path) as connection:
            rows: list[tuple[str | bytes | int | float | None, ...]] = connection.execute(
                f'SELECT "{column}" FROM "{table}"'
            ).fetchall()
        for row in rows:
            cell: str | bytes | int | float | None = row[0]
            value: str = (
                cell.decode("utf-8", errors="replace")
                if isinstance(cell, bytes)
                else str(cell or "")
            )
            match: re.Match[str] | None = _EMAIL.search(value)
            if match is not None:
                detected: str = match.group()
                findings.append(
                    MaskedFinding(
                        locator=locator,
                        rule_id="email",
                        rule_class=RuleClass.EMAIL,
                        masked_snippet=mask_value(detected),
                        location=column,
                        confidence=1.0,
                        external_fingerprint=external_fingerprint(detected, fingerprint_key),
                    )
                )
    except sqlite3.Error:
        return _failed(task.task_id, "database_query_failed")
    outcome: Outcome = Outcome.FAILED if findings else Outcome.PASSED
    summary: str = "Potential sensitive data found" if findings else "No sensitive data found"
    return ResultEnvelope(
        task.task_id,
        ResultStatus.COMPLETED,
        outcome=outcome,
        summary=summary,
        findings=tuple(findings),
    )


def _failed(task_id: str, code: str) -> ResultEnvelope:
    """Build a static failure result."""
    return ResultEnvelope(
        task_id, ResultStatus.FAILED, error=ResultError(code, "Police check failed")
    )


async def main() -> None:
    """Enroll and run the example Police worker."""
    base_url: str = os.environ["TELESCREEN_URL"]
    enrollment_token: str = os.environ["TELESCREEN_POLICE_ENROLLMENT_TOKEN"]
    database_path: str = os.environ["PRIVATE_DB_PATH"]
    fingerprint_key: bytes = os.environ["TELESCREEN_FINGERPRINT_KEY"].encode()
    async with httpx.AsyncClient(timeout=35.0) as http:
        client: BureauClient = BureauClient(base_url, http)
        registration: Registration = await client.register(
            Manifest("private-db-police", "1.0", Mode.PULL, ("verify_remediation",), (), 4),
            enrollment_token,
        )
        if registration.worker_token is None:
            raise RuntimeError("Bureau did not return a worker token")

        async def handler(task: TaskEnvelope) -> ResultEnvelope:
            return await verify_task(task, database_path, fingerprint_key)

        worker: PullWorker = PullWorker(
            client,
            registration.worker_id,
            registration.worker_token,
            (("verify_remediation", handler),),
            4,
            float(registration.heartbeat_interval_seconds),
            registration.max_wait_seconds,
        )
        await worker.run(asyncio.Event())


if __name__ == "__main__":
    asyncio.run(main())

"""Exercise raw-value leak assertions across all three independent processes."""

import sqlite3
from pathlib import Path

import httpx
import pytest

from telescreen.core.vocabulary import NotificationEvent
from telescreen.db.ownership import Store
from telescreen.ports.notification_commands import MemberSendOperation, decode_notification_command
from tests.leak.flows_split import (
    assert_split_sinks_clean,
    assert_store_tables_clean,
    assert_texts_clean,
)
from tests.split.support import (
    ServiceStack,
    SmtpRecorder,
    run_cli,
    scalar_int,
    seed_issue,
    seed_member,
    wait_for,
)
from tests.support.samples import Sample, all_samples


@pytest.mark.asyncio
async def test_split_process_leak_matrix(tmp_path: Path) -> None:
    """Scan stores, logs, API bodies, SMTP and service audit exports for raw samples."""
    stack: ServiceStack = ServiceStack.create(tmp_path)
    smtp: SmtpRecorder = SmtpRecorder(stack.smtp_port)
    samples: tuple[Sample, ...] = all_samples()
    bodies: list[str] = []
    try:
        async with httpx.AsyncClient(timeout=3, trust_env=False) as http:
            _ = await stack.start_all(http)
            _ = await seed_member(stack, http, "alice", bodies)
            issue_id: str = await seed_issue(stack, http, samples[0].value, bodies)
            comment: httpx.Response = await http.post(
                f"http://127.0.0.1:{stack.api_port}/api/issues/{issue_id}/comments",
                headers={"Authorization": f"Bearer {stack.admin_token}"},
                json={"body": f"Review @alice with {samples[2].value}"},
            )
            bodies.append(comment.text)
            assert comment.status_code == 201, comment.text
            created: httpx.Response = await http.post(
                f"http://127.0.0.1:{stack.api_port}/api/workflows",
                headers={"Authorization": f"Bearer {stack.admin_token}"},
                json={
                    "source_format": "yaml",
                    "source_text": _inline_workflow(),
                },
            )
            bodies.append(created.text)
            assert created.status_code == 201, created.text
            activated: httpx.Response = await http.post(
                f"http://127.0.0.1:{stack.api_port}/api/workflows/p08-leak-inline/activate",
                headers={"Authorization": f"Bearer {stack.admin_token}"},
                json={"version": 1},
            )
            bodies.append(activated.text)
            assert activated.status_code == 200, activated.text
            run: httpx.Response = await http.post(
                f"http://127.0.0.1:{stack.api_port}/api/workflows/p08-leak-inline/runs",
                headers={"Authorization": f"Bearer {stack.admin_token}"},
                json={"issue_id": issue_id},
            )
            bodies.append(run.text)
            assert run.status_code == 201, run.text
            run_id: str = run.json()["run_id"]
            asset: httpx.Response = await http.post(
                f"http://127.0.0.1:{stack.api_port}/api/data/assets",
                headers={"Authorization": f"Bearer {stack.admin_token}"},
                json={
                    "name": "P08 rejected locator probe",
                    "source": "manual",
                    "locator": f"p08/{samples[4].value}",
                    "kind": "file",
                },
            )
            bodies.append(asset.text)
            assert asset.status_code == 422, asset.text
            field: httpx.Response = await http.post(
                f"http://127.0.0.1:{stack.api_port}/api/data/fields",
                headers={"Authorization": f"Bearer {stack.admin_token}"},
                json={
                    "key": "p08_context_probe",
                    "label": "P08 context probe",
                    "field_type": "long_text",
                    "role": None,
                },
            )
            bodies.append(field.text)
            assert field.status_code == 201, field.text
            context_probe: httpx.Response = await http.post(
                f"http://127.0.0.1:{stack.api_port}/api/data/assets",
                headers={"Authorization": f"Bearer {stack.admin_token}"},
                json={
                    "name": "P08 context probe",
                    "source": "manual",
                    "locator": "p08/context-probe",
                    "kind": "file",
                    "values": [
                        {
                            "key": "p08_context_probe",
                            "value_json": _json_string(samples[5].value),
                        }
                    ],
                },
            )
            bodies.append(context_probe.text)
            assert context_probe.status_code in (201, 422), context_probe.text
            wait_for(
                lambda: (
                    scalar_int(
                        stack.main_path,
                        "SELECT count(*) FROM workflow_runs WHERE id=? "
                        "AND status IN ('succeeded','failed')",
                        (run_id,),
                    )
                    == 1
                ),
                90,
                "workflow notify run did not finish",
            )
            wait_for(
                lambda: (
                    scalar_int(
                        stack.main_path,
                        "SELECT count(*) FROM notification_outbox WHERE delivered_at IS NULL",
                    )
                    == 0
                    and scalar_int(
                        stack.main_path,
                        "SELECT count(*) FROM inventory_outbox WHERE delivered_at IS NULL",
                    )
                    == 0
                    and scalar_int(stack.main_path, "SELECT count(*) FROM projection_changes") == 0
                    and scalar_int(
                        stack.inventory_path,
                        "SELECT count(*) FROM inventory_notification_outbox "
                        "WHERE delivered_at IS NULL",
                    )
                    == 0
                ),
                90,
                "producer outboxes or projection relay did not drain",
            )
            wait_for(lambda: len(smtp.messages) >= 1, 90, "SMTP did not receive workflow message")
            inventory_export: str = run_cli(
                ("telescreen.inventory_service", "audit", "export"), stack.inventory_env
            )
            notification_export: str = run_cli(
                ("telescreen.notification_service", "audit", "export"),
                stack.notification_env,
            )
            assert inventory_export.strip()
            assert notification_export.strip()
            assert_store_tables_clean(
                samples,
                (
                    (Store.MAIN, stack.main_path),
                    (Store.INVENTORY, stack.inventory_path),
                    (Store.NOTIFICATION, stack.notification_path),
                ),
            )
            log_paths: tuple[Path, ...] = tuple(
                stack.log_dir / f"{name}.log"
                for name in ("telescreen", "worker", "inventory", "notification")
            )
            assert_split_sinks_clean(
                samples,
                (stack.main_path, stack.inventory_path, stack.notification_path),
                log_paths,
                tuple(bodies),
            )
            assert_texts_clean(samples, smtp.messages + (inventory_export, notification_export))
            notification_rows: tuple[tuple[str, ...], ...] = _notification_outboxes(stack.main_path)
            assert any(
                isinstance(operation, MemberSendOperation)
                and operation.redaction.subject
                + operation.redaction.body
                + sum(hit.count for hit in operation.redaction.context)
                > 0
                for row in notification_rows
                for operation in decode_notification_command(row[1]).operations
            )
            assert (
                scalar_int(
                    stack.notification_path,
                    "SELECT count(*) FROM notifications WHERE recipient_ref=? AND event_kind=?",
                    ("member:alice", NotificationEvent.ISSUE_MENTIONED.value),
                )
                >= 1
            )
            assert scalar_int(stack.main_path, "SELECT count(*) FROM audit_records") > 0
    finally:
        stack.stop_all()
        smtp.close()


def test_split_leak_matrix_fails_when_owned_table_is_missing(tmp_path: Path) -> None:
    """Require every table declared by ownership metadata to exist in its store."""
    path: Path = tmp_path / "empty.db"
    connection: sqlite3.Connection = sqlite3.connect(path)
    connection.close()
    with pytest.raises(AssertionError, match="store is missing declared table"):
        assert_store_tables_clean(all_samples(), ((Store.MAIN, path),))


def _inline_workflow() -> str:
    """Build an inline notification flow whose rendered issue title carries a sample."""
    return """apiVersion: telescreen.workflow/v2
kind: Workflow
name: p08-leak-inline
start: alert
notification_sets:
  - name: p08-leak-set
    targets:
      - recipients: [email:secops@example.invalid]
        channels: [email]
steps:
  - id: alert
    type: notify
    notification_set: p08-leak-set
    subject: P08 leak matrix
    body: Review masked issue {{ issue.title }}.
    next: done
  - id: done
    type: end
    result: succeeded
"""


def _json_string(value: str) -> str:
    """Return a JSON string literal without introducing an untyped mapping."""
    import json

    return json.dumps(value)


def _notification_outboxes(path: Path) -> tuple[tuple[str, ...], ...]:
    """Read notification producer outbox rows with a read-only SQLite connection."""
    import sqlite3

    with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5) as connection:
        rows: tuple[tuple[str, ...], ...] = tuple(
            tuple(str(value) for value in row)
            for row in connection.execute(
                "SELECT id, payload_json, recorded_at, delivered_at FROM notification_outbox"
            ).fetchall()
        )
    return rows

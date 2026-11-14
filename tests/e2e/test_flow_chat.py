"""End-to-end Assistant chat flow against the real assembled services."""

import json
from pathlib import Path

import httpx
import pytest

from tests.e2e.harness import E2EWorld, OutboundRecorder, admin_post, bearer, make_world
from tests.support.samples import Sample, assert_no_leaks, aws_access_key_id


@pytest.mark.asyncio
async def test_chat_runs_count_tool_and_redacts_model_requests(tmp_path: Path) -> None:
    """Prove chat tools query persisted issues and raw transcript values stay local."""
    sample: Sample = aws_access_key_id()
    model_turn: int = 0

    def anthropic(request: httpx.Request) -> httpx.Response:
        nonlocal model_turn
        model_turn += 1
        completion: str = (
            '{"action":"tool","tool":"count_incidents","arguments":'
            '{"start":"2026-10-01T00:00:00Z","end":"2026-10-02T00:00:00Z",'
            '"group_by":"source"}}'
            if model_turn == 1
            else '{"action":"answer","answer":"There are 2 incidents."}'
        )
        return httpx.Response(
            200,
            request=request,
            json={
                "id": "msg_e2e",
                "type": "message",
                "role": "assistant",
                "model": "claude-sonnet-5-5",
                "content": [{"type": "text", "text": completion}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        )

    outbound: OutboundRecorder = OutboundRecorder()
    outbound.handlers.append(("api.anthropic.com", anthropic))
    world: E2EWorld = await make_world(
        tmp_path,
        (
            ("TELESCREEN_ANTHROPIC_API_KEY", "env:E2E_ANTHROPIC"),
            ("E2E_ANTHROPIC", "e2e-" + "a" * 40),
        ),
        outbound,
    )
    try:
        await admin_post(
            world,
            "/api/collectors",
            json.dumps(
                {
                    "name": "e2e-chat-source",
                    "kind": "custom",
                    "trigger_mode": "event",
                    "cron": None,
                    "sweep_cron": None,
                    "selector_json": "{}",
                    "credential_ref": None,
                    "enabled": True,
                }
            ),
        )
        for locator in ("repo/a.txt", "repo/b.txt"):
            scan: httpx.Response = await world.client.post(
                "/api/ingest/v1/scan",
                content=json.dumps(
                    {
                        "source": "custom:e2e-chat-source",
                        "resources": [
                            {
                                "locator": locator,
                                "complete": True,
                                "segments": [
                                    {"locator": "L1", "text": f"access_key={sample.value}"}
                                ],
                            }
                        ],
                    }
                ),
                headers=bearer(world.admin_token) + (("Content-Type", "application/json"),),
            )
            world.responses.append(scan.text)
            assert scan.status_code == 200, scan.text

        chat: httpx.Response = await world.client.post(
            "/api/chat",
            content=json.dumps(
                {
                    "messages": [
                        {
                            "role": "user",
                            "content": (
                                f"How many incidents this month? access_key={sample.value}"
                            ),
                        }
                    ]
                }
            ),
            headers=bearer(world.admin_token) + (("Content-Type", "application/json"),),
        )
        world.responses.append(chat.text)
        assert chat.status_code == 200, chat.text
        assert chat.json()["answer"] == "There are 2 incidents."
        assert model_turn == 2
        provider_bodies: tuple[str, ...] = tuple(
            request.body.decode("utf-8")
            for request in outbound.requests
            if request.host == "api.anthropic.com"
        )
        assert len(provider_bodies) == 2
        second_transcript: str = json.loads(provider_bodies[1])["messages"][0]["content"][0]["text"]
        assert r"\"total\": 2" in second_transcript
        for body in provider_bodies:
            assert_no_leaks(body, "Anthropic request")
        for response_text in world.responses:
            assert_no_leaks(response_text, "chat API response")
        assert not outbound.failures
    finally:
        await world.close()

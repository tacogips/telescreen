"""Check the Slack CLI project that registers the Telescreen notification app."""

import subprocess
from pathlib import Path

from telescreen.core.json import JsonValue, parse_json

_APP: Path = Path(__file__).resolve().parents[2] / "examples" / "slack-app"


def _manifest() -> JsonValue:
    """Parse the committed app manifest."""
    return parse_json((_APP / "manifest.json").read_text(encoding="utf-8"))


def _strings(value: JsonValue | None) -> tuple[str, ...]:
    """Return the string items of a JSON array."""
    items: tuple[JsonValue, ...] = (value.as_list() or ()) if value is not None else ()
    return tuple(text for item in items if (text := item.as_str()) is not None)


def _path(value: JsonValue, *names: str) -> JsonValue | None:
    """Walk nested object members."""
    current: JsonValue | None = value
    for name in names:
        current = current.get(name) if current is not None else None
    return current


def test_manifest_requests_only_notification_scopes() -> None:
    """The bot can post into channels and nothing else: no history, user or file scopes."""
    scopes: tuple[str, ...] = _strings(_path(_manifest(), "oauth_config", "scopes", "bot"))
    assert scopes == ("chat:write", "chat:write.public")
    assert _path(_manifest(), "oauth_config", "scopes", "user") is None


def test_manifest_has_a_bot_user_without_inbound_endpoints() -> None:
    """Notifications are outbound only, so no event, interactivity or socket endpoints exist."""
    manifest: JsonValue = _manifest()
    display_name: JsonValue | None = _path(manifest, "features", "bot_user", "display_name")
    assert display_name is not None and display_name.as_str() == "telescreen"
    assert _path(manifest, "settings", "event_subscriptions") is None
    assert _path(manifest, "settings", "interactivity") is None
    socket_mode: JsonValue | None = _path(manifest, "settings", "socket_mode_enabled")
    assert socket_mode is not None and socket_mode == parse_json("false")


def test_get_manifest_hook_prints_the_manifest_and_ignores_cli_flags() -> None:
    """The Slack CLI runs the hook with extra flags such as --source; output stays the manifest."""
    hooks: JsonValue = parse_json((_APP / ".slack" / "hooks.json").read_text(encoding="utf-8"))
    command: JsonValue | None = _path(hooks, "hooks", "get-manifest")
    assert command is not None and command.as_str() == "sh .slack/get-manifest.sh"
    result: subprocess.CompletedProcess[str] = subprocess.run(
        ["sh", ".slack/get-manifest.sh", f"--source={_APP}"],
        cwd=_APP,
        capture_output=True,
        text=True,
        check=True,
    )
    assert parse_json(result.stdout) == _manifest()

---
name: slack-app-setup
description: Use when registering, updating or connecting the Telescreen Slack app (notification bot) in a Slack workspace with the Slack CLI from mise, inviting the bot into a channel, and wiring and verifying its token in Telescreen.
allowed-tools: Bash, Read, Write, Edit
---

# Slack App Setup Skill

Register the Telescreen notification bot in a Slack workspace from the committed
manifest, put the bot into the target channel, and prove delivery. The bot posts masked
alerts into channels; it never carries raw secret values.

## When to Apply

- A workspace needs the Telescreen app (first install, a new workspace, or a manifest change)
- Notifications must go to a Slack channel and the bot is not a member yet
- The bot token or signing secret must be (re)wired into Telescreen

## Files

| Path | Purpose |
|------|---------|
| `examples/slack-app/manifest.json` | App manifest: name `Telescreen`, bot `telescreen`, scopes `chat:write`, `chat:write.public` only |
| `examples/slack-app/.slack/hooks.json` | Slack CLI hooks; `get-manifest` runs `.slack/get-manifest.sh` |
| `examples/slack-app/.slack/config.json` | `manifest.source: local` (the repo manifest is the source of truth) |
| `examples/slack-app/.slack/apps.json` | Written by the CLI per installation (app and team id); git-ignored |
| `tests/examples/test_slack_app.py` | Guards the scopes and the hook output |
| `mise.toml` tasks `slack-login`, `slack-app-validate`, `slack-app-install`, `slack-app-settings` | Wrappers; the CLI is pinned as `aqua:slackapi/slack-cli` |

## Rules

- Secrets never enter the repository, logs, chat output or commit messages. Store the bot
  token and signing secret only in a file outside the repo with mode 600 (for example
  `~/.config/telescreen/slack-<workspace>.env`) and print values as `<redacted>`.
- Do not widen scopes casually. Notifications need only `chat:write` (plus
  `chat:write.public` to post to a public channel without an invite). Add history, file or
  user scopes only for the Slack collector, and update `tests/examples/test_slack_app.py`
  in the same change.
- Telescreen posts to channels: use `slack_channel:<id>` with a `C` or `G` channel id.
  User (`U`/`W`) and DM (`D`) ids are rejected by the DSL and by the resolver.
- Work only in the workspace the user named. Confirm before installing into a production
  workspace; a test workspace may be used freely when the user said so.

## Procedure

Run commands from the repository root unless noted. `SLACK_TEAM` is the team id (`T...`)
or workspace name.

### 1. Install the CLI

```bash
mise install          # installs aqua:slackapi/slack-cli with the other tools
slack version
```

`mise exec -C <dir>` changes the working directory to `<dir>`; to run the CLI inside
`examples/slack-app`, `cd` there or use `mise run slack-*` (those tasks set `dir`).

### 2. Log the CLI in

Interactive (a human at a terminal): `mise run slack-login`, run the printed
`/slackauthticket ...` command in any Slack channel or DM of the target workspace,
approve the modal, and type the challenge code back into the terminal.

Agent-driven (no TTY):

1. `slack auth login --no-prompt` prints `/slackauthticket <ticket>`. Keep the ticket in a
   scratch file; do not echo it into the conversation.
2. Have someone run that slash command in the Slack client. With Codex computer use:
   open the user's own DM in the target workspace, type the command, press Enter, click
   Confirm in "Slack CLI Authentication", and write the shown challenge code to a scratch
   file. Run `codex exec ... < /dev/null`; without the redirect it waits on stdin forever.
3. `slack auth login --no-prompt --ticket <ticket> --challenge <code>`
4. `slack auth list` shows the team. Credentials live in `~/.slack/credentials.json`.
   Delete the scratch ticket and challenge files.

### 3. Create or update and install the app

```bash
SLACK_TEAM=T0123456789 mise run slack-app-validate
SLACK_TEAM=T0123456789 mise run slack-app-install
```

The first install creates the app from the manifest and installs it (the CLI user must
be allowed to install apps; otherwise an admin approval request is created, see
`slack app request`). The CLI writes `examples/slack-app/.slack/apps.json`; later runs
update the same app. If an update fails with `app_manifest_update_error`, run
`slack manifest diff --app deployed --team $SLACK_TEAM` in `examples/slack-app`: Slack adds
default settings, and the manifest must list them (it already sets
`app_level_token_rotation_enabled` and `is_mcp_enabled` to `false`).

### 4. Collect the bot token and signing secret

The CLI does not reveal the bot token. Open the app settings
(`SLACK_TEAM=... mise run slack-app-settings`, or
`https://api.slack.com/apps/<app id>/oauth`) and copy:

- OAuth & Permissions > "Bot User OAuth Token" (`xoxb-...`) into `SLACK_BOT_TOKEN=`
- Basic Information > "Signing Secret" into `SLACK_SIGNING_SECRET=`

Write both to the mode-600 env file outside the repo. With Codex computer use, instruct it
to write the file itself and never print the values. Afterwards remove any scratch log
that captured them (check with `grep -l xoxb <scratch dir>`).

Check the token without printing it:

```bash
set -a; . ~/.config/telescreen/slack-<workspace>.env; set +a
curl -s -D - -o /dev/null -H "Authorization: Bearer $SLACK_BOT_TOKEN" \
  https://slack.com/api/auth.test | grep -i x-oauth-scopes
```

### 5. Put the bot into the channel

In the Slack client, open the target channel and run `/invite @telescreen` (pick the
`telescreen` bot from the autocomplete). The channel's "Add apps" search may not list a
newly created app; `/invite` works. A private channel always needs the invite; a public
channel works without it through `chat:write.public`, but inviting is still preferred.
Read the channel id from channel details (About tab, bottom). Confirm membership with
`conversations.info` only if the token has `channels:read`; otherwise check "Agents &
apps" in channel details.

### 6. Wire Telescreen

```bash
export SLACK_BOT_TOKEN=...            # from the env file, never inline in shell history
export TELESCREEN_SLACK_BOT_TOKEN=env:SLACK_BOT_TOKEN
export TELESCREEN_SLACK_SIGNING_SECRET=env:SLACK_SIGNING_SECRET   # only for Slack webhooks
```

Workflow notification sets target the channel:

```yaml
notification_sets:
  - name: secops-alerts
    targets:
      - recipients: [slack_channel:C0123ABCDE]
        channels: [slack]
```

### 7. Verify

```bash
uv run telescreen notify test --channel slack --to slack_channel:<channel id>
TELESCREEN_TEST_SLACK_BOT_TOKEN=$SLACK_BOT_TOKEN TELESCREEN_TEST_SLACK_CHANNEL=<channel id> \
  uv run pytest -m live tests/live/test_slack_delivery.py
```

`notify test` prints `"status":"sent"` on success. The token has no history scope, so read
back visually (Slack client or Codex computer use) when proof of arrival is required.
`slack_channel:<U...>` must come back `"status":"skipped"` with `recipient_unknown`.

## Slack collector (optional)

Scanning Slack content is a separate concern from notifications. It additionally needs
`channels:history`, `groups:history`, `channels:read`, `groups:read` and `files:read`, plus
Event Subscriptions (`message.channels`, `message.groups`, `file_shared`, `file_deleted`)
pointing at `https://<telescreen host>/api/collectors/slack/webhook`, signed with the
signing secret. Keep it in a separate manifest or app unless the user wants one app for
both, and update the scope test accordingly.

## Troubleshooting

| Symptom | Cause and fix |
|---------|---------------|
| `invalid_app_directory` | The CLI did not run inside `examples/slack-app` (for example `mise exec -C` moved it) |
| `cat: --source=...: No such file` | A hook command received the CLI's extra flags; keep the hook as the shell script |
| `prompt_error ... Select an app` | The project is linked; pass `--app deployed` (the mise tasks do) |
| `app_manifest_update_error` | Manifest differs from app settings; see step 3 |
| `channel_not_found` on post | Bot is not in the private channel, or the id is a user or self-DM id; invite the bot and use the channel id |
| `not_in_channel` | Public channel without `chat:write.public` and without an invite |

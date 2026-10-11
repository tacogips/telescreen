# V12-P11 Wiring, CLI (notify test, member set-role, token --member), Route-Walk Test

**Status**: Not Started
**Design Reference**: design-docs/specs/design-v1-2-notifications-and-identity.md sections 5.2, 7.1, 7.4, 10; design-docs/specs/command.md (v1.2 rows)
**Created**: 2026-10-11
**Last Updated**: 2026-10-11

## Plan Metadata

- planId: `v12-p11-wiring-cli`
- planPath: `impl-plans/active/v12-p11-wiring-cli.md`
- wave: 4
- dependsOn: `v12-p04-notification-templates`, `v12-p05-workflow-notification-sets`, `v12-p06-rbac-enforcement`, `v12-p07-directory-roles`, `v12-p08-oidc-identity`, `v12-p10-api-routers`
- writePaths:
  - `impl-plans/active/v12-p11-wiring-cli.md`
  - `src/telescreen/wiring.py`
  - `src/telescreen/wiring_v12.py` (new)
  - `src/telescreen/cli.py`
  - `src/telescreen/cli_v12.py` (new)
  - `tests/e2e/test_api_routes.py`
  - `tests/e2e/test_wiring_v12.py` (new)
  - `tests/e2e/test_route_permissions.py` (new)
  - `tests/e2e/test_cli_v12.py` (new)
- sharedPaths: none
- artifactRoots: none

Read `impl-plans/conventions.md` first.

## Intent and Context

Bind every v1.2 implementation into the container:

- the DB template resolver and catalog;
- the OIDC verifier, sign-in service and provisioner;
- the role service;
- the three new routers;
- `ApiContext.verifier` / `identities`.

Add the CLI commands, and add the full-app route-walk and scope-compatibility tests
that guard R1.

Imitate:

- `src/telescreen/wiring_v11.py` (builder functions returning small frozen dataclasses);
- `src/telescreen/wiring.py:build_container` (router tuple, `Container` fields);
- `src/telescreen/cli.py:_run_async_command` (the `token` branch);
- `tests/e2e/test_wiring_v11.py`, `tests/e2e/test_api_routes.py`.

## Non-goals

- `api/app.py` needs no change (it includes `container.routers`).
- No service changes. A service bug becomes a Reconciliation request to its owning plan.
- No live-service code (p12).

## Invariants

- `wiring.py` stays under 1000 lines (733 now); new construction goes into
  `wiring_v12.py`.
- `Container` gains one field at the end: `v12: WiringV12`, holding `templates:
  TemplateService`, `notifications: NotificationService`, `delivery:
  NotificationDeliveryHandler`, `roles: RoleService` and `sign_in: SignInService |
  None`. Existing positional construction is updated in one place only.
- With `settings.oidc is None`, there is no verifier, `/api/auth/session` returns 404,
  and behavior is otherwise v1.1-identical for API tokens.

## Deliverables

1. **`wiring_v12.py`**: `build_v12(settings, primitives, directory pieces, gate, queue,
   resolver, http) -> WiringV12Parts`:
   - `DbTemplateResolver` and `TemplateService`;
   - `RoleService`, `DirectoryProvisioner(fingerprinter, ...)`;
   - when `settings.oidc`: `OidcProviderClient(http, settings.oidc, clock)`,
     `OidcBearerVerifier` and `SignInService(sessions, ...)`;
   - the routers `build_notification_templates_router`, `build_users_router`
     (`idp_role_mapping = settings.oidc is not None`) and `build_auth_router`.
2. **`wiring.py`**:
   - `ApiContext(sessions, clock, audit, verifier, identities)`;
   - `NotificationService(..., templates=DbTemplateResolver(...))`;
   - `WorkflowService(..., templates=template_service)`;
   - append the three v12 routers to `routers`;
   - fill the `Container.v12` field.
   - Keep the import order and the existing names.
3. **`cli_v12.py`** + **`cli.py`** parser additions (exit codes per command.md):
   - `notify test --channel email|slack --to REF [--template NAME]`:
     - an unknown template -> stderr "unknown template", exit 2;
     - in one transaction: `container.v12.notifications.send(NotificationRequest(channel,
       (ref,), "", "", "system:cli", event=<template event or workflow_notify>,
       template_name=name or "system.workflow_notify",
       context=synthetic_context(event), record_unreachable=True))`;
     - then, for each queued id, `await container.v12.delivery.handle(ClaimedTask-like
       value for that id)`. Build it with the real `ClaimedTask` constructor and
       attempt 1, and use max_attempts 1 so failures end as `failed`;
     - print one JSON line per row (`notification_id`, `channel`, `status`,
       `dlp_verdict`, `error_code`) using `dump_json`;
     - exit 0 when all rows are `sent`, else 1.
   - `member set-role HANDLE ROLE` (`none` allowed): `RoleService.set_role(...,
     actor_ref="system:cli", source=cli)`; `LAST_ADMIN` -> stderr "last admin cannot be
     removed", exit 1; unknown handle -> exit 1.
   - `token create --member HANDLE`: resolve via `TokenService.member_id_for_handle`;
     unknown -> exit 2.
4. **`tests/e2e/test_route_permissions.py`**:
   - build the full app with `build_container` (SQLite, in-memory search, OIDC
     configured through `tests/support/oidc.py` and a MockTransport);
   - walk `app.routes`: for every `fastapi.routing.APIRoute`, collect dependency callables
     recursively from `route.dependant` (including `route.dependencies`). Exactly one
     must be a `PermissionDependency` or an `ExternalAuthDeclaration`;
   - the set of external `(method, path, kind)` must equal the allow-list of design 5.1
     (health, auth config, auth session, the collectors webhook, the 6 police v1 routes);
   - scope compatibility: a hard-coded table `(method, path) -> frozenset of v1 scopes
     accepted` for every pre-v1.2 route (derived from the p06 table and v1
     `require_scope`). For each route, compute `{s for s in ApiScope if
     route_permission in scope_permissions({s})}` and assert equality;
   - the test fails with the offending method and path listed.
5. **`tests/e2e/test_wiring_v12.py`**:
   - the container builds with and without OIDC;
   - `/api/auth/config` reflects the settings;
   - a JWT minted by the test IdP for a provisioned admin reaches
     `/api/notification-templates` (200);
   - a viewer JWT gets 403 on template create;
   - `NotificationService` resolves a DB template activated through the API.
6. **`tests/e2e/test_cli_v12.py`**:
   - `notify test --channel slack --to slack_channel:C0123ABCDE`, with a container
     built by `build_container(..., http=httpx.AsyncClient(transport=MockTransport))`
     returning `{"ok":true,"channel":"C0123ABCDE","ts":"1.1"}` and a Slack token env
     ref, run through `_run_async_command(container, args)` -> exit 0 and a JSON line
     with status `sent`. No SMTP injection is needed;
   - an unknown template -> 2;
   - an unreachable `email:` ref on Slack -> 1 and a `skipped` line;
   - `member set-role` last admin -> 1;
   - `token create --member` binds the member.
7. **`tests/e2e/test_api_routes.py`**: add the new list routes to the assembled-route
   200 checks.

## Pitfalls

- The route walk must include routes added by every router, including the police
  contract routes, and must exclude the static SPA `Route` (not an `APIRoute`).
- `ClaimedTask` fields: read `ports/workflow.py` for the exact constructor. Do not invent
  a fake type.
- The CLI must not print the template text or the rendered body, only the row metadata
  listed.
- When OIDC is configured but discovery is unreachable at startup, building the container
  must not fail (the verifier is lazy).

## Verification

Conventions section 7 with `<src>` = `src/telescreen/wiring.py src/telescreen/wiring_v12.py
src/telescreen/cli.py src/telescreen/cli_v12.py` and `<tests>` = `tests/e2e`. Also run the
full offline suite, which must exit 0 at this wave:

```bash
uv run --frozen pytest -p no:cacheprovider > /tmp/telescreen-v12-p11-wiring-cli-pytest-all.log 2>&1; echo "exit=$?"
uv run --frozen python tools/typelint.py > /tmp/telescreen-v12-p11-wiring-cli-typelint-all.log 2>&1; echo "exit=$?"
uv run --frozen ty check > /tmp/telescreen-v12-p11-wiring-cli-ty-all.log 2>&1; echo "exit=$?"
```

## Completion Criteria

- [ ] Deliverables 1-7 implemented; `wiring.py` < 1000 lines.
- [ ] The route-walk and scope-compatibility tests pass.
- [ ] The full pytest, typelint and ty runs exit 0 (log paths recorded).
- [ ] Progress Log complete.

## Progress Log

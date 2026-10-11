# v1.2: Notification Templates, Notification Sets, Roles and OIDC SSO

This document specifies Telescreen v1.2. The owner asked (2026-10-11, translated): "Did you
add email and Slack notifications? Verify that they actually work: test email with a
Mailpit container, test Slack in the tacogips-test workspace. Make notification templates
changeable. Make notification destinations configurable inside workflows, specifying a set
of destination plus notification method. Also verify registered-user permission management
and SSO integration, using a Keycloak container where test users are registered."

Status: implemented (2026-10-12). N1-N3, R1 and R2 shipped on `main` through the 12 v1.2
plans `v12-p01` to `v12-p12`. All of them are integration-accepted and archived under
`impl-plans/completed/`, and their run manifests (`telescreen-v12-20261011-d1` to `-d4`)
are under `impl-plans/completed/dispatch/`. The v1.2 leak matrix and the end-to-end flows
T1, T2, A1 and A2 pass offline; the `tests/live` Mailpit, Slack and Keycloak tests are
env-gated and run by the owner against the real services.

The design was written on 2026-10-11 as the baseline for the `v12-pNN` plans. It extends the implemented
v1 and v1.1 designs and replaces none of their decisions except those listed in section 1.
Tables are in `design-data-model.md` ("v1.2 additions"), endpoints in `design-api-and-ui.md`
("v1.2 endpoints"), DSL changes in `design-workflow-engine.md` ("Workflow DSL v2"), leak
rules in `design-masking.md` ("v1.2 additions"), UI in `design-ui-sentry.md` section 10, and
the OIDC decisions in `design-docs/user-qa/qa-oidc-sso.md`.

## Overview

| # | Feature | Summary |
|---|---------|---------|
| N1 | Editable notification templates | Versioned, DB-stored templates per event kind, with built-in defaults that reproduce the current hard-coded text; allow-listed masked context; validate, preview (synthetic context), activate; audited; Settings UI |
| N2 | Workflow notification sets | `apiVersion: telescreen.workflow/v2` adds named sets of targets (recipients x channels x optional template); notify steps, human task assignment and escalation tiers fan out one DLP-gated notification per target and channel and record per-target outcomes |
| N3 | Live delivery verification | `tests/live` against Mailpit, Slack and Keycloak (skipped offline) and `telescreen notify test` |
| R1 | Roles and permissions | Roles `admin`, `responder`, `viewer`; one `Permission` per route action; one enforcement dependency; route-walk test; last-admin protection; token permission = scopes intersected with the bound member's role |
| R2 | OIDC SSO | UI Authorization Code + PKCE; backend JWT validation with discovery and JWKS rotation; `(issuer, sub)` identity; verified-email linking or JIT creation; role and team claim mapping; 403 without a role; audited sign-ins; static-token login kept |

Baseline facts this design builds on (checked against `main` at 7402f51):

- `NotificationService.send` (`notifications/sender.py`) already resolves recipient refs,
  runs one DLP inspection per request, writes one `notifications` row per recipient ref
  and enqueues `notification_delivery`. `SlackNotifier` and `EmailNotifier` accept only
  `CensoredMessage`.
- The recipient kinds `member:`, `team:`, `email:` and `slack:` already resolve in
  `directory/resolver.py`. v1.2 adds `slack_channel:` and keeps `slack:` as an alias.
- Hard-coded notification text exists in nine call sites: `workflow/executors/notify.py`
  (inline only), `workflow/executors/human.py` (task assigned, owner review assigned),
  `workflow/escalation/sla.py`, `issues/digest.py`, `issues/exceptions.py` (requested,
  expired), `issues/verification.py`, `collectors/health.py`, `police/health.py`. There is no
  separate human task reminder notification; SLA escalation tiers are the reminder
  mechanism, and v1.2 adds no new reminder.
- Authentication is `api/common/auth.py` `require_scope` (scopes `read`, `write`, `admin`,
  `ingest`; `admin` implies all), used by 17 routers. Six in-handler checks in three
  routers (`human_tasks`, `issues`, `exceptions`) additionally call
  `principal.has(ApiScope.ADMIN)`.
- `pyjwt[crypto]` is already a runtime dependency (Google service-account auth). No new
  Python or UI dependency is needed for v1.2.
- `examples/compose/dev-services.yml` runs Mailpit (SMTP 1025, API 8025), Keycloak 26.4
  (8080, realm `telescreen`, public client `telescreen-ui` with S256 PKCE, direct access
  grants enabled, a `groups` claim mapper and an audience mapper) and PostgreSQL 16 (55432).

## Details

### 1. Decisions

| # | Topic | Decision | Rationale |
|---|-------|----------|-----------|
| D1 | Template identity and activation | A template is a `name` with immutable versions. All versions of a name share one `event_kind`. At most one version per name is active; activation is per name (like workflows and policies). New versions are created inactive | Same lifecycle as workflows and policies; no new concept |
| D2 | Built-in defaults | One built-in per event kind, named `system.<event_kind>`, held in code as version 0. A DB version of a `system.*` name overrides it once activated; activating version 0 restores the built-in. Built-ins cannot be deleted, so a referenced name always resolves. New names may not start with `system.` | Defaults survive without seeding data in a migration |
| D3 | Template syntax | The existing path-only `{{ path }}` syntax (`workflow/dsl/templates.py` `parse_template`). No logic, filters or loops | Requirement: "the existing safe template syntax" |
| D4 | Where rendering happens | `NotificationService.send` renders when the request carries an `event` (template path); requests without `event` keep the v1 inline path. Call sites pass a typed `NotificationContext`, never text | One renderer, one DLP gate, call sites cannot bypass the allow-list |
| D5 | Workflow-referenced templates | Templates referenced from workflows must have event kind `workflow_notify` | A single variable set for workflow authors; checked at upload |
| D6 | Workflow schema version | `apiVersion: telescreen.workflow/v2` adds sets, targets and template references. v1 documents load unchanged; v2-only fields in a v1 document are a validation error | Backward compatible, explicit |
| D7 | Recipient kinds | Add `slack_channel:<id>` (id pattern `^[CDGUW][A-Z0-9]{8,}$`; a `U`/`W` id posts a DM). Keep `slack:` (v1 alias, permissive) and `email:<address>` | `slack:` and `email:` already exist; existing workflows keep working |
| D8 | Per-target outcomes | Every (target, channel) produces `notifications` rows with `set_name`, `target_index`, `template_name`, `template_version`, `event_kind`. Unknown or unreachable recipients on fan-out sites produce a `skipped` row with `error_code` instead of failing | Outcomes are queryable and audited in one place |
| D9 | Roles | `admin`, `responder`, `viewer`. No `auditor` role: `viewer` already reads audit (as the v1 `read` scope did), and no accepted requirement separates audit reading from other reading | Smallest role set that covers the request |
| D10 | Permission model | `Permission` enum, one value per route action (section 5). Role permission sets mirror the v1 scopes: viewer = `read`, responder = `write`, admin = `admin` minus machine permissions. The only machine permission, `ingest.push`, comes from token scopes alone | Existing tokens keep their exact behavior; the matrix is explicit |
| D11 | Token intersection | Unbound token: scope permissions. Member-bound token: scope permissions intersected with the member's role permissions (human permissions), plus machine permissions from scopes. OIDC session: role permissions only | Requirement: "effective permission is the intersection" |
| D12 | Migration of members and tokens | `0003_v1_2` sets each member's role from the highest scope among its non-revoked bound tokens (`admin` -> admin, `write` -> responder, `read` -> viewer), else null | No existing bound token loses or gains access |
| D13 | OIDC role claim | Default `TELESCREEN_OIDC_ROLE_CLAIM=realm_access.roles` with map `telescreen-admin=admin,telescreen-responder=responder,telescreen-viewer=viewer`; the highest mapped role wins. Groups (`groups` claim) map to teams only, and that map is empty by default | Matches the realm: realm roles carry the role, groups carry team membership |
| D14 | IdP authority over roles | With OIDC role mapping, every sign-in sets the member's role from the claims (or `TELESCREEN_OIDC_DEFAULT_ROLE`, or none). API role changes stay possible but are overwritten at the next sign-in of an OIDC-linked member; the UI marks such users "Managed by IdP". The last-admin rule applies to Telescreen-side changes (API, UI, CLI); an IdP-driven demotion is applied and audited | The IdP is the source of truth for SSO users; recovery uses the CLI (section 7.4) |
| D15 | Provisioning | `TELESCREEN_OIDC_PROVISIONING=link` (default): an unknown `(issuer, sub)` is linked to an existing active member with the same verified email; `create`: link, else create a member. A subject with no mapped role and no default role is denied before any provisioning | Requirement: "optional JIT provisioning by verified email (configurable)" |
| D16 | Identity storage | `(issuer, sub)` is stored as a `member_identities` row of kind `oidc_subject` with the keyed `identity_key = HMAC(identity key v1, "identity\0oidc_subject\0" + issuer + "\0" + sub)` | Reuses the v1.1 keyed identity table; no raw IdP id stored |
| D17 | Session model | No server-side session. The SPA holds the access, refresh and ID tokens in `sessionStorage` (as v1 holds the static token) and calls `POST /api/auth/session` once per sign-in. Each API call carries the access token. Logout clears storage and redirects to the IdP `end_session_endpoint` when one is advertised | No cookies, so no CSRF surface; small hand-written PKCE |
| D18 | Static tokens | Always accepted for API clients. `TELESCREEN_UI_TOKEN_LOGIN` (default `true`) controls only whether the login page offers token entry | Requirement: "static bearer-token login remains as a configurable fallback" |
| D19 | JWT library | `pyjwt[crypto]` (already a dependency), used only through typed entry points: `jwt.PyJWK.from_json(...)` for keys and `jwt.api_jws.PyJWS().decode(...)`, which returns the verified payload bytes. Header and claims are parsed by Telescreen (`parse_json`, typed accessors). No per-file-ignore is planned | No new dependency; untyped dict APIs of PyJWT are not used |
| D20 | Notify test CLI | `telescreen notify test` queues through the real `NotificationService` and then delivers inline with the real `NotificationDeliveryHandler` | Exercises the production path, including DLP, rows and audit |

### 2. Shared vocabulary, permissions and ports (foundation)

New or extended enums in `telescreen.core.vocabulary` (persisted as `String(32)`, so no DB
enum migration is needed):

| Enum | Values |
|------|--------|
| `NotificationEvent` (new) | `workflow_notify`, `human_task_assigned`, `owner_review_assigned`, `sla_escalated`, `owner_digest`, `exception_requested`, `exception_expired`, `verification_inconclusive`, `collector_health_alert`, `police_health_alert` |
| `NotificationStatus` | adds `skipped` |
| `Role` (new) | `admin`, `responder`, `viewer` |
| `MembershipOrigin` (new) | `manual`, `idp` |
| `IdentityKind` / `IdentityOrigin` | add `oidc_subject` / `oidc` |
| `OidcProvisioning` (new) | `link`, `create` |
| `PrincipalKind` (new) | `token`, `oidc` |
| `ExternalAuthKind` (new) | `public`, `provider_signature`, `police_contract`, `oidc_sign_in` |
| `ErrorCode` | adds `recipient_unreachable`, `unknown_template`, `unknown_variable`, `template_event_mismatch`, `last_admin`, `no_role`, `not_provisioned`, `identity_conflict`, `idp_unavailable`, `invalid_token` |

New L0 module `telescreen/core/permissions.py`: the `Permission` StrEnum (section 5), frozen
sets `ROLE_PERMISSIONS: tuple[tuple[Role, frozenset[Permission]], ...]`,
`SCOPE_PERMISSIONS` (same shape for `ApiScope`), `MACHINE_PERMISSIONS =
frozenset({INGEST_PUSH})`, and pure functions `role_permissions(role)`,
`scope_permissions(scopes)`, `effective_token_permissions(scopes, role | None, bound: bool)`,
`highest_role(roles)`. No mapping types (typelint).

Settings (`core/config.py`, with the OIDC group in `core/oidc_settings.py`; all optional;
OIDC is off unless the issuer is set):

| Variable | Default | Meaning |
|----------|---------|---------|
| `TELESCREEN_OIDC_ISSUER` | unset | Issuer URL, compared exactly with `iss` and the discovery `issuer` |
| `TELESCREEN_OIDC_CLIENT_ID` | `telescreen-ui` | Public PKCE client used by the UI; checked against `azp` when present |
| `TELESCREEN_OIDC_AUDIENCE` | client id | Required member of `aud` |
| `TELESCREEN_OIDC_SCOPES` | `openid profile email` | Scopes the UI requests |
| `TELESCREEN_OIDC_ALGORITHMS` | `RS256` | Comma list, subset of `RS256,RS384,RS512,PS256,PS384,PS512,ES256,ES384`; `none` and `HS*` are a configuration error (exit 3) |
| `TELESCREEN_OIDC_LEEWAY_SECONDS` | 60 | `exp`/`nbf` leeway, 0-300 |
| `TELESCREEN_OIDC_JWKS_CACHE_SECONDS` | 300 | JWKS cache lifetime |
| `TELESCREEN_OIDC_ROLE_CLAIM` / `TELESCREEN_OIDC_ROLE_MAP` | `realm_access.roles` / `telescreen-admin=admin,telescreen-responder=responder,telescreen-viewer=viewer` | Dotted claim path to a string or string array; `<claim value>=<role>` list |
| `TELESCREEN_OIDC_DEFAULT_ROLE` | unset | Role for subjects with no mapped role; unset means 403 |
| `TELESCREEN_OIDC_GROUP_CLAIM` / `TELESCREEN_OIDC_TEAM_MAP` | `groups` / empty | `<group>=<team slug>` list; empty disables team sync |
| `TELESCREEN_OIDC_PROVISIONING` | `link` | `link` or `create` (D15) |
| `TELESCREEN_UI_TOKEN_LOGIN` | `true` | Offer static-token login in the UI |

Port changes (`telescreen.ports`):

- `ports/notifications.py`: `NotificationRequest` gains defaulted fields
  `event: NotificationEvent | None = None`, `template_name: str | None = None`,
  `context: NotificationContext | None = None`, `set_name: str | None = None`,
  `target_index: int | None = None`, `record_unreachable: bool = False`. New frozen
  dataclasses `NotificationContext` (section 3.3 fields, all defaulted) and
  `NotificationTarget` (`recipients: tuple[str, ...]`, `channels:
  tuple[NotificationChannel, ...]`, `template: str | None`). `NotificationReceipt` gains
  `skipped: tuple[str, ...] = ()` (notification ids of `skipped` rows).
- `ports/templates.py` (new): `TemplateCatalog.lookup(session, name) -> TemplateInfo | None`
  where `TemplateInfo(name, event_kind, active_version)`; used by workflow upload validation.
- `ports/identity.py` (new): `BearerVerifier.verify(token) -> VerifiedToken` (raises
  `invalid_token` / `idp_unavailable`) and `BearerVerifier.metadata() -> ProviderMetadata`
  (the discovered public endpoints served by `GET /api/auth/config`); `VerifiedToken(issuer, subject, claims_json: str)`
  where `claims_json` is canonical JSON of the verified payload; `MemberProvisioner` with
  `member_for_identity(session, issuer, subject) -> MemberAuth | None` and
  `sign_in(session, SignInRequest) -> SignInResult`; `MemberAuth(member_id, handle, role:
  Role | None, active)`.
- `ports/issues.py`: `IssueSnapshot` gains `masked_excerpt: str | None = None` (latest
  finding's masked snippet).

### 3. N1 Editable notification templates

#### 3.1 Model

A template version has: `name` (slug `^[a-z0-9][a-z0-9_.-]{0,63}$`), `version` (int;
built-ins are 0), `event_kind`, `description` (<= 200 chars, optional), `subject` (<= 200
chars), `body` (<= 4000 chars), and optional channel variants `email_subject`, `email_body`,
`slack_body`. Rendering per channel:

| Channel | Subject | Body |
|---------|---------|------|
| email | `email_subject` or `subject` | `email_body` or `body` |
| slack | `subject` | `slack_body` or `body` (the adapter still posts `subject + "\n" + body`) |

Rendering rules: substituted values come only from `NotificationContext`; a variable with no
value renders as empty text; newlines in a rendered subject become spaces (email header
safety); for Slack, `&`, `<` and `>` in substituted values (not in the literal template
text) are escaped as `&amp;`, `&lt;`, `&gt;`, so monitored-data-derived titles cannot form
mentions or links. Email is sent as `text/plain` only. The rendered subject and body pass
`Redactor` and then the DLP gate, as today.

#### 3.2 Resolution at send time

`NotificationService.send(request)` with `request.event` set:

1. name = `request.template_name` or `system.<event>`;
2. the active DB version of that name; else the built-in when the name is `system.*`; else
   (a referenced custom name that has no active version, which upload validation prevents)
   the built-in `system.<event>`, and the audit details record `template_fallback: true`;
3. render the channel variant with `request.context`, then `Redactor`, then DLP;
4. the row records `event_kind`, `template_name`, `template_version`.

The lookup runs in the caller's transaction. Requests without `event` use
`request.subject`/`request.body` exactly as in v1.

#### 3.3 Allow-listed context and variables per event

`NotificationContext` fields are filled by call sites only from masked sources (masked
issue columns, masked step titles, refs, enums, counts, timestamps, and configured names).
Variables:

| Variable | Source |
|----------|--------|
| `issue.short_id` | `TELE-<number>` |
| `issue.title` | `issues.display_title` (masked) |
| `issue.rule_class`, `issue.severity`, `issue.status`, `issue.exposure` | enums (`exposure` = visibility) |
| `issue.risk_score` | integer |
| `issue.location` | `issues.display_locator` (redacted locator; Telescreen stores no provider URL, so this is the source location reference) |
| `issue.excerpt` | latest finding `masked_snippet` |
| `issue.link` | `<TELESCREEN_PUBLIC_BASE_URL>/issues/<id>`; empty when the base URL is unset |
| `run.workflow`, `step.name` | workflow name, step name or id |
| `task.title`, `task.due_at`, `task.tier`, `task.link` | masked human task title, ISO due time, escalation tier number, `<base>/my-tasks` |
| `assignee.ref` | `member:<handle>` or `team:<slug>` |
| `digest.summary` | multi-line summary built by the digest service (short ids, enums, counts only) |
| `health.target`, `health.condition`, `health.details` | collector config name or Police worker name, condition enum, `key=value` list of timestamps and counts |

| Event kind | Allowed variable groups | Built-in subject / body (current text) |
|------------|-------------------------|----------------------------------------|
| `workflow_notify` | issue, run, step, task, assignee | new default: `Telescreen: {{ issue.short_id }} {{ issue.rule_class }}` / `{{ issue.title }}` plus severity, location and link lines |
| `human_task_assigned` | issue, run, task, assignee | `Workflow task assigned` / `{{ task.title }}` |
| `owner_review_assigned` | issue, task, assignee | v1.1 owner review text (`Telescreen: review ...`, short id, enums, risk, task link) |
| `sla_escalated` | issue, run, task, assignee | `Workflow task escalated` / `{{ task.title }}` |
| `owner_digest` | digest | `Telescreen owner digest` / `{{ digest.summary }}` |
| `exception_requested`, `exception_expired`, `verification_inconclusive` | issue | v1.1 text (`Exception requested for ...` and so on) |
| `collector_health_alert`, `police_health_alert` | health | v1.1 text |

Each built-in reproduces the current v1.1 subject and body byte for byte for the same inputs
(one parity test per event). Where v1.1 text depends on optional data (owner review link,
digest lines), the call site puts the already-formatted optional part into the context
variable, so templates need no conditionals.

#### 3.4 Validation (save, validate and preview)

Errors carry a field name and a code, never template text: `invalid_name`, `reserved_name`
(new `system.*` name), `template_event_mismatch` (new version of an existing name with
another event kind), `too_long`, `invalid_template` (parse error), `unknown_variable`
(a path outside the event's allow-list), `raw_value_detected` (`Redactor` hit in any text
field; 422, nothing stored, audited as `template.rejected` with field names only),
`incomplete_variant` (`email_body` without `email_subject` or the reverse is allowed; an
empty string variant is rejected).

Preview renders with a fixed synthetic context per event kind, held as constants in code.
The constants use reserved example names and Masker output formats only, and a unit test
asserts that the rule detector finds nothing in them. Preview then runs `Redactor` and the
DLP gate and returns the rendered email and Slack variants with their DLP verdicts. Preview
never reads issues, runs or members.

#### 3.5 Service and audit

`telescreen.notifications.templates` holds `TemplateService` (list, get, versions, create
version, activate, validate, preview), the built-in catalog and the renderer. It also
implements `TemplateCatalog`. Audit actions: `template.created` and `template.activated`
(details: name, version, event kind) and `template.rejected` (details: field names and codes).
The template text is not copied into audit details.

### 4. N2 Workflow notification sets

#### 4.1 DSL v2 additions

```yaml
apiVersion: telescreen.workflow/v2
notification_sets:
  - name: secops-alert
    targets:
      - {recipients: [team:secops, slack_channel:C0123ABCDE], channels: [slack]}
      - {recipients: [email:secops-leads@example.invalid], channels: [email], template: secops-leak-mail}
steps:
  - {id: alert, type: notify, notification_set: secops-alert, template: secops-leak-alert, next: triage}
  - {id: triage, type: human_task, assignee: team:secops, notify_set: secops-alert, ...}
```

- `notification_sets`: list of `{name (slug, unique), targets: [target]}`.
- target: `recipients` (non-empty list of refs), `channels` (non-empty subset of `slack`,
  `email`, no duplicates), optional `template` (name).
- `notify` step (v2): exactly one of `to` (v1 form, with `channel`), `notification_set`, or
  `targets` (inline list). Text: optional `template`, or inline `subject` + `body`, or
  neither (`system.workflow_notify`). Per target, the template is chosen in this order:
  `target.template`, then `step.template`, then the step's inline text, then
  `system.workflow_notify`.
- `human_task` / `evidence_upload` (v2): optional `notify_set` (name) or `notify_targets`
  (inline). The assignee notification of `notify_on_assign` is unchanged. The set targets are
  notified in addition when the task opens; their default template is
  `system.human_task_assigned`.
- Escalation tiers (policy documents): optional `notification_set` (name, resolved against
  the workflow of the escalated task's run) and optional inline `targets`. `notify` +
  `channels` become optional, but a tier needs at least one of `notify`, `targets`,
  `notification_set`. The default template is `system.sla_escalated`.

#### 4.2 Validation

Offline loader (CLI and API): v2-only fields in a v1 document give
`requires_api_version_v2`. Unknown set name gives `unknown_notification_set`. Duplicate set
name gives `duplicate_name`. Empty `recipients` or `channels` gives `empty_list`. A recipient
that is not a known kind or is malformed gives `invalid_recipient`; `email:` needs one `@`,
no whitespace, control characters or `,;<>`, and at most 254 characters, and
`slack_channel:` uses the D7 pattern. `email:` with channel `slack`, or `slack_channel:` /
`slack:` with channel `email`, gives `undeliverable_combination`. `to` and
`notification_set` together give `conflicting_fields`. Errors carry JSON paths only.

API-level checks in `WorkflowService.upload` (as for `sub_workflow` and `map`), through
`TemplateCatalog`: every referenced template exists with an active version
(`unknown_template`) and has event kind `workflow_notify` (`template_event_mismatch`).
For every `human_task` / `evidence_upload` with an `escalation_policy` whose tiers name a
`notification_set`, that set must be declared in the workflow (`unknown_notification_set`
at `/steps/<id>/escalation_policy`). Escalation policy saves accept any set slug. At runtime
a missing set skips that part of the tier with audit `notification.set_missing`, and the
tier's `notify` refs are still sent.

`member:` and `team:` existence stays a runtime check, as in v1.

#### 4.3 Fan-out and outcomes

For each target (index i) and each channel c of the target, the site sends one
`NotificationRequest(channel=c, recipients=target.recipients, event, template_name, context,
set_name, target_index=i, record_unreachable=True)`. `NotificationService` then:

- resolves each ref. An unknown ref (`recipient_unknown`), or a ref with no address on c
  (`recipient_unreachable`), becomes a `skipped` row with that `error_code`, an audit
  record `notification.skipped`, and no delivery task;
- renders and runs the DLP gate once per request, so once per target and channel, and
  writes `queued` (or `blocked`) rows for the reachable refs, as today.

The delivery handler also marks a row `failed` with `recipient_unreachable` when, at
delivery time, the resolved recipients have no address on the row's channel. Today such a
row is marked `sent` without any send.

The `notify` step output for v2 forms is `{queued: bool, notification_ids: [...], targets:
[{index, channel, queued, blocked, skipped}]}` (counts per target and channel, no recipient
values). `queued` is true when at least one row is queued. Recipient problems in v2 forms
never fail the step. The v1 `to` form keeps its v1 output and its failure on an unknown
recipient.

### 5. R1 Roles, permissions and enforcement

#### 5.1 Permission matrix

V = viewer, R = responder, A = admin. Scope columns give the v1 scope sets that now map to
each permission, and are identical to what each route required in v1.

| Route group | Permission | Routes | V | R | A | read | write | admin | ingest |
|-------------|-----------|--------|---|---|---|------|-------|-------|--------|
| Issues | `issues.read` | GET `/api/issues*` | x | x | x | x | x | x | |
| | `issues.write` | PATCH issue, comments, remediation, resolve `false_positive` | | x | x | | x | x | |
| | `issues.resolve_unverified` | resolve `manual_unverified` (in-handler check) | | | x | | | x | |
| Exceptions | `exceptions.read` | GET `/api/exceptions` | x | x | x | x | x | x | |
| | `exceptions.write` | request, approve, reject, revoke (approver rules stay in the service) | | x | x | | x | x | |
| | `exceptions.manage_any` | admin override in the service | | | x | | | x | |
| Workflows | `workflows.read` | GET `/api/workflows*` | x | x | x | x | x | x | |
| | `workflows.write` | upload, validate | | x | x | | x | x | |
| | `workflows.activate` | activate | | | x | | | x | |
| Runs | `runs.read` / `runs.write` | GET runs / start, cancel | x / | x / x | x / x | x / | x / x | x / x | |
| Human tasks | `human_tasks.read` / `human_tasks.complete` | list / complete as assignee | x / | x / x | x / x | x / | x / x | x / x | |
| | `human_tasks.manage_any` | `assignee=all`, complete any task | | | x | | | x | |
| Evidence | `evidence.read` / `evidence.upload` | GET / PUT | x / | x / x | x / x | x / | x / x | x / x | |
| Policies | `policies.read` / `policies.write` | GET, validate / create, activate, disable | x / | x / | x / x | x / | x / | x / x | |
| Escalation | `escalation.read` / `escalation.write` | GET / POST, PUT | x / | x / | x / x | x / | x / | x / x | |
| Directory | `directory.read` / `directory.write` | members, teams, identities | x / | x / | x / x | x / | x / | x / x | |
| Collectors | `collectors.read` / `collectors.write` | list, coverage / create, patch, run, sweep | x / | x / | x / x | x / | x / | x / x | |
| Map jobs | `map_jobs.read` / `map_jobs.write` | GET / cancel, retry-failed | x / | x / | x / x | x / | x / | x / x | |
| Police admin | `police.read` / `police.write` | workers list / disable | x / | x / | x / x | x / | x / | x / x | |
| Audit | `audit.read` | search, verify | x | x | x | x | x | x | |
| Search | `search.read` | `/api/search` | x | x | x | x | x | x | |
| Chat | `chat.use` | `/api/chat` | x | x | x | x | x | x | |
| Templates | `templates.read` / `templates.write` | list, get, validate, preview / create version, activate | x / | x / | x / x | x / | x / | x / x | |
| Users and roles | `users.read` / `users.write` | list users, role matrix / set role | | | x | | | x | |
| Tokens | `tokens.read` / `tokens.write` | list / create, revoke | | | x | | | x | |
| Settings (self) | `auth.self` | `GET /api/auth/me` | x | x | x | x | x | x | x |
| Ingest | `ingest.push` (machine) | `/api/ingest/v1/*` | | | | | | x | x |

Routes declared without a permission, through `declare_external(ExternalAuthKind)`:
`GET /api/health` and `GET /api/auth/config` (`public`); `POST
/api/collectors/{kind}/webhook` (`provider_signature`); `/api/police/v1/*`
(`police_contract`); `POST /api/auth/session` (`oidc_sign_in`).

#### 5.2 Enforcement

- `api/common/auth.py` replaces `require_scope` with `require_permission(ctx, permission)`.
  It returns a `PermissionDependency` (frozen dataclass with an async `__call__`) that
  authenticates (section 6.4) and checks `principal.allows(permission)`. Failures return
  401 `unauthorized` or 403 `forbidden`, with static detail text.
- `Principal(kind: PrincipalKind, token_id: str | None, member_id: str | None, role: Role |
  None, permissions: frozenset[Permission])`. `actor_ref()` is unchanged: `member:<id>`
  when bound, else `token:<id>`. In-handler `principal.has(ApiScope.ADMIN)` checks become
  `principal.allows(<permission>)` with the permissions of the table.
- Every route of every router declares exactly one `PermissionDependency` or one
  `declare_external` dependency. The route-walk test builds the full app with
  `create_app(build_container(...))`, visits every `APIRoute`, and fails when a route has
  none or more than one. It also checks that the external declarations equal the allow-list
  of 5.1.
- A scope-compatibility test asserts, for every route, that the set of v1 scopes accepted
  after v1.2 equals the set accepted in v1 (encoded in the test as a table). Both tests need
  the full app, so they live in `tests/e2e/test_route_permissions.py` (p11). p06 unit-tests
  the dependency, the principal construction and each router's permission choice with
  fakes.

#### 5.3 Role management and last admin

- `RoleService.set_role(session, handle, role | None, actor, source)` (directory). When the
  change would remove `admin` from an active member, it first locks every active admin row
  (`SELECT ... WHERE role='admin' AND active FOR UPDATE`). If no other active admin
  remains, it raises 409 `last_admin`. PostgreSQL re-checks the predicate on locked rows,
  so two concurrent demotions cannot both pass. SQLite is single-writer. The same check
  covers member deactivation if a later change adds it.
- Audit `role.changed`: details `from`, `to` and `source` (`manual`, `idp`, `cli`).
- `GET /api/users` reports `idp_managed: true` for members with an `oidc_subject` identity
  while OIDC role mapping is configured.

#### 5.4 Tokens

`POST /api/tokens {label, scopes, member_handle?}` returns the token once.
`GET /api/tokens` returns id, label, scopes, member handle, created_at and revoked_at.
`POST /api/tokens/{id}/revoke` revokes a token. Token audit records now carry the calling
principal's actor ref. The CLI gains `telescreen token create --member HANDLE`.

### 6. R2 OIDC SSO

#### 6.1 Flow

```text
UI /login -> GET /api/auth/config -> {oidc: {issuer, client_id, authorization_endpoint,
  token_endpoint, end_session_endpoint, scopes}, token_login}
UI: state, nonce (32 random bytes each), code_verifier (64 chars) -> sessionStorage
  -> redirect authorization_endpoint?response_type=code&code_challenge_method=S256&...
IdP -> /auth/callback?code&state -> state check -> POST token_endpoint (code, verifier)
  -> nonce check (ID token payload) -> tokens to sessionStorage
UI -> POST /api/auth/session (Bearer access token) -> member, role, permissions | 403
UI -> API calls with Bearer access token; refresh_token grant when exp - 30 s is reached
Logout -> clear storage -> end_session_endpoint?client_id&id_token_hint&post_logout_redirect_uri=<origin>/login
```

The backend serves the discovered endpoints in `/api/auth/config`, so the browser never
fetches discovery itself. `returnTo` is kept only when it is a relative path that starts
with `/` and not `//`.

#### 6.2 Backend token validation (`telescreen.identity`)

1. The bearer value is treated as a JWT when it has exactly two `.` separators. API tokens
   have none. With OIDC off, a JWT gets 401.
2. Length is at most 8192 bytes, and each segment must be valid base64url.
3. Header: `alg` must be in `TELESCREEN_OIDC_ALGORITHMS`; `kid` is required; `crit`, `jku`,
   `x5u`, `jwk` and `x5c` headers are rejected (keys come only from the cached JWKS).
4. Key: the JWKS entry with that `kid`. Its `kty` must match the algorithm family, `use` must
   be absent or `sig`, and `alg` must be absent or equal to the header `alg`.
5. Signature: `PyJWS().decode(token, key, algorithms=[alg])` returns the payload bytes.
6. Claims (parsed with `parse_json`): `iss` equals the configured issuer exactly; `aud`
   (string or list) contains the audience; `azp`, when present, equals the client id;
   `exp` is required and `now < exp + leeway`; `nbf`, when present, satisfies `now >= nbf -
   leeway`; `sub` is non-empty and at most 255 characters; the Keycloak `typ` claim, when
   present, must be `Bearer`, so ID tokens are rejected as access tokens.
7. Any failure gives 401 `invalid_token`. Logs carry static text only, never the token,
   header or claims.

Discovery and JWKS: `<issuer>/.well-known/openid-configuration` is fetched with httpx (5 s
timeout). Its `issuer` must equal the configured issuer. `jwks_uri` must have the same
scheme, host and port as the issuer, and `https` is required unless the configured issuer
itself is `http` (local Keycloak). The JWKS is cached for `JWKS_CACHE_SECONDS`. An unknown
`kid` triggers at most one refetch per 30 s, which handles key rotation; if the `kid` is
still unknown, the result is 401. If a fetch fails, a stale JWKS is used for up to 1 h, and
after that the result is 503 `idp_unavailable`. Tests inject `httpx.MockTransport` with a
locally generated RSA key (and a second key for rotation).

#### 6.3 Sign-in (`POST /api/auth/session`)

1. Validate the token (6.2).
2. Role: read the role claim path, map the values, and take the highest mapped role. With no
   mapped role, use the default role. With neither, return 403 `no_role` and audit
   `auth.sign_in_denied` (actor `system:auth`, details `reason` and the 12-hex
   identity-key prefix). Nothing is provisioned.
3. Identity: look up `member_identities` (`oidc_subject`, keyed). If none is found, read the
   claims `email` and `email_verified`:
   - `link`: link to the single active member whose email matches case-insensitively, when
     `email_verified` is true. If that member already has an `oidc_subject` identity for
     another subject, return 409 `identity_conflict`. If there is no match, return 403
     `not_provisioned`.
   - `create`: link as above; else create a member (handle = slug of `preferred_username`,
     suffixed `-2`, `-3` and so on when taken; display name from `name`; email when
     verified).
   An inactive member gets 403 `forbidden`.
4. Sync: set the role (D14, source `idp`, audited when it changes). When a team map is
   configured, add `idp`-origin memberships for mapped groups and remove `idp`-origin
   memberships of mapped teams that are absent from the claim. `manual` memberships are never
   touched, and unknown team slugs are ignored with a static warning.
5. Set `members.last_sign_in_at` and audit `auth.sign_in` (actor `member:<id>`, details
   `method: oidc`, `role` and `provisioning: existing|linked|created`).
6. Respond `{member: {handle, display_name}, role, permissions}`.

#### 6.4 Request authentication

- An API token is looked up by hash as in v1. Its permissions follow D11, using the bound
  member's current role. A bound member that is inactive has no human permissions.
- A JWT is validated (6.2). Then `member_for_identity(issuer, sub)` runs: no identity gives
  401 `unauthorized` (detail "sign-in required"); an inactive member or no role gives 403.
  Permissions are `role_permissions(role)`. Roles are read per request, so a role change
  takes effect on the next call.

#### 6.5 Keycloak setup (documented in `examples/compose/README.md`)

```text
TELESCREEN_OIDC_ISSUER=http://127.0.0.1:8080/realms/telescreen
TELESCREEN_OIDC_CLIENT_ID=telescreen-ui
TELESCREEN_OIDC_PROVISIONING=create
TELESCREEN_OIDC_TEAM_MAP=security=secops,dev=dev,ghe-admin=ghe-admin   # teams must exist
```

Keycloak puts the request host into `iss`, so the UI, the backend and the issuer value must
all use `127.0.0.1` (or all use `localhost`). Expected results: alice is admin, bob is
responder, carol is viewer, and dave gets 403 `no_role`.

### 7. N3 Live verification and the notify test CLI

#### 7.1 `telescreen notify test`

```text
telescreen notify test --channel email|slack --to <recipient ref> [--template NAME]
```

The command needs `TELESCREEN_DATABASE_URL` (migrated) and the channel settings (SMTP or
`TELESCREEN_SLACK_BOT_TOKEN`). In one transaction it calls `NotificationService.send` with
the template's event kind (default `system.workflow_notify`), the synthetic preview context,
`record_unreachable=True` and actor `system:cli`. After commit it runs
`NotificationDeliveryHandler.handle` inline for each queued row. The enqueued task stays and
is a no-op later, because the handler skips rows that are not `queued`. Output: one JSON
line per row with `notification_id`, `channel`, `status`, `dlp_verdict` and `error_code`.
Exit codes: 0 when every row is `sent`, 1 otherwise, 3 on a configuration error. An unknown
template name is a usage error (exit 2) with a static message.

#### 7.2 Live tests (`tests/live/`, marker `live`)

| Test | Gate (all must be set) | Behavior |
|------|------------------------|----------|
| `test_mailpit_email.py` | `TELESCREEN_TEST_MAILPIT_URL`, `TELESCREEN_TEST_SMTP_HOST`, `TELESCREEN_TEST_SMTP_PORT` | SQLite repositories plus the real `NotificationService`, the real `EmailNotifier` (STARTTLS off, no auth) and the delivery handler. Sends (a) a template render and (b) an inline body that contains runtime-assembled sample secrets, each with a unique run token in the subject. Reads `GET /api/v1/search?query=<token>` and `GET /api/v1/message/<ID>` from Mailpit. Asserts the subject, that the body is masked, and that no raw sample appears |
| `test_slack_delivery.py` | `TELESCREEN_TEST_SLACK_BOT_TOKEN`, `TELESCREEN_TEST_SLACK_CHANNEL` | Sends through the real `SlackNotifier`, which returns `SlackPostResult(channel, ts)` after `ok=true`. Calls `auth.test`; only if the `x-oauth-scopes` header lists `channels:history`, `groups:history`, `im:history` or `mpim:history` does it read back with `conversations.history` and assert masking. Otherwise only `ok=true` is asserted |
| `test_keycloak_oidc.py` | `TELESCREEN_TEST_KEYCLOAK_URL` | Gets access tokens for alice, bob, carol and dave through the `telescreen-ui` direct access grant (the dev realm enables it; the UI uses code + PKCE). Runs the full app on SQLite with OIDC pointed at the realm and `create` provisioning. Asserts `POST /api/auth/session` roles (admin, responder, viewer) and 403 `no_role` for dave, a viewer write gets 403, and an ID token gets 401. A second case drives code + PKCE through the Keycloak login form with httpx (form action parsed from the HTML) and asserts the same session result for alice |

The token, secrets and passwords (dev only, from the realm file) are read only from the
environment and the realm constants and are never printed. `tests/live/conftest.py` skips
each module when its variables are missing, so the default `uv run pytest` passes offline.
Skipped live tests are not risks.

#### 7.3 Owner walkthrough (documented)

`docker compose -f examples/compose/dev-services.yml up -d`, then
`TELESCREEN_DATABASE_URL=postgresql+asyncpg://telescreen:telescreen-dev@127.0.0.1:55432/telescreen`,
`telescreen migrate`, seed teams (`secops`, `dev`, `ghe-admin`) through the API with a CLI
admin token, set the section 6.5 OIDC variables, SMTP `127.0.0.1:1025` with STARTTLS off,
and the Slack token as `env:` refs from `~/.config/telescreen/slack-test.env`. Then
`telescreen serve` with `TELESCREEN_WEB_DIST` and `telescreen worker`. Sign in as alice, bob,
carol and dave. Upload an example v2 workflow, run it, and watch Mailpit (`:8025`) and Slack.

#### 7.4 Recovery

`telescreen member set-role HANDLE admin|responder|viewer|none` (audited with source `cli`,
last-admin rule enforced on demotion) and `telescreen token create --scopes admin` (unbound)
are the break-glass paths when an IdP change removes every admin (D14).

### 8. Data handling and threat model

| Threat | Control |
|--------|---------|
| `alg=none`, HS* key confusion | Allow-list from config only; `none`/`HS*` are rejected at config load; algorithm per verification is the header `alg` only if allowed, and `kty` must match |
| Key injection (`jku`, `x5u`, embedded `jwk`) | Those headers are rejected; keys come only from the issuer-origin JWKS |
| JWKS poisoning or SSRF | Discovery `issuer` must match exactly; `jwks_uri` must be same-origin; fixed timeouts; refetches are rate-limited |
| ID token used as access token | `typ` claim check, `aud`, and `azp` |
| Expired or replayed tokens | `exp`/`nbf` with leeway of at most 300 s; IdP access tokens are short-lived; no `jti` store (accepted) |
| Privilege escalation through claims | Only configured role values map; highest wins; no role means no provisioning and 403; teams only from the configured map, add/remove `idp` origin only |
| Account takeover through email linking | `email_verified` required; one `oidc_subject` per member per issuer (`identity_conflict`); `link` never creates |
| Last-admin removal race | Row locks on active admins (5.3); CLI recovery |
| Template injection or leakage | Path-only syntax; per-event allow-list checked at save; `Redactor` at save; context built only from masked sources; `Redactor` + DLP at send; plain-text email; Slack value escaping; subject newline folding |
| Preview leakage | Synthetic constants only; no data reads |
| Wider egress (`email:`, `slack_channel:`) | Same kinds as v1 `email:`/`slack:`; DLP per target and channel; workflow activation stays admin-only, so a responder-uploaded workflow cannot send until an admin activates it |
| Secrets | Slack token, SMTP password: secret refs (`env:`/`file:`) resolved at use, never logged, stored or returned; the public PKCE client has no secret; `/api/auth/config` returns public metadata only |
| Token storage in the browser (XSS) | `sessionStorage`, as v1; no cookies, so no CSRF; strict relative `returnTo` |

Persisted v1.2 data: template text (admin-entered and `Redactor`-checked), notification
rows (masked subject and body, refs, enums, ids), roles, keyed OIDC identities, membership
origin, timestamps, and audit details limited to names, versions, enums and key prefixes.
Access, ID and refresh tokens, `sub`, raw claims and IdP passwords are never stored,
logged, indexed or returned. Search indexes do not change.

Leak matrix extension (`tests/leak/flows_v12.py`, `tests/leak/test_leak_matrix_v12.py`,
real `build_container` with the v1 seams plus a MockTransport IdP):

1. Template create and validate with a sample secret in each text field: 422, no row, audit
   field names only. Preview with sample secrets in the inline text: 422.
2. A valid template rendered for a v2 notification set (Slack via MockTransport, email via a
   fake SMTP transport) and a v2 notify with inline text that contains samples: rows, sent
   payloads and step outputs hold only masked text, and per-target rows exist, including
   `skipped`.
3. Role change through the API, `last_admin` rejection, and token create/revoke: the audit
   records and API responses do not contain the token value.
4. OIDC sign-in with locally signed tokens: the access token, ID token and `sub` appear in
   no table, log, audit record, search document or response. `no_role` and `invalid_token`
   paths are included.
5. Notify-test CLI path with sample secrets in an inline body (through the service, as in 2).

The v1 assertions apply to all sinks: tables, captured logs, search documents, outbound
payloads, API bodies and tracebacks.

### 9. UI (summary; binding detail in `design-ui-sentry.md` section 10)

- `/login`: an SSO button (when `oidc` is configured) and token entry (when `token_login`
  is true); `/auth/callback`. Unauthenticated users are sent to `/login`. Sign out moves to
  the shell user menu. Token entry moves from Settings to `/login`.
- Settings gains a secondary panel: General, Notification templates
  (`/settings/templates`, `/settings/templates/:name`), and Users and roles
  (`/settings/users`). Editors are shown only with the `*.write` permissions returned by
  `/api/auth/me`.
- The workflow definition viewer shows a "Notification sets" panel (set, targets,
  recipients, channels, template), and step nodes show their set or target references.
- The hand-written PKCE module (`src/web/src/auth/`) uses WebCrypto (`crypto.subtle`,
  `crypto.getRandomValues`). There is no new package.

### 10. Implementation plan shape (v1.2 run)

Plans follow `impl-plans/conventions.md` (including 7a), stay under 400 lines each, and keep
every Python file under 1000 lines. Known split points:

- `workflow/dsl/loader.py` (652 lines): v2 parsing goes into a new
  `workflow/dsl/notification_sets.py`;
- `wiring.py` (733 lines): bindings go into a new `wiring_v12.py`;
- `cli.py` (250 lines): `notify test` and `member set-role` may go into `cli_v12.py`;
- new tests go into new `v12` files and directories.

Wave DAG. Write paths are disjoint, and no plan edits `pyproject.toml` beyond the `live`
marker of p01. `uv.lock` and `src/web/package.json`/`bun.lock` do not change; a needed
dependency goes to p01 or p03 by serial reconciliation.

| Wave | Plan | Owns | Depends on |
|------|------|------|-----------|
| 1 | v12-p01-foundation | `pyproject.toml` (`live` marker only), `src/telescreen/core/{vocabulary,config}.py`, new `src/telescreen/core/permissions.py`, `src/telescreen/ports/{notifications,issues}.py`, new `src/telescreen/ports/{templates,identity}.py`, `tests/core/v12/**`, `tests/ports/v12/**` | - |
| 1 | v12-p02-db | `src/telescreen/db/models/**`, `src/telescreen/db/migrations/versions/0003_v1_2.py`, `tests/db/v12/**` | - |
| 1 | v12-p03-ui-foundation | `src/web/src/api/**` (types, client with session and refresh, mock router and fixtures for every v1.2 endpoint), new `src/web/src/auth/**`, `src/web/src/components/**`, `src/web/src/{routes,App,main}.tsx`, `src/web/src/shell.test.tsx`, new `src/web/src/pages/login/**`, `src/web/scripts/**`, `src/web/vite.config.ts` (dev port 5174) | - |
| 2 | v12-p04-notification-templates | `src/telescreen/notifications/**`, call-site migration in `src/telescreen/issues/{verification,digest,exceptions,recipient_channel,service}.py` (`service.py`: `masked_excerpt` only), `src/telescreen/collectors/health.py`, `src/telescreen/police/health.py`, `tests/notifications/v12/**` | p01, p02 |
| 2 | v12-p05-workflow-notification-sets | `src/telescreen/workflow/dsl/**`, `src/telescreen/workflow/executors/{notify,human}.py`, `src/telescreen/workflow/escalation/**`, `src/telescreen/workflow/engine/definitions.py`, `src/telescreen/workflow/recipients.py`, `tests/workflow/v12/**` | p01, p02 |
| 2 | v12-p06-rbac-enforcement | `src/telescreen/api/common/**`, all 17 existing `src/telescreen/api/routers/*.py`, `tests/api/v12_rbac/**` | p01, p02 |
| 2 | v12-p07-directory-roles | `src/telescreen/directory/**` (RoleService, MemberProvisioner, `slack_channel:` resolution, token service changes), `tests/directory/v12/**` | p01, p02 |
| 2 | v12-p08-oidc-identity | new `src/telescreen/identity/**` (discovery, JWKS cache, verifier, claim mapping, sign-in orchestration over `MemberProvisioner`), `tests/identity/**` | p01 |
| 2 | v12-p09-ui-pages | `src/web/src/pages/{settings,workflows,runs}/**` | p03 |
| 3 | v12-p10-api-routers | new `src/telescreen/api/routers/{notification_templates,users,auth}.py` (users router also serves `/api/roles` and `/api/tokens`), `tests/api/v12_routers/**` | p04, p06, p07, p08 |
| 4 | v12-p11-wiring-cli | `src/telescreen/wiring.py`, new `src/telescreen/wiring_v12.py`, `src/telescreen/api/app.py`, `src/telescreen/cli.py` (and new `cli_v12.py`), `tests/e2e/test_api_routes.py`, new `tests/e2e/{test_wiring_v12,test_route_permissions,test_cli_v12}.py` | p04-p08, p10 |
| 5 | v12-p12-verification-docs | `tests/leak/**` (new v12 files), new `tests/e2e/test_flow_v12_*.py`, new `tests/live/**`, `examples/workflows/**`, `tests/examples/**`, `examples/compose/README.md` (and `dev-services.yml` / `keycloak/telescreen-realm.json` only if a live test needs it), `README.md` (serial final owner), `impl-plans/README.md`, status lines of the v1.2 design documents | all |

Rules carried over from v1.1: wave 2-3 plans keep `wiring.py` type-checking with defaulted
fields that turn features off (`templates` resolves built-ins only, `ApiContext.verifier =
None`, `TemplateCatalog` is `None` so upload template checks are skipped, and `slack_channel:`
is resolved by p07). p11 binds the real implementations. The route-walk test (p11) is the
gate that every router is covered.

E2E flows (p12):

- **T1**: an admin creates and activates a custom template, a v2 workflow with a set fans
  out to Slack and email (MockTransport, fake SMTP), and the per-target rows and outputs
  are checked.
- **T2**: human task `notify_set` and an escalation tier set.
- **A1**: OIDC sign-in for the four realm-equivalent users with locally signed tokens,
  permission checks per role, and token intersection.
- **A2**: last admin and the CLI `member set-role`.

UI gates: `bun run typecheck`, `bun run test`, `bun run build`, and the screenshot smoke with
the added pages (section 10.4 of `design-ui-sentry.md`).

### 11. Author self-check against the request

| Request item | Section |
|--------------|---------|
| Email and Slack really work (Mailpit, tacogips-test Slack) | 7.2, 7.3 |
| Templates changeable | 3 |
| Destination plus method sets in workflows | 4 |
| Registered-user permission management | 5 |
| SSO with Keycloak test users | 6, 7.2 |
| Never leak raw values | 3.1, 3.4, 8 |

## References

See `design-docs/references/README.md`. External: OpenID Connect Core 1.0 (ID token and
`azp` rules), RFC 7636 (PKCE), RFC 8725 (JWT best current practices: algorithm allow-lists
and key confusion), RFC 9700 (OAuth 2.0 security BCP: no implicit flow, PKCE for public
clients), Keycloak 26 server administration (realm roles in `realm_access.roles`, group
membership mapper, RP-initiated logout), Slack `chat.postMessage` and text escaping, and the
Mailpit API v1.

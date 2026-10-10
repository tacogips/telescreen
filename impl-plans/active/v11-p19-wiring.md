# V11-P19 Wiring: Bind v1.1 Services, Handlers, Schedule Providers and Routers

**Status**: Ready
**Design Reference**: design-docs/specs/design-v1-1-dspm-adoption.md sections 2, 12, 14 (wave rule); design-docs/specs/architecture.md section 4 (L4 composition)
**Created**: 2026-10-10
**Last Updated**: 2026-10-10

## Plan Metadata

- planId: `v11-p19-wiring`
- planPath: `impl-plans/active/v11-p19-wiring.md`
- wave: 4
- dependsOn: `v11-p05-issues-record`, `v11-p06-issues-remediation`, `v11-p07-policy-risk`, `v11-p08-collectors-core`, `v11-p09-collectors-slack`, `v11-p10-collectors-google`, `v11-p11-collectors-github`, `v11-p12-police-bureau`, `v11-p13-workflow`, `v11-p14-directory-search`, `v11-p17-api-issues`, `v11-p18-api-ops`
- writePaths:
  - `impl-plans/active/v11-p19-wiring.md`
  - `src/telescreen/wiring.py`
  - `src/telescreen/wiring_v11.py` (new)
  - `src/telescreen/wiring_collectors.py`
  - `tests/e2e/test_api_routes.py`
  - `tests/e2e/test_wiring_cli.py`
  - `tests/e2e/test_wiring_collectors.py`
  - `tests/e2e/test_wiring_v11.py` (new)
- sharedPaths: none
- artifactRoots: none

Read `impl-plans/conventions.md` first.

## Intent and Context

All v1.1 components were built with "feature off" defaults so that `wiring.py`
(`build_container`, about 626 lines) kept compiling. This plan turns every feature on by
binding the real implementations from settings. To keep `wiring.py` under 1000 lines,
put the new construction code in `src/telescreen/wiring_v11.py`. That module holds
builder functions returning small frozen dataclasses of the built services;
`build_container` calls them.

## Non-goals

- No feature code changes. A missing constructor parameter or a bug in a feature plan ->
  a reconciliation request, with a minimal local workaround only inside the wiring
  files.
- No change to `wiring_builders.py` (v11-p01 already builds the versioned
  `Fingerprinter`).

## Invariants

- Layering: only `wiring*.py` (L4) imports concrete L3 implementations across packages.
- Default settings (`owner_routing=False`, no `health_recipients`, digest cron default)
  produce a working container. Every existing e2e test still passes.
- Notifications are only ever sent through the existing `NotificationService` (DLP
  gate).

## Deliverables

1. **`wiring_v11.py`**, with typed builder functions:
   - `build_policy_and_risk(settings, primitives, queue, resolver) -> PolicyRisk(store:
     PolicyStore, decider: PlacementPolicyDecider, scorer: DefaultRiskScorer)`.
   - `build_directory_v11(primitives) -> DirectoryV11(identities: IdentityService,
     owners: DirectoryOwnerResolver)`.
   - `build_issue_ops(settings, primitives, issue_service, queue, rescanner,
     notification_service, resolver, owners, decider) -> IssueOps(remediation:
     IssueRemediationService, exceptions: ExceptionService, verify_handler,
     digest_handler, exception_maintenance, policy_handler, policy_maintenance)`.
   - `build_health(settings, primitives, notification_service) ->
     Health(collector_provider, police_provider, coverage: CoverageRecorder,
     collector_recorder: CollectorHealthRecorder)`.
2. **`wiring.py` changes in `build_container`**:
   - `DirectoryService(..., fingerprinter=primitives.fingerprinter)`.
   - `IssueService(..., decider=..., scorer=..., owners=..., owner_routing=settings.owner_routing,
     default_team=settings.default_team)`.
   - `CollectorScanHandler`, `CollectorSweeperImpl`, `CollectorService` and
     `CollectorRescanner` get `coverage` and `health`.
   - `HumanTaskExecutor` gets `issues=issue_service`,
     `public_base_url=settings.public_base_url`.
   - `SlaCheckHandler` gets `reader=issue_service`.
   - Police: `PoliceHealthProvider` with `notifier` and `recipients`.
   - The `Scheduler` providers tuple appends `IssuePolicyMaintenance`,
     `ExceptionMaintenanceProvider`, `CollectorHealthProvider` and `PoliceHealthProvider`.
   - Register the handlers `VerifyRescanHandler`, `PolicyReevaluateHandler` and
     `OwnerDigestHandler`.
   - Routers:
     - `IssuesRouterDeps(..., remediation, exceptions, owners, resolver)`;
     - add `build_exceptions_router` and `build_policies_router`;
     - `CollectorsRouterDeps(..., coverage, health_view, issues=issue_service)`;
     - `DirectoryRouterDeps(..., identities)`;
     - `HumanTasksRouterDeps(..., owners, remediation)`.
   - The police router needs no new deps if v11-p18 reads health inside the router via
     the police repository; read v11-p18's final code.
3. **`wiring_collectors.py`**: pass `internal_domains=settings.internal_domains` to the
   Google adapters (field from v11-p10). `build_collector_registry` already receives
   `settings`.
4. **Notification channel defaults**: health providers send on both channels the
   recipients resolve to, as v1 human-task notifications do. Reuse
   `workflow/recipients.py:reachable_refs` / `refs_for_channel` if the provider expects
   per-channel refs. Read v11-p08 and v11-p12 to match their constructor contract.

## Pitfalls

- Circular construction: `IssueService` needs `decider` before the collectors are built;
  the rescanner needs `issue_service`; the remediation service needs the rescanner. Build
  in this order: policy and risk -> directory v11 -> issue service -> scanner and
  collectors and rescanner -> notifications -> issue ops -> health -> handlers and
  providers -> routers.
- Do not construct a second `Fingerprinter`. Always use `primitives.fingerprinter`.
- Keep `wiring.py` under 1000 lines (check with `wc -l`).

## Tests

- `tests/e2e/test_api_routes.py`: the route smoke list adds `/api/policies`,
  `/api/exceptions`, `/api/collectors/<seeded id>/coverage` (seed a collector config) and
  `/api/members/<seeded handle>/identities`. Every one returns 200 with an admin token.
- `tests/e2e/test_wiring_v11.py`:
  - the container scheduler has the 4 new providers (assert by type);
  - the handler registry has the 3 new kinds;
  - `IssueService.decider`, `scorer` and `owners` are not None;
  - the human executor has `issues` set;
  - `settings.owner_routing=True` propagates;
  - a container built with `TelescreenSettings(fingerprint_keys=((1, ref), (2, ref2)),
    fingerprint_active_version=2)` produces findings with `fingerprint_key_version == 2`
    and the same `resource_key` as version-1-only (one ingest push through the API, then
    query the DB).
- Update `tests/e2e/test_wiring_cli.py` and `test_wiring_collectors.py` only if
  constructor changes require it.

## Verification

Conventions section 7 with
`<src>` = `src/telescreen/wiring.py src/telescreen/wiring_v11.py src/telescreen/wiring_collectors.py`
and `<tests>` = `tests/e2e`. Then the full gates, which must all exit 0, with exactly one
tolerance. v11-p20 runs in the same wave 4 and may still be editing its owned paths
`README.md`, `examples/workflows`, `examples/compose` and `tests/examples`. While
v11-p20 is in progress, the only tolerated failures are tests in `tests/examples` and
ruff/typelint/ty diagnostics located in `tests/examples/`. If one occurs: record the test ids or
file paths in the Progress Log as informational, then prove nothing else fails:

- pytest: rerun with `--deselect <test id>` per tolerated id to
  `/tmp/telescreen-v11-p19-wiring-pytest-deselected.log` -> `exit=0`;
- ruff format/check: rerun with `--extend-exclude tests/examples` -> `exit=0`;
- typelint: `! grep -v -E 'tests/examples/|^typelint:' /tmp/telescreen-v11-p19-wiring-typelint.log | grep -q .; echo "exit=$?"` -> `exit=0`;
- ty: `! grep -E '^ *--> ' /tmp/telescreen-v11-p19-wiring-ty.log | grep -v -E 'tests/examples/'; echo "exit=$?"` -> `exit=0`.

Every failure in a path this plan owns, or in any path not owned by v11-p20, blocks
completion. Per conventions 7a, runs with tolerated failures go only in the Progress
Log; only passing commands go in the step `verification` list. v11-p21 remains the final
whole-repository gate with no tolerances.

- `uv run --frozen ruff format --check --no-cache . > /tmp/telescreen-v11-p19-wiring-format.log 2>&1; echo "exit=$?"`
- `uv run --frozen ruff check --no-cache . > /tmp/telescreen-v11-p19-wiring-ruff.log 2>&1; echo "exit=$?"`
- `uv run --frozen python tools/typelint.py > /tmp/telescreen-v11-p19-wiring-typelint.log 2>&1; echo "exit=$?"`
- `uv run --frozen ty check > /tmp/telescreen-v11-p19-wiring-ty.log 2>&1; echo "exit=$?"`
- `uv run --frozen pytest -p no:cacheprovider > /tmp/telescreen-v11-p19-wiring-pytest.log 2>&1; echo "exit=$?"`
- `wc -l src/telescreen/wiring.py src/telescreen/wiring_v11.py > /tmp/telescreen-v11-p19-wiring-wc.log; echo "exit=$?"`

## Completion Criteria

- [ ] Every v1.1 component is bound; the providers, handlers and routers are registered.
- [ ] Full ruff, typelint, ty and pytest all exit 0 (or fail only in `tests/examples`
      while v11-p20 is in progress, with the reruns above showing `exit=0`); the wiring
      files are under 1000 lines.
- [ ] Progress Log complete.

## Progress Log

(append sessions per conventions section 8)

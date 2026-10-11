# V12-P08 OIDC Identity: Discovery, JWKS, JWT Verification, Sign-in Orchestration

**Status**: Not Started
**Design Reference**: design-docs/specs/design-v1-2-notifications-and-identity.md sections 1 (D13-D19), 6.2, 6.3, 8; design-docs/user-qa/qa-oidc-sso.md
**Created**: 2026-10-11
**Last Updated**: 2026-10-11

## Plan Metadata

- planId: `v12-p08-oidc-identity`
- planPath: `impl-plans/active/v12-p08-oidc-identity.md`
- wave: 2
- dependsOn: `v12-p01-foundation`
- writePaths:
  - `impl-plans/active/v12-p08-oidc-identity.md`
  - `src/telescreen/identity` (new package)
  - `tests/identity` (new directory)
- sharedPaths: none
- artifactRoots: none

Read `impl-plans/conventions.md` first.

## Intent and Context

Security-critical: validate bearer JWTs from the configured IdP and turn a valid token
into a sign-in. The new L3 package `telescreen.identity` implements `BearerVerifier`
(p01 port). `SignInService` maps claims to a role, denies with `NO_ROLE`, and calls
`MemberProvisioner.sign_in` (implemented by v12-p07; tests use a fake).

Imitate:

- `collectors/google/auth.py` (httpx calls, PyJWT import style, static errors);
- `core/json.py` accessors (`parse_json`, `JsonValue.get`, `as_str`, `as_list`) for
  claims;
- `issues/verification.py` audit calls (`AuditEntry`).

## Non-goals

- No per-request member lookup (p06). No router (p10). No UI.
- No refresh-token or userinfo handling on the backend. No `jti` replay store.
- No new dependency. No per-file-ignore. If ty or typelint cannot type a PyJWT call,
  isolate it in `identity/jose.py` and write a Reconciliation request for the foundation
  owner. Never add `# type: ignore`.

## Invariants

- The token, header, payload, `sub` and claims never appear in logs, exception details,
  audit records or `repr`.
- Algorithms come only from `OidcSettings.algorithms`. `none` and `HS*` are already
  rejected at config load; also assert at verify time that the header `alg` is in the
  configured set.
- Keys come only from the cached JWKS of the discovered same-origin `jwks_uri`.
- The clock comes from `Clock` (tests use `FakeClock`).

## Deliverables

1. **`identity/jose.py`**: compact JWS helpers.
   - `split_token(token) -> tuple[str, str, str]`: exactly 3 segments, total length <=
     8192, each segment base64url-decodable; else `INVALID_TOKEN`.
   - `parse_header(segment) -> JwsHeader(alg, kid, typ)`: rejects the `crit`, `jku`,
     `x5u`, `jwk` and `x5c` members, and a missing `kid`.
   - `verify_signature(token, jwk_json: str, alg) -> bytes`: `jwt.PyJWK.from_json(jwk_json,
     alg)`, then `jwt.api_jws.PyJWS().decode(token, key=<PyJWK>, algorithms=[alg])`;
     assert the result is `bytes`; any `jwt.PyJWTError` -> `INVALID_TOKEN`.
2. **`identity/discovery.py`** `OidcProviderClient(http: httpx.AsyncClient, settings,
   clock)`:
   - `metadata()`: GET `<issuer>/.well-known/openid-configuration` (timeout 5 s),
     parsed with a pydantic dataclass `DiscoveryDocument(issuer, authorization_endpoint,
     token_endpoint, jwks_uri, end_session_endpoint: str | None = None)` via
     `TypeAdapter.validate_json(response.content)`. `issuer` must equal the setting
     exactly. `jwks_uri` must share scheme, host and port with the issuer, and https is
     required unless the issuer is http. Cache the metadata with the same lifetime as
     the JWKS.
   - `key_for(kid) -> tuple[str, str | None, str | None, str | None] | None` returns the
     JWK JSON text plus `kty`, `use` and `alg`. Each JWK is kept as its canonical JSON
     text (`dump_json`); no dicts.
   - Cache rules:
     - fresh for `jwks_cache_seconds`;
     - an unknown kid triggers at most one refetch per 30 s;
     - on fetch failure, use the stale JWKS for up to 3600 s, then raise
       `IDP_UNAVAILABLE` (503);
     - an `asyncio.Lock` prevents concurrent refetch storms.
3. **`identity/verifier.py`** `OidcBearerVerifier(provider, settings, clock)` implements
   `BearerVerifier`:
   - `verify(token)`: split -> header (alg in settings) -> key (unknown kid ->
     `INVALID_TOKEN` after the refetch rule; `kty` must be `RSA` for RS*/PS* and `EC`
     for ES*; `use` absent or `sig`; JWK `alg` absent or equal) -> signature -> claims
     via `parse_json(payload)`:
     - `iss` equals the issuer exactly;
     - `aud` is a string or list containing the audience;
     - `azp`, when present, equals `client_id`;
     - `exp` is a required int and `now < exp + leeway`;
     - `nbf`, when present: `now >= nbf - leeway`;
     - `sub` is a non-empty string of at most 255 characters;
     - `typ`, when present, must be `Bearer`;
     - also reject tokens that carry a `nonce` or `at_hash` claim (only ID tokens have
       them; design review S3-V12-L01).
   - Returns `VerifiedToken(issuer, sub, dump_json(claims))`.
   - `metadata()` -> `ProviderMetadata`.
4. **`identity/claims.py`**:
   - `claim_strings(claims: JsonValue, dotted_path) -> tuple[str, ...]`: a string or an
     array of strings; anything else -> ();
   - `map_role(claims, settings) -> Role | None`: mapped values, `highest_role`, else
     `default_role`;
   - `profile(claims) -> (email, email_verified, preferred_username, name)`.
5. **`identity/signin.py`** `SignInService(sessions: async_sessionmaker[AsyncSession],
   verifier: BearerVerifier, provisioner: MemberProvisioner, settings: OidcSettings,
   audit: AuditLog, fingerprinter)`:
   - `async def sign_in(token) -> SignInResult` owns its transactions:
     - verify;
     - role None -> audit `auth.sign_in_denied` (actor `system:auth`, actor kind
       `SYSTEM`, target kind `auth`, details `reason=no_role` and `key_prefix` = the
       first 12 hex of the oidc identity key) and raise 403 `NO_ROLE`. Derive the key
       exactly as p07 does:
       `fingerprinter.owner_ref(OwnerIdentity(IdentityKind.OIDC_SUBJECT, issuer + "\0" +
       subject))`, then take the hex part after the first `:` (the same split as
       `directory/identities.py:IdentityService._key_parts`);
     - build `SignInRequest` and call `provisioner.sign_in`;
     - on `NOT_PROVISIONED`, `IDENTITY_CONFLICT` or `FORBIDDEN`, audit
       `auth.sign_in_denied` with that reason, then re-raise;
     - on success, audit `auth.sign_in` (actor kind `MEMBER`, actor
       `member:<member_id>`, details `method=oidc`, `role` and `provisioning`).
   - Transactions:
     - the provisioning and the success audit run in one `async with
       sessions.begin()`;
     - a denial (role None, or a `TelescreenError` from the provisioner, which rolls
       back the first transaction) is audited in a SEPARATE `async with
       sessions.begin()` block, and then the original error is raised;
     - the verifier runs before any transaction is opened.

     This keeps denied sign-ins audited even though the provisioning transaction rolls
     back.
6. **`identity/__init__.py`**: exports.

## Pitfalls

- `jwt.api_jws.PyJWS().decode` verifies only the signature and the `alg` membership. All
  claim checks are ours. Do not call `jwt.decode` (it returns a dict and does its own,
  differently configured claim checks).
- Base64url padding: add `=` padding before decoding segments. Reject non-ASCII.
- An `exp` given as a float or string -> `INVALID_TOKEN`.
- `aud` as an empty list -> invalid.
- `azp` applies only when present (Keycloak access tokens include it).
- When the discovery fetch fails on the first use, raise `IDP_UNAVAILABLE`, not
  `INVALID_TOKEN`.
- Never include the URL host or token fragments in error details.

## Tests (`tests/identity/`, using `tests/support/oidc.py` from p01, `httpx.MockTransport` and `FakeClock`)

- A valid RS256 token -> `VerifiedToken` with the matching issuer and subject; `repr`
  hides the subject.
- Negative cases, each -> `INVALID_TOKEN`:
  - `alg: none` (hand-built token);
  - `HS256` signed with the public key bytes;
  - header `jku`; header `jwk`; missing `kid`;
  - a kid that is unknown after refetch;
  - an RSA key with `alg` ES256;
  - wrong `iss`; `aud` mismatch; `azp` mismatch; expired beyond the leeway; `nbf` in
    the future beyond the leeway;
  - `typ: ID`; a `nonce` claim present; a tampered payload; 4 segments; a 9000-byte
    token.
- Leeway: `exp` 30 s in the past with leeway 60 -> valid.
- Rotation: a token signed with key 2 while the cache has key 1 -> one refetch -> valid;
  a second unknown kid within 30 s -> no extra HTTP call (count the requests).
- Discovery: a discovery `issuer` mismatch -> `IDP_UNAVAILABLE`, with a static warning
  log "oidc discovery issuer mismatch"; a cross-origin `jwks_uri` -> `IDP_UNAVAILABLE`;
  a fetch failure with a stale cache under 1 h -> still verifies; after 1 h ->
  `IDP_UNAVAILABLE`.
- Claims: `realm_access.roles` `["telescreen-viewer","telescreen-admin"]` -> admin; no
  mapped role and no default -> None; default `viewer` -> viewer; a `groups` string vs
  array.
- `SignInService` with a fake provisioner:
  - success audit details contain no sub, email or token;
  - no role -> `NO_ROLE` and a denial audit with `reason=no_role`, and the provisioner
    is not called;
  - `NOT_PROVISIONED` from the fake -> denial audited and re-raised.
- Log capture over all tests: the minted token strings and subs do not appear in
  `caplog.text`.

## Verification

Conventions section 7 with `<src>` = `src/telescreen/identity` and `<tests>` =
`tests/identity`.

## Completion Criteria

- [ ] Deliverables 1-6 implemented; every negative test passes.
- [ ] Scoped gates pass; typelint grep empty; no per-file-ignore added.
- [ ] Progress Log complete.

## Progress Log

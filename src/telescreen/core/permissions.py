"""Role and API-scope permission mapping shared across feature packages."""

from collections.abc import Sequence
from enum import StrEnum

from telescreen.core.errors import TelescreenError
from telescreen.core.vocabulary import ApiScope, ErrorCode, Role


class Permission(StrEnum):
    """An individually enforceable application capability."""

    ISSUES_READ = "issues.read"
    ISSUES_WRITE = "issues.write"
    ISSUES_RESOLVE_UNVERIFIED = "issues.resolve_unverified"
    EXCEPTIONS_READ = "exceptions.read"
    EXCEPTIONS_WRITE = "exceptions.write"
    EXCEPTIONS_MANAGE_ANY = "exceptions.manage_any"
    WORKFLOWS_READ = "workflows.read"
    WORKFLOWS_WRITE = "workflows.write"
    WORKFLOWS_ACTIVATE = "workflows.activate"
    RUNS_READ = "runs.read"
    RUNS_WRITE = "runs.write"
    HUMAN_TASKS_READ = "human_tasks.read"
    HUMAN_TASKS_COMPLETE = "human_tasks.complete"
    HUMAN_TASKS_MANAGE_ANY = "human_tasks.manage_any"
    EVIDENCE_READ = "evidence.read"
    EVIDENCE_UPLOAD = "evidence.upload"
    POLICIES_READ = "policies.read"
    POLICIES_WRITE = "policies.write"
    ESCALATION_READ = "escalation.read"
    ESCALATION_WRITE = "escalation.write"
    DIRECTORY_READ = "directory.read"
    DIRECTORY_WRITE = "directory.write"
    COLLECTORS_READ = "collectors.read"
    COLLECTORS_WRITE = "collectors.write"
    MAP_JOBS_READ = "map_jobs.read"
    MAP_JOBS_WRITE = "map_jobs.write"
    POLICE_READ = "police.read"
    POLICE_WRITE = "police.write"
    AUDIT_READ = "audit.read"
    SEARCH_READ = "search.read"
    CHAT_USE = "chat.use"
    TEMPLATES_READ = "templates.read"
    TEMPLATES_WRITE = "templates.write"
    USERS_READ = "users.read"
    USERS_WRITE = "users.write"
    TOKENS_READ = "tokens.read"
    TOKENS_WRITE = "tokens.write"
    AUTH_SELF = "auth.self"
    INGEST_PUSH = "ingest.push"


READ_PERMISSIONS: frozenset[Permission] = frozenset(
    permission
    for permission in Permission
    if permission.value.endswith(".read")
    and permission not in (Permission.USERS_READ, Permission.TOKENS_READ)
) | frozenset((Permission.CHAT_USE, Permission.AUTH_SELF))
WRITE_PERMISSIONS: frozenset[Permission] = READ_PERMISSIONS | frozenset(
    (
        Permission.ISSUES_WRITE,
        Permission.EXCEPTIONS_WRITE,
        Permission.WORKFLOWS_WRITE,
        Permission.RUNS_WRITE,
        Permission.HUMAN_TASKS_COMPLETE,
        Permission.EVIDENCE_UPLOAD,
    )
)
ALL_PERMISSIONS: frozenset[Permission] = frozenset(Permission)
MACHINE_PERMISSIONS: frozenset[Permission] = frozenset((Permission.INGEST_PUSH,))
INGEST_PERMISSIONS: frozenset[Permission] = frozenset(
    (Permission.INGEST_PUSH, Permission.AUTH_SELF)
)

SCOPE_PERMISSIONS: tuple[tuple[ApiScope, frozenset[Permission]], ...] = (
    (ApiScope.READ, READ_PERMISSIONS),
    (ApiScope.WRITE, WRITE_PERMISSIONS),
    (ApiScope.ADMIN, ALL_PERMISSIONS),
    (ApiScope.INGEST, INGEST_PERMISSIONS),
)
ROLE_PERMISSIONS: tuple[tuple[Role, frozenset[Permission]], ...] = (
    (Role.VIEWER, READ_PERMISSIONS),
    (Role.RESPONDER, WRITE_PERMISSIONS),
    (Role.ADMIN, ALL_PERMISSIONS - MACHINE_PERMISSIONS),
)


def scope_permissions(scopes: frozenset[ApiScope]) -> frozenset[Permission]:
    """Return the union of permissions granted by token scopes."""
    permissions: set[Permission] = set()
    for scope, granted in SCOPE_PERMISSIONS:
        if scope in scopes:
            permissions.update(granted)
    return frozenset(permissions)


def role_permissions(role: Role | None) -> frozenset[Permission]:
    """Return permissions for a role, or no permissions when unassigned."""
    for configured_role, permissions in ROLE_PERMISSIONS:
        if configured_role is role:
            return permissions
    return frozenset()


def effective_token_permissions(
    scopes: frozenset[ApiScope], role: Role | None, bound: bool
) -> frozenset[Permission]:
    """Intersect a bound token's scope grants with its member role."""
    scoped: frozenset[Permission] = scope_permissions(scopes)
    if not bound:
        return scoped
    role_grants: frozenset[Permission] = role_permissions(role)
    return (scoped & role_grants) | (scoped & MACHINE_PERMISSIONS)


def highest_role(roles: Sequence[Role]) -> Role | None:
    """Return the highest role in admin, responder, viewer order."""
    if Role.ADMIN in roles:
        return Role.ADMIN
    if Role.RESPONDER in roles:
        return Role.RESPONDER
    if Role.VIEWER in roles:
        return Role.VIEWER
    return None


def parse_role(value: str) -> Role | None:
    """Parse a role string, treating empty and none as unassigned."""
    normalized: str = value.strip().lower()
    if normalized in ("", "none"):
        return None
    try:
        return Role(normalized)
    except ValueError:
        raise TelescreenError(ErrorCode.VALIDATION_ERROR, "unknown role") from None

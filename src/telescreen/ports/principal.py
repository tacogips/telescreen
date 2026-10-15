"""Detached authentication result shared by trusted service adapters."""

from dataclasses import dataclass

from telescreen.core.permissions import Permission
from telescreen.core.vocabulary import PrincipalKind, Role


@dataclass(frozen=True)
class Principal:
    """Authenticated API identity and its granted permissions."""

    kind: PrincipalKind
    token_id: str | None
    member_id: str | None
    role: Role | None
    permissions: frozenset[Permission]

    def allows(self, permission: Permission) -> bool:
        """Return whether this principal holds a permission."""
        return permission in self.permissions

    def actor_ref(self) -> str:
        """Return a stable, non-secret audit reference."""
        if self.member_id is not None:
            return f"member:{self.member_id}"
        return f"token:{self.token_id}"

"""Count-only exposure mapping for transient Google Drive permissions."""

from telescreen.collectors.google.drive import DrivePermission
from telescreen.core.vocabulary import Visibility
from telescreen.ports.collectors import Exposure


def drive_exposure(
    permissions: tuple[DrivePermission, ...],
    drive_id: str | None,
    internal_domains: tuple[str, ...],
) -> Exposure:
    """Map transient Drive permissions to visibility and an external grant count."""
    domains: tuple[str, ...] = tuple(domain.casefold() for domain in internal_domains)
    if any(
        permission.type == "anyone" and permission.allow_file_discovery
        for permission in permissions
    ):
        visibility: Visibility = Visibility.PUBLIC
        external_count: int = 0
    elif any(permission.type == "anyone" for permission in permissions):
        visibility = Visibility.ANYONE_WITH_LINK
        external_count = 0
    else:
        grants: tuple[DrivePermission, ...] = tuple(
            permission for permission in permissions if permission.role != "owner"
        )
        if not domains and any(permission.type in ("user", "group") for permission in grants):
            visibility = Visibility.UNKNOWN
            external_count = 0
        else:
            external_count = sum(1 for permission in grants if _is_external(permission, domains))
            if external_count > 0:
                visibility = Visibility.EXTERNAL_SHARED
            elif any(
                permission.type == "domain" and _normalized_domain(permission.domain) in domains
                for permission in grants
            ):
                visibility = Visibility.DOMAIN
            elif any(
                permission.type in ("user", "group")
                and _principal_domain(permission.email_address) in domains
                for permission in grants
            ):
                visibility = Visibility.INTERNAL
            else:
                visibility = Visibility.PRIVATE
    return Exposure(visibility, external_count, shared_drive=drive_id is not None)


def _is_external(permission: DrivePermission, domains: tuple[str, ...]) -> bool:
    """Return whether a non-owner user, group or domain grant is external."""
    if permission.type == "domain":
        return _normalized_domain(permission.domain) not in domains
    if permission.type in ("user", "group"):
        principal_domain: str | None = _principal_domain(permission.email_address)
        return principal_domain is not None and principal_domain not in domains
    return False


def _normalized_domain(domain: str | None) -> str | None:
    """Normalize a transient permission domain for comparison."""
    return domain.casefold() if domain is not None else None


def _principal_domain(email_address: str | None) -> str | None:
    """Extract a transient principal's normalized email domain."""
    if email_address is None or "@" not in email_address:
        return None
    return email_address.rsplit("@", maxsplit=1)[-1].casefold()

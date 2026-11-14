"""OIDC bearer-token verification and sign-in orchestration."""

from telescreen.identity.claims import ClaimProfile, claim_strings, map_role, profile
from telescreen.identity.discovery import DiscoveryDocument, OidcProviderClient
from telescreen.identity.jose import JwsHeader, parse_header, split_token, verify_signature
from telescreen.identity.signin import SignInService, identity_key_prefix
from telescreen.identity.verifier import OidcBearerVerifier

__all__: list[str] = [
    "ClaimProfile",
    "DiscoveryDocument",
    "JwsHeader",
    "OidcBearerVerifier",
    "OidcProviderClient",
    "SignInService",
    "claim_strings",
    "identity_key_prefix",
    "map_role",
    "parse_header",
    "profile",
    "split_token",
    "verify_signature",
]

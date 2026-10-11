"""Locally generated OIDC signing keys and MockTransport endpoints for tests."""

from __future__ import annotations

import json
from dataclasses import dataclass

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from jwt.algorithms import RSAAlgorithm


@dataclass(frozen=True)
class TestIdp:
    """In-memory test identity provider with optional key-rotation support."""

    issuer: str
    audience: str
    private_key: RSAPrivateKey
    kid: str
    rotation_private_key: RSAPrivateKey | None = None
    rotation_kid: str | None = None

    @classmethod
    def generate(cls, issuer: str, audience: str) -> TestIdp:
        """Generate local RSA keys for an isolated test identity provider."""
        private_key: RSAPrivateKey = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        rotation_private_key: RSAPrivateKey = rsa.generate_private_key(
            public_exponent=65537, key_size=2048
        )
        return cls(issuer, audience, private_key, "test-key-1", rotation_private_key, "test-key-2")

    def _jwk_json(self, private_key: RSAPrivateKey, kid: str) -> str:
        """Return one public JWK with verification metadata."""
        jwk_json: str = RSAAlgorithm.to_jwk(private_key.public_key())
        return f'{jwk_json[:-1]},"kid":"{kid}","use":"sig","alg":"RS256"}}'

    def jwks_json(self) -> str:
        """Serialize public keys for the configured and optional rotated signer."""
        keys_json: str = self._jwk_json(self.private_key, self.kid)
        if self.rotation_private_key is not None and self.rotation_kid is not None:
            keys_json = (
                f"{keys_json},{self._jwk_json(self.rotation_private_key, self.rotation_kid)}"
            )
        return f'{{"keys":[{keys_json}]}}'

    def mint(
        self,
        claims_json: str,
        kid: str | None = None,
        alg: str = "RS256",
        extra_headers_json: str | None = None,
    ) -> str:
        """Sign caller-supplied claims without retaining the resulting token."""
        token_kid: str = kid or self.kid
        signing_key: RSAPrivateKey = self.private_key
        if token_kid == self.rotation_kid and self.rotation_private_key is not None:
            signing_key = self.rotation_private_key
        if extra_headers_json is None:
            return jwt.encode(
                json.loads(claims_json),
                signing_key,
                algorithm=alg,
                headers={"kid": token_kid},
            )
        return jwt.encode(
            json.loads(claims_json),
            signing_key,
            algorithm=alg,
            headers=json.loads(extra_headers_json),
        )

    def mock_handler(self, request: httpx.Request) -> httpx.Response:
        """Serve minimal discovery metadata and the in-memory JWKS."""
        discovery_url: str = f"{self.issuer}/.well-known/openid-configuration"
        jwks_url: str = f"{self.issuer}/.well-known/jwks.json"
        if str(request.url) == discovery_url:
            return httpx.Response(
                200,
                json={
                    "issuer": self.issuer,
                    "authorization_endpoint": f"{self.issuer}/protocol/openid-connect/auth",
                    "token_endpoint": f"{self.issuer}/protocol/openid-connect/token",
                    "end_session_endpoint": f"{self.issuer}/protocol/openid-connect/logout",
                    "jwks_uri": jwks_url,
                },
            )
        if str(request.url) == jwks_url:
            return httpx.Response(
                200, content=self.jwks_json(), headers={"content-type": "application/json"}
            )
        return httpx.Response(404)

"""Strict, secret-free authentication boundary for agent access."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping, Protocol, Any
from .query_contract import Principal, QueryApiError

MAX_TOKEN_LIFETIME_SECONDS = 15 * 60
MAX_CLOCK_SKEW_SECONDS = 60
READ_SCOPE = "domain-graph:read"
PROPOSED_SCOPE = "domain-graph:read-proposed"
KNOWN_SCOPES = frozenset({READ_SCOPE, PROPOSED_SCOPE, "domain-graph:write-propose", "domain-graph:write-apply", "domain-graph:audit-read"})

class TokenVerifier(Protocol):
    """Adapter for an IdP/JWT verifier; implementations must not return secrets."""
    def verify(self, token: str) -> Mapping[str, Any]: ...

@dataclass(frozen=True)
class DeploymentPolicy:
    tenant_id: str
    project_ids: frozenset[str]
    issuer: str
    audience: str

class Authenticator:
    def __init__(self, verifier: TokenVerifier, policy: DeploymentPolicy, *, now=None):
        self.verifier, self.policy, self.now = verifier, policy, now or (lambda: datetime.now(timezone.utc))

    def authenticate(self, bearer: str | None) -> Principal:
        if not isinstance(bearer, str) or not bearer.startswith("Bearer ") or len(bearer) <= 7:
            raise QueryApiError("unauthorized", 401, "unauthorized")
        try:
            claims = self.verifier.verify(bearer[7:])
            if not isinstance(claims, Mapping): raise ValueError
            required = ("iss", "aud", "exp", "sub", "tenant_id", "project_ids", "scopes")
            if any(k not in claims for k in required): raise ValueError
            if claims["iss"] != self.policy.issuer or claims["aud"] != self.policy.audience: raise ValueError
            subject, tenant = claims["sub"], claims["tenant_id"]
            projects, scopes = claims["project_ids"], claims["scopes"]
            if not isinstance(subject, str) or not 1 <= len(subject) <= 128: raise ValueError
            if not isinstance(tenant, str) or not 1 <= len(tenant) <= 128: raise ValueError
            if not isinstance(projects, (list, tuple, set, frozenset)) or not projects: raise ValueError
            if not all(isinstance(x, str) and 1 <= len(x) <= 128 for x in projects): raise ValueError
            if not isinstance(scopes, (list, tuple, set, frozenset)) or not scopes or not set(scopes) <= KNOWN_SCOPES: raise ValueError
            exp = self._timestamp(claims["exp"]); now = self.now()
            if exp <= now or (exp - now).total_seconds() > MAX_TOKEN_LIFETIME_SECONDS + MAX_CLOCK_SKEW_SECONDS: raise ValueError
            if "nbf" in claims and self._timestamp(claims["nbf"]) > now.replace(microsecond=0): raise ValueError
            if tenant != self.policy.tenant_id or not set(projects) <= self.policy.project_ids or READ_SCOPE not in scopes:
                raise QueryApiError("forbidden", 403, "forbidden")
            token_id = claims.get("jti")
            if token_id is not None and (not isinstance(token_id, str) or len(token_id) > 128): raise ValueError
            return Principal(subject, frozenset(projects), frozenset(scopes), exp, tenant, token_id)
        except QueryApiError: raise
        except Exception as exc: raise QueryApiError("unauthorized", 401, "unauthorized") from exc

    @staticmethod
    def _timestamp(value):
        if not isinstance(value, (int, float)) or isinstance(value, bool): raise ValueError
        return datetime.fromtimestamp(value, timezone.utc)

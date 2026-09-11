"""Safe, deterministic contracts for design/implementation consistency checks."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

from .ingest import ALLOWED_RELATIONS, ID_PATTERN

RULE_VERSION = "consistency-v1"
SAFE_ERRORS = {"invalid_request", "forbidden", "not_registered", "evidence_insufficient",
               "query_limit_exceeded", "query_timeout", "service_unavailable", "conflicting_evidence",
               "approval_required", "approval_invalid", "github_publish_failed"}


class ConsistencyError(ValueError):
    def __init__(self, code: str, status: int = 400, message: str = "request is invalid"):
        self.code = code if code in SAFE_ERRORS else "service_unavailable"
        self.status = status
        super().__init__(message if message in {"request is invalid", "service unavailable", "forbidden"} else "request is invalid")

    def as_dict(self, request_id: str) -> dict[str, Any]:
        return {"error": {"code": self.code, "message": self.code}, "meta": {"requestId": request_id}}


@dataclass(frozen=True)
class ConsistencyCheckRequest:
    repository_id: str
    pull_request_id: str | None = None
    head_sha: str | None = None
    baseline_revision: str | None = None
    scope_node_ids: tuple[str, ...] = ()
    include_proposed: bool = False
    max_findings: int = 200
    failure_threshold: str = "error_only"


def parse_request(value: Any) -> ConsistencyCheckRequest:
    if not isinstance(value, dict): raise ConsistencyError("invalid_request")
    allowed = {"repositoryId", "pullRequestId", "headSha", "baselineRevision", "scopeNodeIds", "includeProposed", "maxFindings", "failureThreshold"}
    if set(value) - allowed: raise ConsistencyError("invalid_request")
    rid = value.get("repositoryId")
    if not isinstance(rid, str) or not ID_PATTERN.fullmatch(rid): raise ConsistencyError("invalid_request")
    def opaque(name: str, optional=True):
        x = value.get(name)
        if x is None and optional: return None
        if not isinstance(x, str) or len(x) > 256 or not re.fullmatch(r"[A-Za-z0-9._:-]+", x): raise ConsistencyError("invalid_request")
        return x
    scope = value.get("scopeNodeIds", [])
    if not isinstance(scope, list) or len(scope) > 100 or len(set(scope)) != len(scope) or any(not isinstance(x, str) or not ID_PATTERN.fullmatch(x) for x in scope): raise ConsistencyError("query_limit_exceeded", 413)
    proposed = value.get("includeProposed", False); maximum = value.get("maxFindings", 200); threshold = value.get("failureThreshold", "error_only")
    if type(proposed) is not bool or type(maximum) is not int or not 1 <= maximum <= 200 or threshold not in {"error_only", "warning_or_error"}: raise ConsistencyError("invalid_request")
    return ConsistencyCheckRequest(rid, opaque("pullRequestId"), opaque("headSha"), opaque("baselineRevision"), tuple(scope), proposed, maximum, threshold)


def _clean(value: Any) -> Any:
    if isinstance(value, dict): return {str(k): _clean(v) for k, v in sorted(value.items()) if str(k) not in {"title", "body", "text", "email", "token", "password", "secret", "path"}}
    if isinstance(value, (list, tuple)): return [_clean(v) for v in value]
    return value


def fingerprint(rule_id: str, subject_id: str, expected: str, basis: str | None = None, relation_key: str | None = None) -> str:
    payload = {"rule": rule_id, "subject": subject_id, "expected": expected, "basis": basis or "", "relation": relation_key or ""}
    return "sha256:" + hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass
class ConsistencyReport:
    status: str
    run_id: str
    basis: dict[str, Any]
    findings: list[dict[str, Any]] = field(default_factory=list)
    candidate_evidence: list[dict[str, Any]] = field(default_factory=list)
    summary: dict[str, int] = field(default_factory=lambda: {"error": 0, "warning": 0, "info": 0, "suppressed": 0})
    meta: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"status": self.status, "runId": self.run_id, "basis": _clean(self.basis), "findings": _clean(self.findings), "candidateEvidence": _clean(self.candidate_evidence), "summary": dict(self.summary), "meta": _clean(self.meta)}

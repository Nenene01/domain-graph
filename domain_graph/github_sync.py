"""HTTP-independent GitHub snapshot boundary.

The client and credential provider are deliberately protocols: this module never
needs to know how a short-lived GitHub credential is obtained or how HTTP works.
Only allowlisted, redacted DTOs cross the boundary.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence
from urllib.parse import urlparse

from .ingest import InputValidationError, _safe_excerpt
from .audit import NullAudit, AuditSink


class CredentialProvider(Protocol):
    def get_credential(self, repository_id: str) -> str: ...


class GitHubClient(Protocol):
    """A read-only client. Implementations must not return raw API payloads."""
    def fetch_snapshot(self, repository_id: str, repository_url: str,
                       credential_provider: CredentialProvider | None = None,
                       *, updated_since: str | None = None) -> "GitHubSnapshot": ...


@dataclass(frozen=True)
class GitHubSnapshot:
    issues: tuple[dict[str, Any], ...] = ()
    pull_requests: tuple[dict[str, Any], ...] = ()
    commits: tuple[dict[str, Any], ...] = ()
    reviews: tuple[dict[str, Any], ...] = ()

    def as_dict(self) -> dict[str, list[dict[str, Any]]]:
        return {"issues": list(self.issues), "pullRequests": list(self.pull_requests),
                "commits": list(self.commits), "reviews": list(self.reviews)}


@dataclass(frozen=True)
class RetryClassification:
    category: str
    retryable: bool
    retry_after_seconds: int | None = None


def classify_response(status: int, headers: Mapping[str, str] | None = None) -> RetryClassification:
    headers = headers or {}
    if status == 429 or (status == 403 and ("retry-after" in {k.lower() for k in headers} or
                                             str(headers.get("X-RateLimit-Remaining", "")) == "0")):
        value = next((v for k, v in headers.items() if k.lower() == "retry-after"), None)
        try: delay = max(0, int(value)) if value is not None else None
        except (TypeError, ValueError): delay = None
        return RetryClassification("rate_limited", False, delay)
    if status in {408, 425} or 500 <= status <= 599:
        return RetryClassification("transient", True)
    if status in {401, 403}:
        return RetryClassification("unauthorized", False)
    if 400 <= status <= 499:
        return RetryClassification("client_error", False)
    return RetryClassification("ok", False)


def record_sync_audit(audit: AuditSink | None, *, request_id: str, repository_id: str,
                      sync_id: str, source_revision: str | None, sync_mode: str,
                      outcome: str, error_code: str | None = None, counts: Mapping[str, int] | None = None,
                      duration_ms: int | None = None, http_status: int | None = None) -> None:
    """Emit only the documented, non-sensitive github_sync fields."""
    event: dict[str, Any] = {"event": "github_sync", "requestId": request_id,
                             "repositoryId": repository_id, "syncId": sync_id,
                             "sourceRevision": source_revision, "syncMode": sync_mode,
                             "outcome": outcome}
    if error_code is not None: event["errorCode"] = error_code
    if counts is not None: event["counts"] = {k: int(v) for k, v in counts.items() if k in {"issues", "pullRequests", "commits", "reviews"}}
    if duration_ms is not None: event["durationMs"] = max(0, int(duration_ms))
    if http_status is not None and 100 <= int(http_status) <= 599: event["httpStatus"] = int(http_status)
    (audit or NullAudit()).record(event)


def _safe_url(value: Any) -> str:
    if not isinstance(value, str): raise ValueError("unsafe URL")
    parsed = urlparse(value)
    if parsed.scheme != "https" or parsed.netloc != "github.com" or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("unsafe URL")
    if not re.fullmatch(r"/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", parsed.path.rstrip("/")):
        raise ValueError("unsafe URL")
    return "https://github.com" + parsed.path.rstrip("/")


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


_SNAPSHOT_FIELDS = {
    "issues": {"number", "databaseId", "url", "title", "state", "createdAt", "updatedAt", "closedAt", "labels"},
    "pullRequests": {"number", "databaseId", "url", "title", "state", "createdAt", "updatedAt", "mergedAt", "closedAt", "headSha", "mergeCommitSha", "linkedIssueNumbers"},
    "commits": {"sha", "url", "committedAt", "parentShas", "pullRequestNumbers"},
    "reviews": {"databaseId", "pullRequestNumber", "url", "state", "submittedAt", "commitSha"},
}


def _allowlisted_snapshot(value: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    if set(value) != set(_SNAPSHOT_FIELDS) or any(not isinstance(value[k], (list, tuple)) for k in _SNAPSHOT_FIELDS):
        raise InputValidationError("snapshot does not match allowlist", code="invalid_github_snapshot")
    result: dict[str, list[dict[str, Any]]] = {}
    for category, items in _SNAPSHOT_FIELDS.items():
        result[category] = []
        for item in value[category]:
            if not isinstance(item, Mapping) or set(item) != items:
                raise InputValidationError("snapshot does not match allowlist", code="invalid_github_snapshot")
            item = dict(item)
            if "title" in item: _safe_excerpt(item["title"], Path("snapshot.json"), "title")
            result[category].append(item)
    return result


def _safe_sync_id(value: str) -> bool:
    return bool(re.fullmatch(r"SYNC-[0-9]{8}T[0-9]{6}Z-[0-9]{6}", value))


class AtomicSnapshotPublisher:
    """Publish snapshot and manifest without exposing a partial directory."""
    def publish(self, root: str | Path, *, repository_id: str, repository_url: str,
                sync_id: str, snapshot: GitHubSnapshot | Mapping[str, Any],
                retrieved_at: str, updated_by: str = "github-api",
                approval_status: str = "proposed", sync_mode: str = "incremental",
                window: Mapping[str, str] | None = None) -> Path:
        if not re.fullmatch(r"[A-Z0-9]+(?:-[A-Z0-9]+)+", repository_id) or not _safe_sync_id(sync_id):
            raise InputValidationError("invalid sync identifier", code="invalid_manifest")
        try: url = _safe_url(repository_url)
        except ValueError as exc: raise InputValidationError("invalid repository URL", code="invalid_manifest") from exc
        if approval_status not in {"proposed", "approved"} or not re.fullmatch(r"[^\r\n]{1,80}", updated_by):
            raise InputValidationError("invalid manifest field", code="invalid_manifest")
        payload = _allowlisted_snapshot(snapshot.as_dict() if isinstance(snapshot, GitHubSnapshot) else dict(snapshot))
        raw = _json_bytes(payload); revision = "sha256:" + hashlib.sha256(raw).hexdigest()
        manifest = {"schemaVersion": "1.0", "syncId": sync_id, "repositoryId": repository_id,
                    "repositoryUrl": url, "snapshotFile": "snapshot.json", "sourceRevision": revision,
                    "retrievedAt": retrieved_at, "updatedBy": updated_by, "approvalStatus": approval_status,
                    "syncMode": sync_mode}
        if window is not None: manifest["window"] = dict(window)
        root = Path(root); destination = root / repository_id / sync_id
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            existing = destination / "manifest.json"
            if existing.exists() and json.loads(existing.read_text(encoding="utf-8")).get("sourceRevision") == revision:
                return destination
            raise InputValidationError("snapshot already exists", code="conflicting_record")
        temp = Path(tempfile.mkdtemp(prefix=f".{sync_id}-", dir=str(destination.parent)))
        try:
            for name, data in (("snapshot.json", raw), ("manifest.json", _json_bytes(manifest))):
                path = temp / name
                with path.open("wb") as fh:
                    fh.write(data); fh.flush(); os.fsync(fh.fileno())
            os.replace(temp, destination)
            fd = os.open(destination.parent, os.O_RDONLY)
            try: os.fsync(fd)
            finally: os.close(fd)
        except Exception:
            # The temporary directory is intentionally left recoverable; it is not
            # reachable by the loader because only SYNC-* directories are scanned.
            raise
        return destination

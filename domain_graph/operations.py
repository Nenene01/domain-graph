"""Closed-environment operational primitives.

These interfaces deliberately contain no cloud-provider, IdP, region, network,
retention, or key identifiers.  Deployments must bind them from an approved
environment manifest at the edge.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import re
from typing import Any, Callable, Mapping, Protocol


class ConfigurationError(ValueError):
    pass


REQUIRED_DEPLOYMENT_FIELDS = frozenset({
    "environment", "cloud", "region", "network_cidr", "idp_issuer",
    "idp_audience", "kms_key_ref", "secret_store_ref", "audit_sink_ref",
    "backup_catalog_ref", "retention_policy_ref", "artifact_digest",
})


@dataclass(frozen=True)
class DeploymentConfig:
    values: Mapping[str, str]
    digest: str

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any], *, production: bool = False) -> "DeploymentConfig":
        if not isinstance(raw, Mapping):
            raise ConfigurationError("deployment configuration must be an object")
        missing = sorted(REQUIRED_DEPLOYMENT_FIELDS - raw.keys())
        if missing:
            raise ConfigurationError("missing deployment decisions: " + ",".join(missing))
        values = {}
        for key in REQUIRED_DEPLOYMENT_FIELDS:
            value = raw.get(key)
            if not isinstance(value, str) or not value.strip() or value.strip().lower() in {"todo", "tbd", "change-me", "unknown", "未決定"}:
                raise ConfigurationError(f"invalid or undecided deployment value: {key}")
            values[key] = value.strip()
        if production and not re.fullmatch(r"sha256:[0-9a-f]{64}", values["artifact_digest"]):
            raise ConfigurationError("production requires an immutable sha256 artifact digest")
        encoded = json.dumps(values, sort_keys=True, separators=(",", ":")).encode()
        import hashlib
        return cls(values, "sha256:" + hashlib.sha256(encoded).hexdigest())


class SecretProvider(Protocol):
    def get(self, reference: str) -> str: ...


class SecretInjector:
    """Resolve references at runtime; never serializes or logs secret values."""
    def __init__(self, provider: SecretProvider): self.provider = provider
    def inject(self, references: Mapping[str, str]) -> Mapping[str, str]:
        if not references or any(not isinstance(k, str) or not isinstance(v, str) or not v.strip() for k, v in references.items()):
            raise ConfigurationError("secret references are required")
        try:
            result = {key: self.provider.get(ref) for key, ref in references.items()}
        except Exception as exc:
            raise ConfigurationError("secret provider unavailable") from exc
        if any(not isinstance(v, str) or not v for v in result.values()):
            raise ConfigurationError("secret provider returned an invalid value")
        return result


class RedactingLogger:
    """Allowlist logger. Values named sensitive are never emitted."""
    ALLOWED = frozenset({"requestId", "subjectId", "tenantId", "projectId", "route", "tool", "outcome", "status", "durationMs", "count", "errorCode", "releaseDigest", "configDigest", "eventType"})
    def __init__(self, sink: Callable[[str], None]): self.sink = sink
    def info(self, event: Mapping[str, Any]) -> None:
        clean = {k: v for k, v in event.items() if k in self.ALLOWED and isinstance(v, (str, int, float, bool, type(None)))}
        self.sink(json.dumps(clean, ensure_ascii=False, separators=(",", ":")))


class DurableAuditSink(Protocol):
    def enqueue(self, event: Mapping[str, Any]) -> None: ...
    def available(self) -> bool: ...


class DurableAuditAdapter:
    def __init__(self, sink: DurableAuditSink): self.sink = sink
    def record(self, event: Mapping[str, Any]) -> None:
        if not self.sink.available(): raise RuntimeError("audit sink unavailable")
        self.sink.enqueue(dict(event))


@dataclass
class HealthState:
    migration_ok: bool = False
    secret_ok: bool = False
    database_ok: bool = False
    audit_ok: bool = False
    verifier_ok: bool = False
    stopping: bool = False
    def liveness(self) -> bool: return not self.stopping
    def readiness(self) -> bool:
        return not self.stopping and all((self.migration_ok, self.secret_ok, self.database_ok, self.audit_ok, self.verifier_ok))


class GracefulShutdown:
    def __init__(self, stop_accepting: Callable[[], None], close: Callable[[], None]):
        self.stop_accepting, self.close = stop_accepting, close
        self.started = False
    def shutdown(self) -> None:
        if self.started: return
        self.started = True
        self.stop_accepting()
        self.close()


class Migration(Protocol):
    version: int
    def apply(self, store: Any) -> None: ...
    def verify(self, store: Any) -> bool: ...


class MigrationRunner:
    """Runs ordered migrations and stops on the first failure."""
    def __init__(self, migrations: list[Migration], history: set[int] | None = None):
        self.migrations = sorted(migrations, key=lambda m: m.version); self.history = history if history is not None else set()
    def run(self, store: Any) -> list[int]:
        applied = []
        for migration in self.migrations:
            if migration.version in self.history: continue
            migration.apply(store)
            if not migration.verify(store): raise RuntimeError(f"migration {migration.version} verification failed")
            self.history.add(migration.version); applied.append(migration.version)
        return applied


@dataclass(frozen=True)
class BackupCatalogEntry:
    backup_id: str
    digest: str
    migration_version: int
    artifact_digest: str
    created_at: str


def validate_backup_catalog(entry: Mapping[str, Any]) -> BackupCatalogEntry:
    required = {"backup_id", "digest", "migration_version", "artifact_digest", "created_at"}
    if not required <= entry.keys() or any(not entry.get(k) for k in required):
        raise ConfigurationError("backup catalog entry is incomplete")
    if not isinstance(entry["migration_version"], int) or entry["migration_version"] < 0:
        raise ConfigurationError("invalid backup migration version")
    for key in ("digest", "artifact_digest"):
        if not isinstance(entry[key], str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", entry[key]):
            raise ConfigurationError("backup digests must be immutable sha256 values")
    try: datetime.fromisoformat(str(entry["created_at"]).replace("Z", "+00:00"))
    except ValueError as exc: raise ConfigurationError("invalid backup timestamp") from exc
    return BackupCatalogEntry(str(entry["backup_id"]), entry["digest"], entry["migration_version"], entry["artifact_digest"], entry["created_at"])

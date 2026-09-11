import unittest
from domain_graph.operations import (BackupCatalogEntry, ConfigurationError, DeploymentConfig,
    DurableAuditAdapter, GracefulShutdown, HealthState, MigrationRunner, RedactingLogger, SecretInjector,
    validate_backup_catalog)

class SecretProvider:
    def get(self, ref): return "fixture-secret" if ref == "ref://fixture" else (_ for _ in ()).throw(RuntimeError())

class Migration:
    def __init__(self, version, ok=True): self.version, self.ok, self.calls = version, ok, 0
    def apply(self, store): self.calls += 1
    def verify(self, store): return self.ok

class Sink:
    def __init__(self, available=True): self.available_value, self.events = available, []
    def available(self): return self.available_value
    def enqueue(self, event): self.events.append(event)

class OperationsTests(unittest.TestCase):
    def config(self):
        return {k: ("sha256:" + "a" * 64 if k == "artifact_digest" else "fixture-value") for k in __import__('domain_graph.operations', fromlist=['REQUIRED_DEPLOYMENT_FIELDS']).REQUIRED_DEPLOYMENT_FIELDS}
    def test_undecided_and_production_digest_rejected(self):
        with self.assertRaises(ConfigurationError): DeploymentConfig.from_mapping({"environment": "production"})
        raw = self.config(); raw["artifact_digest"] = "latest"
        with self.assertRaises(ConfigurationError): DeploymentConfig.from_mapping(raw, production=True)
    def test_secret_not_logged_and_injection_failure_closed(self):
        self.assertEqual(SecretInjector(SecretProvider()).inject({"DB_PASSWORD": "ref://fixture"})["DB_PASSWORD"], "fixture-secret")
        lines = []; RedactingLogger(lines.append).info({"eventType":"test", "secret":"fixture-secret", "requestId":"r"})
        self.assertNotIn("fixture-secret", lines[0]); self.assertNotIn("secret", lines[0])
        with self.assertRaises(ConfigurationError): SecretInjector(SecretProvider()).inject({"DB_PASSWORD": ""})
    def test_readiness_fail_closed_and_shutdown_once(self):
        health = HealthState(secret_ok=True, database_ok=True, migration_ok=True, audit_ok=True)
        self.assertFalse(health.readiness()); health.verifier_ok = True; self.assertTrue(health.readiness())
        calls = []; shutdown = GracefulShutdown(lambda: calls.append("stop"), lambda: calls.append("close")); shutdown.shutdown(); shutdown.shutdown()
        self.assertEqual(calls, ["stop", "close"])
    def test_migration_is_idempotent_and_stops_on_verify_failure(self):
        first, second = Migration(1), Migration(2, False); runner = MigrationRunner([second, first])
        with self.assertRaises(RuntimeError): runner.run(object())
        self.assertEqual(first.calls, 1); self.assertEqual(runner.run if False else runner.history, {1})
        self.assertEqual(MigrationRunner([first], runner.history).run(object()), [])
    def test_backup_catalog_requires_digests_and_version(self):
        good = {"backup_id":"b-1", "digest":"sha256:"+"b"*64, "migration_version":1, "artifact_digest":"sha256:"+"a"*64, "created_at":"2026-01-01T00:00:00Z"}
        self.assertIsInstance(validate_backup_catalog(good), BackupCatalogEntry)
        good["digest"] = "fixture" 
        with self.assertRaises(ConfigurationError): validate_backup_catalog(good)
    def test_audit_sink_is_durable_or_fails(self):
        sink = Sink(); DurableAuditAdapter(sink).record({"eventType":"test"}); self.assertEqual(len(sink.events), 1)
        with self.assertRaises(RuntimeError): DurableAuditAdapter(Sink(False)).record({"eventType":"test"})

if __name__ == "__main__": unittest.main()

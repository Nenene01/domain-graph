import tempfile
import unittest
from pathlib import Path

from domain_graph.github_import import load_github_snapshot
from domain_graph.github_sync import AtomicSnapshotPublisher, GitHubSnapshot, classify_response, record_sync_audit
from domain_graph.audit import MemoryAudit


class GitHubSyncTests(unittest.TestCase):
    def test_atomic_manifest_digest_and_idempotent_publish(self):
        with tempfile.TemporaryDirectory() as root:
            kwargs = dict(root=root, repository_id="GH-ACME-REPO", repository_url="https://github.com/acme/repo",
                          sync_id="SYNC-20260912T010203Z-000001", snapshot=GitHubSnapshot(), retrieved_at="2026-09-12T01:02:03Z")
            first = AtomicSnapshotPublisher().publish(**kwargs)
            second = AtomicSnapshotPublisher().publish(**kwargs)
            self.assertEqual(first, second)
            self.assertEqual(len(load_github_snapshot(first)), 1)
            self.assertTrue((Path(first) / "manifest.json").exists())

    def test_rate_limit_classification_and_safe_audit(self):
        self.assertEqual(classify_response(429, {"Retry-After": "3"}).category, "rate_limited")
        audit = MemoryAudit()
        record_sync_audit(audit, request_id="req-1", repository_id="GH-ACME-REPO", sync_id="SYNC-20260912T010203Z-000001",
                          source_revision=None, sync_mode="incremental", outcome="rate_limited", error_code="rate_limited",
                          http_status=429)
        self.assertEqual(audit.events[0]["event"], "github_sync")
        self.assertNotIn("Authorization", repr(audit.events))


if __name__ == "__main__":
    unittest.main()

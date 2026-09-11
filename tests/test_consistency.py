import unittest

from domain_graph.audit import MemoryAudit
from domain_graph.consistency_contract import ConsistencyCheckRequest, ConsistencyError, fingerprint, parse_request
from domain_graph.consistency_repository import InMemoryConsistencyRepository
from domain_graph.consistency_service import ConsistencyService
from domain_graph.ingest import InMemoryGraph
from domain_graph.pr_review_report import GitHubPublisher, render_markdown, render_json


def record(node_id, kind, status="approved", method="human_curated", relations=()):
    provenance = {"source_id": node_id, "source_locator": "inputs/fixture.json", "source_anchor": "$",
                  "retrieved_at": "2026-01-01T00:00:00Z", "updated_by": "fixture", "observed_at": "2026-01-01T00:00:00Z"}
    return {"id": node_id, "title": "safe fixture", "type": kind, "approvalStatus": status,
            "extractionMethod": method, "confidence": 1.0, "evidenceExcerpt": "safe evidence",
            "provenance": provenance, "relations": list(relations)}


class ConsistencyTests(unittest.TestCase):
    def graph(self):
        g = InMemoryGraph()
        g.ingest([record("REQ-1", "Requirement"), record("DSN-1", "Design", relations=[{"type": "IMPLEMENTS", "to": "REQ-1", "evidenceExcerpt": "safe"}]), record("CODE-1", "Code", relations=[{"type": "IMPLEMENTS", "to": "DSN-1", "evidenceExcerpt": "safe"}])])
        return g

    def test_rules_and_stable_fingerprint(self):
        service = ConsistencyService(InMemoryConsistencyRepository(self.graph()))
        report = service.check(ConsistencyCheckRequest("REPO-1"))
        self.assertEqual(report.summary["warning"], 0)
        self.assertEqual(report.as_dict(), service.check(ConsistencyCheckRequest("REPO-1")).as_dict())
        self.assertEqual(fingerprint("x", "REQ-1", "y"), fingerprint("x", "REQ-1", "y"))

    def test_candidate_does_not_clear_finding_and_not_registered(self):
        g = InMemoryGraph(); g.ingest([record("REQ-1", "Requirement"), record("DSN-CANDIDATE", "Design", "proposed", "ai_inferred", [{"type": "IMPLEMENTS", "to": "REQ-1", "evidenceExcerpt": "safe"}])])
        service = ConsistencyService(InMemoryConsistencyRepository(g))
        report = service.check(ConsistencyCheckRequest("REPO-1", include_proposed=True))
        self.assertGreater(report.summary["warning"], 0)
        self.assertEqual(report.candidate_evidence[0]["state"], "candidate")
        missing = service.check(ConsistencyCheckRequest("REPO-1", scope_node_ids=("REQ-UNKNOWN",)))
        self.assertEqual(missing.status, "not_registered")

    def test_safe_error_tenant_and_publisher_deny(self):
        with self.assertRaises(ConsistencyError) as caught:
            ConsistencyService(type("R", (), {"tenant_aware": False})()).check(ConsistencyCheckRequest("REPO-1"))
        self.assertEqual(caught.exception.code, "forbidden")
        with self.assertRaises(PermissionError): GitHubPublisher().publish("REPO-1")

    def test_report_does_not_contain_secret_or_absolute_path(self):
        report = ConsistencyService(InMemoryConsistencyRepository(self.graph())).check(ConsistencyCheckRequest("REPO-1"))
        rendered = render_json(report) + render_markdown(report)
        self.assertNotIn("/Users/", rendered); self.assertNotIn("token", rendered.lower())


if __name__ == "__main__": unittest.main()

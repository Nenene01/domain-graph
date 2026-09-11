import unittest

from domain_graph.ingest import InMemoryGraph, InputValidationError, load_inputs
from domain_graph.query import trace


def graph():
    g = InMemoryGraph()
    g.ingest(load_inputs("inputs"))
    return g


class IngestTests(unittest.TestCase):
  def test_trace_has_provenance_and_expected_ids(self):
    result = trace(graph(), "REQ-ORDER-001", "CODE-ORDER-001")
    ids = {n["id"] for n in result["nodes"]}
    self.assertEqual(result["status"], "ok")
    self.assertTrue({"REQ-ORDER-001", "DEC-ORDER-001", "MTG-2026-001", "DSN-ORDER-001", "CODE-ORDER-001"} <= ids)
    self.assertTrue(all(edge["provenance"]["source_id"] for edge in result["relations"]))


  def test_ingest_is_idempotent(self):
    g = graph(); before = (len(g.nodes), len(g.edges))
    g.ingest(load_inputs("inputs"))
    self.assertEqual((len(g.nodes), len(g.edges)), before)


  def test_rollback_on_unknown_target(self):
    g = graph(); before = (dict(g.nodes), dict(g.edges))
    bad = [{"id": "TEMP", "title": "temporary", "type": "Requirement", "approved": False, "provenance": {}, "relations": [{"type": "IMPLEMENTS", "to": "MISSING"}]}]
    with self.assertRaises(InputValidationError):
        g.ingest(bad)
    self.assertEqual(g.nodes, before[0]); self.assertEqual(g.edges, before[1])


  def test_missing_and_insufficient_statuses(self):
    g = graph()
    self.assertEqual(trace(g, "NO-SUCH-ID")["status"], "not_registered")
    self.assertEqual(trace(g, "REQ-ORDER-001", "NO-SUCH-ID")["status"], "evidence_insufficient")


if __name__ == "__main__":
  unittest.main()

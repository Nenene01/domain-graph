import json
import tempfile
import unittest
from pathlib import Path

from domain_graph.ingest import InMemoryGraph, InputValidationError, load_inputs, neo4j_statements, normalize_inputs
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

  def test_schema_accepts_all_relation_vocabulary_and_flattens_provenance(self):
    records = [{"id": "A", "title": "A", "type": "Requirement", "approved": True,
                "relations": [],
                "provenance": {"source_id": "A", "updated_by": "test", "retrieved_at": "2026-01-01T00:00:00Z"}},
               {"id": "B", "title": "B", "type": "Design", "approved": True,
                "relations": [], "provenance": {"source_id": "B", "updated_by": "test", "retrieved_at": "2026-01-01T00:00:00Z"}}]
    # Exercise each vocabulary entry using a separate source node and target.
    records[0]["relations"] = [{"type": relation, "to": "B"} for relation in
                                 ("DEFINED_BY", "RELATES_TO", "IMPLEMENTS", "VERIFIES", "BLOCKS", "CHANGED_BY", "DECIDED_IN", "DERIVED_FROM")]
    nodes, edges = normalize_inputs(records)
    statements = neo4j_statements(nodes, edges)
    self.assertEqual(len(edges), 8)
    self.assertTrue(all("provenance" not in query for query, _ in statements))
    self.assertTrue(all("sourceId" in query and "evidenceExcerpt" in query for query, _ in statements[4:]))

  def test_ai_inference_is_proposed_and_sensitive_evidence_is_rejected(self):
    with tempfile.TemporaryDirectory() as directory:
      root = Path(directory)
      for source in ("meeting-notes", "requirements", "design", "source-metadata"):
        (root / source).mkdir()
      value = {"id": "AI-1", "title": "推論", "type": "Requirement", "approved": True,
               "extractionMethod": "ai_inferred", "approvalStatus": "approved", "relations": [], "provenance": {}}
      (root / "requirements" / "AI-1.json").write_text(json.dumps(value), encoding="utf-8")
      with self.assertRaises(InputValidationError):
        load_inputs(root)
    g = InMemoryGraph()
    g.ingest([{"id": "A", "title": "A", "type": "Requirement", "approvalStatus": "proposed", "extractionMethod": "ai_inferred", "relations": [{"type": "RELATES_TO", "to": "B"},], "provenance": {}}, {"id": "B", "title": "B", "type": "Design", "approvalStatus": "approved", "relations": [], "provenance": {}}])
    self.assertEqual(trace(g, "A", "B")["status"], "evidence_insufficient")


if __name__ == "__main__":
  unittest.main()

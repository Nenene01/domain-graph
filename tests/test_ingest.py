import json
import hashlib
import os
import tempfile
import unittest
from pathlib import Path

from domain_graph.ingest import InMemoryGraph, InputValidationError, load_inputs, load_issue_tracker, neo4j_statements, normalize_inputs
from domain_graph.query import trace


def graph():
    g = InMemoryGraph()
    g.ingest(load_inputs("inputs"))
    return g


class IngestTests(unittest.TestCase):
  def _tracker(self, directory, raw, fmt="csv", **overrides):
    export = directory / "SRC-TRACKER-TEST"
    export.mkdir()
    filename = "original." + fmt
    (export / filename).write_bytes(raw)
    mapping = {"issueId": "id", "title": "title", "status": "status", "dueDate": "due", "assigneeId": "owner", "requirementIds": "requirements"}
    manifest = {"schemaVersion": "1.0", "exportId": export.name, "originalFile": filename, "format": fmt,
                "encoding": "utf-8", "sourceRevision": "sha256:" + hashlib.sha256(raw).hexdigest(),
                "retrievedAt": "2026-09-12T00:00:00Z", "observedAt": "2026-09-12T00:00:00Z",
                "updatedBy": "fixture-exporter", "approvalStatus": "approved", "statusMap": {"open": "open", "done": "done"},
                "requirementIdSeparator": ";"}
    manifest["columnMap" if fmt == "csv" else "recordMap"] = mapping
    if fmt == "json": manifest["recordsPath"] = "$.issues"
    manifest.update(overrides)
    (export / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return export

  def test_issue_tracker_csv_preserves_source_and_normalizes_relations(self):
    raw = "id,title,status,due,owner,requirements\nISSUE-TEST-001,確認する,open,2026-10-01,TEAM-TEST,REQ-ORDER-001\n".encode()
    with tempfile.TemporaryDirectory() as directory:
      export = self._tracker(Path(directory), raw)
      before = (hashlib.sha256((export / "original.csv").read_bytes()).hexdigest(), os.stat(export / "original.csv").st_mtime_ns)
      records = load_issue_tracker(export)
      issue = next(r for r in records if r["type"] == "Issue")
      self.assertEqual(issue["issueStatus"], "open")
      self.assertEqual(issue["dueDate"], "2026-10-01")
      self.assertEqual({(r["type"], r["to"]) for r in issue["relations"]}, {("DERIVED_FROM", export.name), ("RELATES_TO", "TEAM-TEST"), ("RELATES_TO", "REQ-ORDER-001")})
      self.assertEqual(before, (hashlib.sha256((export / "original.csv").read_bytes()).hexdigest(), os.stat(export / "original.csv").st_mtime_ns))

  def test_issue_tracker_json_and_safe_errors(self):
    raw = json.dumps({"issues": [{"key": "ISSUE-TEST-001", "summary": "確認する", "state": "done", "due": "", "owner": "", "req": ""}]}).encode()
    with tempfile.TemporaryDirectory() as directory:
      export = self._tracker(Path(directory), raw, "json")
      manifest = json.loads((export / "manifest.json").read_text())
      manifest["recordMap"]["issueId"] = "missing"
      (export / "manifest.json").write_text(json.dumps(manifest))
      with self.assertRaises(InputValidationError) as caught: load_issue_tracker(export)
      self.assertEqual(caught.exception.as_dict()["code"], "missing_required_value")
      self.assertNotIn("確認する", str(caught.exception))

  def test_issue_tracker_rejects_duplicate_and_pii(self):
    raw = b"id,title,status,due,owner,requirements\nISSUE-TEST-001,One,open,,,\nISSUE-TEST-001,Two,open,,,\n"
    with tempfile.TemporaryDirectory() as directory:
      export = self._tracker(Path(directory), raw)
      with self.assertRaises(InputValidationError) as caught: load_issue_tracker(export)
      self.assertEqual(caught.exception.as_dict()["code"], "duplicate_record_id")
      raw = b"id,title,status,due,owner,requirements\nISSUE-TEST-001,One,open,,person@example.invalid,\n"
      (export / "original.csv").write_bytes(raw)
      manifest = json.loads((export / "manifest.json").read_text()); manifest["sourceRevision"] = "sha256:" + hashlib.sha256(raw).hexdigest()
      (export / "manifest.json").write_text(json.dumps(manifest))
      with self.assertRaises(InputValidationError) as caught: load_issue_tracker(export)
      self.assertEqual(caught.exception.as_dict()["code"], "pii_or_unsafe_assignee")
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

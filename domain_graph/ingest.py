"""Validate, normalize and load the four MVP input sources."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SOURCES = {"meeting-notes", "requirements", "design", "source-metadata"}
ALLOWED_RELATIONS = {
    "DEFINED_BY", "RELATES_TO", "IMPLEMENTS", "VERIFIES", "BLOCKS",
    "CHANGED_BY", "DECIDED_IN", "DERIVED_FROM",
}
APPROVAL_STATUSES = {"approved", "proposed", "rejected", "superseded"}
EXTRACTION_METHODS = {"human_authored", "human_curated", "deterministic_import", "ai_inferred"}


class InputValidationError(ValueError):
    pass


def _provenance(source: str, path: Path, value: dict[str, Any]) -> dict[str, Any]:
    p = value.get("provenance") or {}
    source_id = p.get("source_id", value["id"])
    retrieved_at = p.get("retrieved_at", datetime.now(timezone.utc).isoformat())
    source_locator = p.get("source_locator", str(path))
    # Keep the snake_case map for the Python MVP while exposing the schema's
    # canonical names for persistence adapters and callers.
    return {
        "source_type": source,
        "source_path": str(path),
        "source_id": source_id,
        "source_locator": source_locator,
        "source_anchor": p.get("source_anchor", "$"),
        "source_revision": p.get("source_revision"),
        "retrieved_at": retrieved_at,
        "updated_by": p.get("updated_by", "sample"),
        "observed_at": p.get("observed_at", retrieved_at),
        "extraction_method": p.get("extraction_method", value.get("extractionMethod", "deterministic_import")),
        "confidence": p.get("confidence", value.get("confidence", 1.0)),
        "evidence_excerpt": p.get("evidence_excerpt", value.get("evidenceExcerpt", value["title"])),
    }


def _validate(value: Any, source: str, path: Path) -> dict[str, Any]:
    if not isinstance(value, dict) or not value.get("id") or not value.get("title"):
        raise InputValidationError(f"{path}: id and title are required")
    if value.get("type") and not isinstance(value["type"], str):
        raise InputValidationError(f"{path}: type must be a string")
    for relation in value.get("relations", []):
        if not isinstance(relation, dict) or relation.get("type") not in ALLOWED_RELATIONS or not relation.get("to"):
            raise InputValidationError(f"{path}: invalid relation")
    value = dict(value)
    value["type"] = value.get("type") or {
        "meeting-notes": "Meeting",
        "requirements": "Requirement",
        "design": "Design",
        "source-metadata": "Code",
    }[source]
    value["provenance"] = _provenance(source, path, value)
    value["approved"] = bool(value.get("approved", False))
    value["approvalStatus"] = value.get("approvalStatus", "approved" if value["approved"] else "proposed")
    value["extractionMethod"] = value.get("extractionMethod", value["provenance"]["extraction_method"])
    value["confidence"] = value.get("confidence", value["provenance"]["confidence"])
    value["evidenceExcerpt"] = value.get("evidenceExcerpt", value["provenance"]["evidence_excerpt"])
    if value["approvalStatus"] not in APPROVAL_STATUSES:
        raise InputValidationError(f"{path}: invalid approvalStatus")
    if value["extractionMethod"] not in EXTRACTION_METHODS:
        raise InputValidationError(f"{path}: invalid extractionMethod")
    try:
        value["confidence"] = float(value["confidence"])
    except (TypeError, ValueError) as exc:
        raise InputValidationError(f"{path}: confidence must be numeric") from exc
    if not 0.0 <= value["confidence"] <= 1.0:
        raise InputValidationError(f"{path}: confidence must be between 0 and 1")
    if value["extractionMethod"] == "ai_inferred" and value["approvalStatus"] != "proposed":
        raise InputValidationError(f"{path}: ai_inferred records must be proposed")
    if not isinstance(value["evidenceExcerpt"], str) or not value["evidenceExcerpt"].strip():
        raise InputValidationError(f"{path}: evidenceExcerpt is required")
    # Reject common secret/PII forms at the ingestion boundary; raw source text
    # must remain outside the graph and logs.
    import re
    if re.search(r"(?i)(password|token|secret)\s*[:=]", value["evidenceExcerpt"]) or re.search(r"\b[^\s@]+@[^\s@]+\.[^\s@]+\b", value["evidenceExcerpt"]):
        raise InputValidationError(f"{path}: evidenceExcerpt contains secret or personal data")
    return value


def load_inputs(root: str | Path) -> list[dict[str, Any]]:
    root = Path(root)
    records = []
    for source in sorted(SOURCES):
        directory = root / source
        if not directory.exists():
            raise InputValidationError(f"missing input directory: {directory}")
        for path in sorted(directory.glob("*.json")):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise InputValidationError(f"{path}: invalid JSON") from exc
            records.append(_validate(value, source, path))
    if not records:
        raise InputValidationError(f"no JSON inputs in {root}")
    return records


def normalize_inputs(records: Iterable[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    nodes: dict[str, dict[str, Any]] = {}
    edges: dict[tuple[str, str, str], dict[str, Any]] = {}
    for record in records:
        provenance = record.get("provenance") or {}
        approval_status = record.get("approvalStatus", "approved" if record.get("approved", False) else "proposed")
        extraction_method = record.get("extractionMethod", "deterministic_import")
        confidence = float(record.get("confidence", 1.0))
        evidence_excerpt = record.get("evidenceExcerpt", record["title"])
        node = {"id": record["id"], "type": record["type"], "title": record["title"], "approved": bool(record.get("approved", approval_status == "approved")), "approvalStatus": approval_status, "extractionMethod": extraction_method, "confidence": confidence, "evidenceExcerpt": evidence_excerpt, "provenance": provenance}
        nodes[record["id"]] = node
        for relation in record.get("relations", []):
            key = (record["id"], relation["type"], relation["to"])
            edges[key] = {"from": key[0], "type": key[1], "to": key[2], "approvalStatus": approval_status, "extractionMethod": extraction_method, "confidence": confidence, "evidenceExcerpt": evidence_excerpt, "provenance": provenance}
    return list(nodes.values()), list(edges.values())


class InMemoryGraph:
    """Deterministic graph double used by tests and local demos."""

    def __init__(self) -> None:
        self.nodes: dict[str, dict[str, Any]] = {}
        self.edges: dict[tuple[str, str, str], dict[str, Any]] = {}

    def ingest(self, records: Iterable[dict[str, Any]]) -> None:
        nodes, edges = normalize_inputs(records)
        old_nodes, old_edges = self.nodes.copy(), self.edges.copy()
        try:
            for node in nodes:
                self.nodes[node["id"]] = node
            for edge in edges:
                if edge["to"] not in self.nodes:
                    raise InputValidationError(f"unregistered target: {edge['to']}")
                self.edges[(edge["from"], edge["type"], edge["to"])] = edge
        except Exception:
            self.nodes, self.edges = old_nodes, old_edges
            raise

    def trace(self, start: str, target: str | None = None) -> dict[str, Any]:
        if start not in self.nodes:
            return {"status": "not_registered", "nodes": [], "relations": []}
        found = {start}
        queue = [start]
        path_edges = []
        while queue:
            current = queue.pop(0)
            for edge in self.edges.values():
                # Proposed AI assertions remain queryable for review, but do
                # not count as approved evidence in trace results.
                if edge.get("approvalStatus", "approved") != "approved" or edge.get("extractionMethod") == "ai_inferred":
                    continue
                neighbour = edge["to"] if edge["from"] == current else edge["from"] if edge["to"] == current else None
                if neighbour and neighbour not in found:
                    found.add(neighbour); queue.append(neighbour); path_edges.append(edge)
        if target and target not in found:
            return {"status": "evidence_insufficient", "nodes": [self.nodes[n] for n in found], "relations": path_edges}
        return {"status": "ok", "nodes": [self.nodes[n] for n in found], "relations": path_edges}


CONSTRAINTS = [
    "CREATE CONSTRAINT domain_node_id IF NOT EXISTS FOR (n:DomainNode) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT domain_edge_key IF NOT EXISTS FOR ()-[r:RELATION]-() REQUIRE r.key IS UNIQUE",
    "CREATE INDEX domain_node_type IF NOT EXISTS FOR (n:DomainNode) ON (n.type)",
    "CREATE INDEX relation_type IF NOT EXISTS FOR ()-[r:RELATION]-() ON (r.type)",
]


def neo4j_statements(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> list[tuple[str, dict[str, Any]]]:
    statements = [(query, {}) for query in CONSTRAINTS]
    for node in nodes:
        params = dict(node)
        params.update(node["provenance"])
        statements.append(("MERGE (n:DomainNode {id: $id}) ON CREATE SET n.createdAt=$createdAt SET n.type=$type, n.title=$title, n.approved=$approved, n.approvalStatus=$approvalStatus, n.extractionMethod=$extractionMethod, n.confidence=$confidence, n.evidenceExcerpt=$evidenceExcerpt, n.sourceId=$source_id, n.sourceType=$source_type, n.sourceLocator=$source_locator, n.sourceAnchor=$source_anchor, n.sourceRevision=$source_revision, n.retrievedAt=$retrieved_at, n.updatedBy=$updated_by, n.observedAt=$observed_at, n.updatedAt=$updatedAt", {**params, "createdAt": node["provenance"]["retrieved_at"], "updatedAt": node["provenance"]["retrieved_at"]}))
    for edge in edges:
        payload = dict(edge); payload["key"] = f"{edge['from']}|{edge['type']}|{edge['to']}"
        payload.update(edge["provenance"])
        statements.append(("MATCH (a:DomainNode {id:$from}), (b:DomainNode {id:$to}) MERGE (a)-[r:RELATION {key:$key}]->(b) SET r.type=$type, r.approvalStatus=$approvalStatus, r.extractionMethod=$extractionMethod, r.confidence=$confidence, r.evidenceExcerpt=$evidenceExcerpt, r.sourceId=$source_id, r.sourceType=$source_type, r.sourceLocator=$source_locator, r.sourceAnchor=$source_anchor, r.sourceRevision=$source_revision, r.retrievedAt=$retrieved_at, r.updatedBy=$updated_by, r.observedAt=$observed_at, r.updatedAt=$updatedAt", {**payload, "updatedAt": edge["provenance"]["retrieved_at"]}))
    return statements

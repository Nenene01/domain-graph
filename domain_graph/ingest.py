"""Validate, normalize and load the four MVP input sources."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SOURCES = {"meeting-notes", "requirements", "design", "source-metadata"}
ALLOWED_RELATIONS = {"DECIDED_IN", "DERIVED_FROM", "IMPLEMENTS", "VERIFIES", "RELATES_TO"}


class InputValidationError(ValueError):
    pass


def _provenance(source: str, path: Path, value: dict[str, Any]) -> dict[str, Any]:
    p = value.get("provenance") or {}
    return {
        "source_type": source,
        "source_path": str(path),
        "source_id": p.get("source_id", value["id"]),
        "retrieved_at": p.get("retrieved_at", datetime.now(timezone.utc).isoformat()),
        "updated_by": p.get("updated_by", "sample"),
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
        node = {k: record[k] for k in ("id", "type", "title", "approved", "provenance")}
        nodes[record["id"]] = node
        for relation in record.get("relations", []):
            key = (record["id"], relation["type"], relation["to"])
            edges[key] = {"from": key[0], "type": key[1], "to": key[2], "provenance": record["provenance"]}
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
                neighbour = edge["to"] if edge["from"] == current else edge["from"] if edge["to"] == current else None
                if neighbour and neighbour not in found:
                    found.add(neighbour); queue.append(neighbour); path_edges.append(edge)
        if target and target not in found:
            return {"status": "evidence_insufficient", "nodes": [self.nodes[n] for n in found], "relations": path_edges}
        return {"status": "ok", "nodes": [self.nodes[n] for n in found], "relations": path_edges}


CONSTRAINTS = [
    "CREATE CONSTRAINT domain_node_id IF NOT EXISTS FOR (n:DomainNode) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT domain_edge_key IF NOT EXISTS FOR ()-[r:RELATION]-() REQUIRE r.key IS UNIQUE",
]


def neo4j_statements(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> list[tuple[str, dict[str, Any]]]:
    statements = [(query, {}) for query in CONSTRAINTS]
    statements.extend((
        "MERGE (n:DomainNode {id: $id}) SET n.type=$type, n.title=$title, n.approved=$approved, n.provenance=$provenance",
        node,
    ) for node in nodes)
    for edge in edges:
        payload = dict(edge); payload["key"] = f"{edge['from']}|{edge['type']}|{edge['to']}"
        statements.append(("MATCH (a:DomainNode {id:$from}), (b:DomainNode {id:$to}) MERGE (a)-[r:RELATION {key:$key}]->(b) SET r.type=$type, r.provenance=$provenance", payload))
    return statements

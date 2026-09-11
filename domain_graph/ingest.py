"""Validate, normalize and load the local Domain Graph input contract."""
from __future__ import annotations

import ast
import json
import re
from urllib.parse import urlparse
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

SOURCES = {"meeting-notes", "requirements", "design", "source-metadata"}
DEFAULT_TYPES = {"meeting-notes": "Meeting", "requirements": "Requirement", "design": "Design", "source-metadata": "Code"}
NODE_TYPES = {"Business", "Domain", "Entity", "Requirement", "Feature", "Design", "Code", "Test", "Issue", "PullRequest", "Commit", "Meeting", "Decision", "SourceDocument"}
ALLOWED_RELATIONS = {"DEFINED_BY", "RELATES_TO", "IMPLEMENTS", "VERIFIES", "BLOCKS", "CHANGED_BY", "DECIDED_IN", "DERIVED_FROM"}
APPROVAL_STATUSES = {"approved", "proposed", "rejected", "superseded"}
EXTRACTION_METHODS = {"human_authored", "human_curated", "deterministic_import", "ai_inferred"}
ID_PATTERN = re.compile(r"^[A-Z0-9]+(?:-[A-Z0-9]+)+$")
UTC_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")
SENSITIVE_PATTERN = re.compile(r"(?i)(?:password|passwd|token|secret|api[_ -]?key|cookie|private[_ -]?key)\s*[:=]")
EMAIL_PATTERN = re.compile(r"\b[^\s@]+@[^\s@]+\.[^\s@]+\b")


class InputValidationError(ValueError):
    """A safe machine-readable error; input values are never included."""
    def __init__(self, message: str, *, code: str = "invalid_field", path: str | Path | None = None,
                 field: str | None = None, record_id: str | None = None):
        # Never expose a workspace absolute path in an exception or its JSON form.
        safe_path = None
        if path:
            candidate = Path(path)
            parts = candidate.parts
            safe_path = str(Path("inputs", *parts[parts.index("inputs") + 1:])) if "inputs" in parts else candidate.name
        self.code, self.path, self.field, self.record_id = code, safe_path, field, record_id
        super().__init__(f"{self.path}: {message}" if self.path else message)

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "path": self.path or "", "field": self.field or "",
                "message": str(self).split(": ", 1)[-1], **({"recordId": self.record_id} if self.record_id else {})}


def _error(code: str, path: Path | str, field: str, message: str, record_id: str | None = None) -> InputValidationError:
    return InputValidationError(message, code=code, path=path, field=field, record_id=record_id)


def _scalar(text: str) -> Any:
    text = text.strip()
    if not text: return ""
    if text in {"true", "True"}: return True
    if text in {"false", "False"}: return False
    if text in {"null", "Null", "~"}: return None
    try: return json.loads(text)
    except json.JSONDecodeError: pass
    try: return ast.literal_eval(text)
    except (ValueError, SyntaxError): return text.strip("'\"")


def _parse_front_matter(path: Path) -> dict[str, Any]:
    try: text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc: raise _error("invalid_markdown_front_matter", path, "encoding", "front matter must be UTF-8") from exc
    if not text.startswith("---\n"): raise _error("invalid_markdown_front_matter", path, "frontMatter", "YAML front matter is required")
    end = text.find("\n---", 4)
    if end < 0: raise _error("invalid_markdown_front_matter", path, "frontMatter", "YAML front matter is not closed")
    result: dict[str, Any] = {}; stack: list[tuple[int, dict[str, Any]]] = [(-1, result)]
    for raw in text[4:end].splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"): continue
        indent = len(raw) - len(raw.lstrip())
        if ":" not in raw: raise _error("invalid_markdown_front_matter", path, "frontMatter", "invalid YAML entry")
        key, val = raw.strip().split(":", 1)
        while stack[-1][0] >= indent: stack.pop()
        parent = stack[-1][1]
        if val.strip(): parent[key] = _scalar(val)
        else: parent[key] = {}; stack.append((indent, parent[key]))
    return result


def _safe_excerpt(value: Any, path: Path, field: str, record_id: str | None = None) -> None:
    if not isinstance(value, str) or not value.strip(): raise _error("invalid_field", path, field, "required non-empty string", record_id)
    if SENSITIVE_PATTERN.search(value) or EMAIL_PATTERN.search(value): raise _error("sensitive_content", path, field, "sensitive content is not allowed", record_id)


def _valid_datetime(value: Any, path: Path, field: str, record_id: str | None = None) -> None:
    if not isinstance(value, str) or not UTC_PATTERN.fullmatch(value): raise _error("invalid_field", path, field, "expected UTC ISO 8601 timestamp", record_id)
    try: datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc: raise _error("invalid_field", path, field, "invalid timestamp", record_id) from exc


def _provenance(source: str, path: Path, value: dict[str, Any], relation: dict[str, Any] | None = None, index: int | None = None) -> dict[str, Any]:
    # A relation without its own provenance inherits values, but gets a distinct
    # relation anchor and evidence; it must not silently reuse the node anchor.
    own = (relation or {}).get("provenance")
    p = own or value.get("provenance") or {}; rid = value["id"]
    retrieved = p.get("retrieved_at", "1970-01-01T00:00:00Z")
    default_anchor = f"$.relations[{index}]" if relation is not None else "$"
    return {"source_type": p.get("source_type", source), "source_path": str(path), "source_id": p.get("source_id", rid),
            "source_locator": p.get("source_locator", f"inputs/{source}/{path.name}"), "source_anchor": p.get("source_anchor", default_anchor) if own else default_anchor,
            "source_revision": p.get("source_revision"), "retrieved_at": retrieved, "updated_by": p.get("updated_by", "unknown"),
            "observed_at": p.get("observed_at", retrieved), "extraction_method": p.get("extraction_method", value.get("extractionMethod", "deterministic_import")),
            "confidence": p.get("confidence", value.get("confidence", 1.0)), "evidence_excerpt": p.get("evidence_excerpt", (relation or {}).get("evidenceExcerpt", value.get("evidenceExcerpt", value["title"]))) }


def _validate(value: Any, source: str, path: Path) -> dict[str, Any]:
    if not isinstance(value, dict): raise _error("invalid_field", path, "$", "input must be one JSON object")
    rid = value.get("id")
    if not isinstance(rid, str) or not rid: raise _error("invalid_field", path, "id", "id is required")
    if not ID_PATTERN.fullmatch(rid): raise _error("invalid_field", path, "id", "id must contain uppercase letters, digits and hyphens", rid)
    if path.stem != rid: raise _error("file_id_mismatch", path, "id", "file name and id must match", rid)
    if not isinstance(value.get("title"), str) or not value["title"].strip(): raise _error("invalid_field", path, "title", "title is required", rid)
    value = dict(value); strict = "schemaVersion" in value; version = value.get("schemaVersion", "1.0")
    if not isinstance(version, str) or not re.fullmatch(r"1\.\d+", version): raise _error("unsupported_schema_version", path, "schemaVersion", "unsupported schema version", rid)
    value["schemaVersion"] = version
    if strict:
        required = ("id", "title", "type", "approvalStatus", "extractionMethod", "confidence", "evidenceExcerpt", "provenance", "relations")
        missing = next((field for field in required if field not in value), None)
        if missing: raise _error("invalid_field", path, missing, "required field is missing", rid)
    if not isinstance(value.get("relations", []), list): raise _error("invalid_relation", path, "relations", "relations must be an array", rid)
    if value.get("type") is not None and value["type"] not in NODE_TYPES: raise _error("invalid_field", path, "type", "unsupported node type", rid)
    value["type"] = value.get("type") or DEFAULT_TYPES[source]
    original_provenance = value.get("provenance")
    if strict and not isinstance(original_provenance, dict):
        raise _error("invalid_field", path, "provenance", "required provenance object", rid)
    if strict:
        required_provenance = ("source_id", "source_type", "source_locator", "source_anchor", "retrieved_at", "updated_by", "observed_at", "extraction_method", "confidence", "evidence_excerpt")
        missing = next((field for field in required_provenance if field not in original_provenance), None)
        if missing: raise _error("invalid_field", path, f"provenance.{missing}", "required provenance field", rid)
    p = _provenance(source, path, value); value["provenance"] = p
    has_status, has_legacy = "approvalStatus" in value, "approved" in value; status = value.get("approvalStatus", "approved" if value.get("approved") is True else "proposed")
    if has_status and has_legacy and ((value["approved"] is True) != (status == "approved")): raise _error("invalid_approval_state", path, "approvalStatus", "approval fields conflict", rid)
    value["approvalStatus"] = status; value["approved"] = status == "approved"; value["extractionMethod"] = value.get("extractionMethod", p["extraction_method"]); value["confidence"] = value.get("confidence", p["confidence"]); value["evidenceExcerpt"] = value.get("evidenceExcerpt", p["evidence_excerpt"])
    if status not in APPROVAL_STATUSES: raise _error("invalid_approval_state", path, "approvalStatus", "unsupported approval status", rid)
    if value["extractionMethod"] not in EXTRACTION_METHODS: raise _error("invalid_field", path, "extractionMethod", "unsupported extraction method", rid)
    if value["extractionMethod"] == "ai_inferred" and status != "proposed": raise _error("invalid_approval_state", path, "approvalStatus", "AI inference must be proposed", rid)
    try: value["confidence"] = float(value["confidence"])
    except (TypeError, ValueError) as exc: raise _error("invalid_field", path, "confidence", "confidence must be numeric", rid) from exc
    if not 0 <= value["confidence"] <= 1: raise _error("invalid_field", path, "confidence", "confidence must be between 0 and 1", rid)
    _safe_excerpt(value["evidenceExcerpt"], path, "evidenceExcerpt", rid)
    for field in ("source_id", "source_type", "source_locator", "source_anchor", "updated_by"):
        if not isinstance(p.get(field), str) or not p[field].strip(): raise _error("invalid_field", path, f"provenance.{field}", "required provenance field", rid)
    locator_url = urlparse(p["source_locator"])
    if Path(p["source_locator"]).is_absolute() or (locator_url.query and re.search(r"(?i)(token|password|secret|key|auth)=", locator_url.query)) or (locator_url.fragment and re.search(r"(?i)(token|password|secret|key|auth)", locator_url.fragment)):
        raise _error("sensitive_content", path, "provenance.source_locator", "source locator must be safe and relative", rid)
    for field in ("retrieved_at", "observed_at"): _valid_datetime(p[field], path, f"provenance.{field}", rid)
    _safe_excerpt(p["evidence_excerpt"], path, "provenance.evidence_excerpt", rid)
    if p.get("extraction_method") != value["extractionMethod"]:
        raise _error("invalid_field", path, "provenance.extraction_method", "provenance does not match record", rid)
    try:
        if float(p.get("confidence")) != float(value["confidence"]):
            raise _error("invalid_field", path, "provenance.confidence", "provenance does not match record", rid)
    except (TypeError, ValueError) as exc:
        raise _error("invalid_field", path, "provenance.confidence", "confidence must be numeric", rid) from exc
    if p.get("evidence_excerpt") != value["evidenceExcerpt"]:
        raise _error("invalid_field", path, "provenance.evidence_excerpt", "provenance does not match record", rid)
    for index, relation in enumerate(value["relations"]):
        if not isinstance(relation, dict) or relation.get("type") not in ALLOWED_RELATIONS or not isinstance(relation.get("to"), str) or not relation["to"]: raise _error("invalid_relation", path, f"relations[{index}]", "unsupported relation or empty target", rid)
        if strict and ("evidenceExcerpt" not in relation and not (isinstance(relation.get("provenance"), dict) and "evidence_excerpt" in relation["provenance"])):
            raise _error("invalid_field", path, f"relations[{index}].evidenceExcerpt", "relation evidence excerpt is required", rid)
        rp = _provenance(source, path, value, relation, index); _safe_excerpt(relation.get("evidenceExcerpt", rp["evidence_excerpt"]), path, f"relations[{index}].evidenceExcerpt", rid)
        if "evidenceExcerpt" in relation:
            rp["evidence_excerpt"] = relation["evidenceExcerpt"]
        for field in ("source_id", "source_type", "source_locator", "source_anchor", "updated_by"):
            if not isinstance(rp.get(field), str) or not rp[field].strip(): raise _error("invalid_field", path, f"relations[{index}].provenance.{field}", "required provenance field", rid)
        for field in ("retrieved_at", "observed_at"): _valid_datetime(rp[field], path, f"relations[{index}].provenance.{field}", rid)
        relation_status = relation.get("approvalStatus", value["approvalStatus"])
        relation_method = relation.get("extractionMethod", value["extractionMethod"])
        if relation_status not in APPROVAL_STATUSES or relation_method not in EXTRACTION_METHODS:
            raise _error("invalid_approval_state" if relation_status not in APPROVAL_STATUSES else "invalid_field", path, f"relations[{index}]", "invalid relation assertion", rid)
        if relation_method == "ai_inferred" and relation_status != "proposed":
            raise _error("invalid_approval_state", path, f"relations[{index}].approvalStatus", "AI inference must be proposed", rid)
        relation["approvalStatus"] = relation_status; relation["extractionMethod"] = relation_method
        relation["provenance"] = rp; relation["evidenceExcerpt"] = relation.get("evidenceExcerpt", rp["evidence_excerpt"])
    return value


def load_inputs(root: str | Path) -> list[dict[str, Any]]:
    root = Path(root); records: list[dict[str, Any]] = []; seen: set[str] = set()
    for source in sorted(SOURCES):
        directory = root / source
        if not directory.exists(): raise InputValidationError(f"missing input directory: {directory}", code="missing_source_directory", path=directory)
        for path in sorted(directory.iterdir()):
            if path.name.startswith("_") or path.suffix not in {".json", ".md"}: continue
            try: value = json.loads(path.read_text(encoding="utf-8")) if path.suffix == ".json" else _parse_front_matter(path)
            except json.JSONDecodeError as exc: raise _error("invalid_json", path, "$", "invalid JSON") from exc
            record = _validate(value, source, path)
            if record["id"] in seen: raise _error("duplicate_record_id", path, "id", "duplicate record id", record["id"])
            seen.add(record["id"]); records.append(record)
    if not records: raise InputValidationError(f"no inputs in {root}", code="no_inputs", path=root)
    return records


def normalize_inputs(records: Iterable[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    nodes: dict[str, dict[str, Any]] = {}; edges: dict[tuple[str, str, str], dict[str, Any]] = {}
    for record in records:
        rid = record["id"]; status = record.get("approvalStatus", "approved" if record.get("approved", False) else "proposed"); method = record.get("extractionMethod", "deterministic_import"); confidence = float(record.get("confidence", 1.0)); evidence = record.get("evidenceExcerpt", record["title"])
        node = {"id": rid, "type": record["type"], "title": record["title"], "approved": status == "approved", "approvalStatus": status, "extractionMethod": method, "confidence": confidence, "evidenceExcerpt": evidence, "provenance": record.get("provenance") or {}}
        if rid in nodes and nodes[rid] != node: raise InputValidationError("conflicting record", code="conflicting_record", record_id=rid)
        nodes[rid] = node
        for relation in record.get("relations", []):
            key = (rid, relation["type"], relation["to"]); edge = {"from": rid, "type": relation["type"], "to": relation["to"], "approvalStatus": relation.get("approvalStatus", status), "extractionMethod": relation.get("extractionMethod", method), "confidence": float(relation.get("confidence", confidence)), "evidenceExcerpt": relation.get("evidenceExcerpt", evidence), "provenance": relation.get("provenance") or record.get("provenance") or {}}
            if key in edges and edges[key] != edge: raise InputValidationError("conflicting relation", code="conflicting_record", record_id=rid)
            edges[key] = edge
    return list(nodes.values()), list(edges.values())


class InMemoryGraph:
    """Deterministic graph double used by tests and local demos."""
    def __init__(self) -> None: self.nodes: dict[str, dict[str, Any]] = {}; self.edges: dict[tuple[str, str, str], dict[str, Any]] = {}
    def ingest(self, records: Iterable[dict[str, Any]]) -> None:
        nodes, edges = normalize_inputs(records); old_nodes, old_edges = self.nodes.copy(), self.edges.copy()
        try:
            for node in nodes:
                existing = self.nodes.get(node["id"])
                if existing and existing != node:
                    # Never let an unapproved/AI assertion replace a human-approved definition.
                    if existing.get("approvalStatus") == "approved" and existing.get("extractionMethod") != "ai_inferred":
                        raise InputValidationError("conflicting record", code="conflicting_record", record_id=node["id"])
                    self.nodes[node["id"]] = node
                else:
                    self.nodes[node["id"]] = node
            for edge in edges:
                if edge["to"] not in self.nodes: raise InputValidationError(f"unregistered target: {edge['to']}", code="unknown_target")
                key = (edge["from"], edge["type"], edge["to"]); existing = self.edges.get(key)
                if existing and existing != edge and existing.get("approvalStatus") == "approved" and existing.get("extractionMethod") != "ai_inferred":
                    raise InputValidationError("conflicting relation", code="conflicting_record", record_id=edge["from"])
                self.edges[key] = edge
        except Exception: self.nodes, self.edges = old_nodes, old_edges; raise
    def trace(self, start: str, target: str | None = None) -> dict[str, Any]:
        if start not in self.nodes: return {"status": "not_registered", "nodes": [], "relations": []}
        found, queue, path_edges = {start}, [start], []
        while queue:
            current = queue.pop(0)
            for edge in self.edges.values():
                if edge.get("approvalStatus") != "approved" or edge.get("extractionMethod") == "ai_inferred": continue
                neighbour = edge["to"] if edge["from"] == current else edge["from"] if edge["to"] == current else None
                if neighbour and neighbour not in found: found.add(neighbour); queue.append(neighbour); path_edges.append(edge)
        return {"status": "evidence_insufficient" if target and target not in found else "ok", "nodes": [self.nodes[n] for n in found], "relations": path_edges}


CONSTRAINTS = ["CREATE CONSTRAINT domain_node_id IF NOT EXISTS FOR (n:DomainNode) REQUIRE n.id IS UNIQUE", "CREATE CONSTRAINT domain_edge_key IF NOT EXISTS FOR ()-[r:RELATION]-() REQUIRE r.key IS UNIQUE", "CREATE INDEX domain_node_type IF NOT EXISTS FOR (n:DomainNode) ON (n.type)", "CREATE INDEX relation_type IF NOT EXISTS FOR ()-[r:RELATION]-() ON (r.type)"]
def neo4j_statements(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> list[tuple[str, dict[str, Any]]]:
    statements = [(query, {}) for query in CONSTRAINTS]
    for node in nodes:
        params = dict(node); params.update(node["provenance"])
        statements.append(("MERGE (n:DomainNode {id: $id}) ON CREATE SET n.createdAt=$createdAt SET n.type=CASE WHEN n.approvalStatus='approved' AND n.extractionMethod <> 'ai_inferred' AND $approvalStatus <> 'approved' THEN n.type ELSE $type END, n.title=CASE WHEN n.approvalStatus='approved' AND n.extractionMethod <> 'ai_inferred' AND $approvalStatus <> 'approved' THEN n.title ELSE $title END, n.approved=CASE WHEN n.approvalStatus='approved' AND n.extractionMethod <> 'ai_inferred' AND $approvalStatus <> 'approved' THEN n.approved ELSE $approved END, n.approvalStatus=CASE WHEN n.approvalStatus='approved' AND n.extractionMethod <> 'ai_inferred' AND $approvalStatus <> 'approved' THEN n.approvalStatus ELSE $approvalStatus END, n.extractionMethod=CASE WHEN n.approvalStatus='approved' AND n.extractionMethod <> 'ai_inferred' AND $approvalStatus <> 'approved' THEN n.extractionMethod ELSE $extractionMethod END, n.confidence=CASE WHEN n.approvalStatus='approved' AND n.extractionMethod <> 'ai_inferred' AND $approvalStatus <> 'approved' THEN n.confidence ELSE $confidence END, n.evidenceExcerpt=CASE WHEN n.approvalStatus='approved' AND n.extractionMethod <> 'ai_inferred' AND $approvalStatus <> 'approved' THEN n.evidenceExcerpt ELSE $evidenceExcerpt END, n.sourceId=$source_id, n.sourceType=$source_type, n.sourceLocator=$source_locator, n.sourceAnchor=$source_anchor, n.sourceRevision=$source_revision, n.retrievedAt=$retrieved_at, n.updatedBy=$updated_by, n.observedAt=$observed_at", {**params, "createdAt": node["provenance"].get("retrieved_at")}))
    for edge in edges:
        payload = dict(edge); payload["key"] = f"{edge['from']}|{edge['type']}|{edge['to']}"; payload.update(edge["provenance"])
        statements.append(("MATCH (a:DomainNode {id:$from}), (b:DomainNode {id:$to}) MERGE (a)-[r:RELATION {key:$key}]->(b) SET r.type=$type, r.approvalStatus=$approvalStatus, r.extractionMethod=$extractionMethod, r.confidence=$confidence, r.evidenceExcerpt=$evidenceExcerpt, r.sourceId=$source_id, r.sourceType=$source_type, r.sourceLocator=$source_locator, r.sourceAnchor=$source_anchor, r.sourceRevision=$source_revision, r.retrievedAt=$retrieved_at, r.updatedBy=$updated_by, r.observedAt=$observed_at", payload))
    return statements

"""Read-only consistency repositories. Cypher is fixed and values are parameters."""
from __future__ import annotations
from typing import Any

from .ingest import ALLOWED_RELATIONS


class InMemoryConsistencyRepository:
    tenant_aware = True
    def __init__(self, graph): self.graph = graph
    def exists(self, node_id): return node_id in self.graph.nodes
    def snapshot(self, scope_ids=(), include_proposed=False):
        ids = set(scope_ids) if scope_ids else set(self.graph.nodes)
        nodes = [n for i, n in self.graph.nodes.items() if i in ids]
        edges = [e for e in self.graph.edges.values() if e["from"] in ids or e["to"] in ids]
        if not include_proposed:
            nodes = [n for n in nodes if _approved(n)]
            edges = [e for e in edges if _approved(e)]
        return {"nodes": nodes, "edges": edges}
    def candidates(self, scope_ids=()):
        ids = set(scope_ids) if scope_ids else set(self.graph.nodes)
        return {"nodes": [n for i, n in self.graph.nodes.items() if i in ids and not _approved(n)], "edges": [e for e in self.graph.edges.values() if (e["from"] in ids or e["to"] in ids) and not _approved(e)]}


def _approved(item):
    return item.get("approvalStatus") == "approved" and item.get("extractionMethod") != "ai_inferred"


class Neo4jConsistencyRepository:
    """Tenant-aware adapter; shared databases fail closed until schema support exists."""
    tenant_aware = False
    CYPHER_APPROVED = "MATCH (n:DomainNode) WHERE n.id IN $scopeIds AND n.approvalStatus='approved' AND n.extractionMethod <> 'ai_inferred' RETURN n.id AS id,n.type AS type,n.approvalStatus AS approvalStatus,n.extractionMethod AS extractionMethod,n.confidence AS confidence,n.evidenceExcerpt AS evidenceExcerpt,n.sourceId AS sourceId,n.sourceLocator AS sourceLocator,n.sourceAnchor AS sourceAnchor,n.sourceRevision AS sourceRevision,n.retrievedAt AS retrievedAt,n.updatedBy AS updatedBy,n.observedAt AS observedAt"
    CYPHER_IMPLEMENTS = "MATCH p=(a:DomainNode)-[:RELATION*1..6]->(b:DomainNode) WHERE ALL(r IN relationships(p) WHERE r.type='IMPLEMENTS' AND r.approvalStatus='approved' AND r.extractionMethod <> 'ai_inferred') AND a.id IN $scopeIds RETURN a.id AS fromId,b.id AS toId,length(p) AS depth"
    def __init__(self, driver, tenant_aware=False): self.driver, self.tenant_aware = driver, tenant_aware
    def _guard(self):
        if not self.tenant_aware: raise RuntimeError("tenant policy unavailable")
    def snapshot(self, scope_ids=(), include_proposed=False):
        self._guard()
        with self.driver.session() as session:
            rows = [dict(r) for r in session.run(self.CYPHER_APPROVED, scopeIds=list(scope_ids))]
        return {"nodes": rows, "edges": []}
    def candidates(self, scope_ids=()): self._guard(); return {"nodes": [], "edges": []}


# Explicit alias for callers that only depend on the repository contract.
ConsistencyRepository = Neo4jConsistencyRepository

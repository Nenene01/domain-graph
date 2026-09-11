"""Read-only repositories. Neo4j query text is fixed and all values are parameters."""
from __future__ import annotations
from typing import Any
from .ingest import ALLOWED_RELATIONS, InMemoryGraph

class InMemoryQueryRepository:
    def __init__(self, graph: InMemoryGraph): self.graph=graph
    tenant_aware = False
    def exists(self, node_id): return node_id in self.graph.nodes
    def paths(self, start, target, direction, relation_types, max_depth, include_proposed):
        result=[]; queue=[(start,[],{start})]
        while queue:
            node,path,seen=queue.pop(0)
            if target and node==target and path: result.append(path)
            if not target or node!=target:
                for key,e in self.graph.edges.items():
                    if e["type"] not in relation_types or len(path)>=max_depth: continue
                    approved=e.get("approvalStatus")=="approved" and e.get("extractionMethod")!="ai_inferred"
                    if not include_proposed and not approved: continue
                    if direction=="outbound": nxt=e["to"] if e["from"]==node else None
                    elif direction=="inbound": nxt=e["from"] if e["to"]==node else None
                    else: nxt=e["to"] if e["from"]==node else e["from"] if e["to"]==node else None
                    if nxt and nxt not in seen and (nxt in self.graph.nodes): queue.append((nxt,path+[e],seen|{nxt}))
            if not target and path: result.append(path)
        unique={e["from"]+"|"+e["type"]+"|"+e["to"]:e for p in result for e in p}
        return sorted(unique.values(),key=lambda e:(e["from"]+"|"+e["type"]+"|"+e["to"],e["from"],e["to"]))

    def provenance(self, *, node_id=None, relation_key=None):
        if node_id is not None:
            node = self.graph.nodes.get(node_id)
            if not node: return None
            return {"kind":"node", "id":node_id, "type":node.get("type"), "approvalStatus":node.get("approvalStatus"), "extractionMethod":node.get("extractionMethod"), "confidence":node.get("confidence"), "provenance":node.get("provenance", {})}
        edge = self.graph.edges.get(tuple(relation_key)) if relation_key else None
        if not edge: return None
        return {"kind":"relation", "key":"|".join(relation_key), "type":edge.get("type"), "fromId":edge.get("from"), "toId":edge.get("to"), "approvalStatus":edge.get("approvalStatus"), "extractionMethod":edge.get("extractionMethod"), "confidence":edge.get("confidence"), "provenance":edge.get("provenance", {})}

class Neo4jQueryRepository:
    # Relationship type is filtered as a property; no user-controlled Cypher fragments.
    _TAIL = """ WHERE length(p)<= $maxDepth AND ($targetId IS NULL OR candidate.id=$targetId) AND ALL(r IN rels WHERE r.type IN $relationTypes AND ($includeProposed OR (r.approvalStatus='approved' AND r.extractionMethod <> 'ai_inferred'))) AND ALL(n IN nodes(p) WHERE $includeProposed OR (n.approvalStatus='approved' AND n.extractionMethod <> 'ai_inferred')) RETURN [n IN nodes(p)|{id:n.id,type:n.type,title:n.title,approvalStatus:n.approvalStatus,extractionMethod:n.extractionMethod,confidence:n.confidence,sourceId:n.sourceId,sourceType:n.sourceType,sourceLocator:n.sourceLocator,sourceAnchor:n.sourceAnchor,sourceRevision:n.sourceRevision,retrievedAt:n.retrievedAt,updatedBy:n.updatedBy,observedAt:n.observedAt,evidenceExcerpt:n.evidenceExcerpt}] AS nodes, [r IN rels|{key:r.key,type:r.type,fromId:startNode(r).id,toId:endNode(r).id,approvalStatus:r.approvalStatus,extractionMethod:r.extractionMethod,confidence:r.confidence,sourceId:r.sourceId,sourceType:r.sourceType,sourceLocator:r.sourceLocator,sourceAnchor:r.sourceAnchor,sourceRevision:r.sourceRevision,retrievedAt:r.retrievedAt,updatedBy:r.updatedBy,observedAt:r.observedAt,evidenceExcerpt:r.evidenceExcerpt}] AS relations ORDER BY relations[0].key LIMIT $limit"""
    CYPHER_OUTBOUND = "MATCH p=(start:DomainNode {id:$startId})-[rels:RELATION*1..6]->(candidate:DomainNode)" + _TAIL
    CYPHER_INBOUND = "MATCH p=(start:DomainNode {id:$startId})<-[rels:RELATION*1..6]-(candidate:DomainNode)" + _TAIL
    CYPHER_BOTH = "MATCH p=(start:DomainNode {id:$startId})-[rels:RELATION*1..6]-(candidate:DomainNode)" + _TAIL
    tenant_aware = False
    def __init__(self, driver): self.driver=driver
    def exists(self,node_id):
        with self.driver.session() as s: return s.run("MATCH (n:DomainNode {id:$id}) RETURN n.id",id=node_id).single() is not None
    def paths(self,start,target,direction,relation_types,max_depth,include_proposed):
        query={"outbound":self.CYPHER_OUTBOUND,"inbound":self.CYPHER_INBOUND,"both":self.CYPHER_BOTH}[direction]
        params={"startId":start,"targetId":target,"relationTypes":list(relation_types),"maxDepth":max_depth,"includeProposed":include_proposed,"limit":10001}
        with self.driver.session() as s: return [dict(r) for r in s.run(query,**params)]

    def provenance(self, *, node_id=None, relation_key=None):
        # Fixed projections only; callers must strictly parse relation_key first.
        if node_id is not None:
            query = "MATCH (n:DomainNode {id:$id}) RETURN {kind:'node',id:n.id,type:n.type,approvalStatus:n.approvalStatus,extractionMethod:n.extractionMethod,confidence:n.confidence,provenance:{sourceId:n.sourceId,sourceType:n.sourceType,sourceLocator:n.sourceLocator,sourceAnchor:n.sourceAnchor,sourceRevision:n.sourceRevision,retrievedAt:n.retrievedAt,updatedBy:n.updatedBy,observedAt:n.observedAt,evidenceExcerpt:n.evidenceExcerpt,extractionMethod:n.extractionMethod,confidence:n.confidence}} AS item"
            params = {"id": node_id}
        else:
            query = "MATCH (a:DomainNode)-[r:RELATION {key:$key}]->(b:DomainNode) RETURN {kind:'relation',key:r.key,type:r.type,fromId:a.id,toId:b.id,approvalStatus:r.approvalStatus,extractionMethod:r.extractionMethod,confidence:r.confidence,provenance:{sourceId:r.sourceId,sourceType:r.sourceType,sourceLocator:r.sourceLocator,sourceAnchor:r.sourceAnchor,sourceRevision:r.sourceRevision,retrievedAt:r.retrievedAt,updatedBy:r.updatedBy,observedAt:r.observedAt,evidenceExcerpt:r.evidenceExcerpt,extractionMethod:r.extractionMethod,confidence:r.confidence}} AS item"
            params = {"key": "|".join(relation_key)}
        with self.driver.session() as s:
            record = s.run(query, **params).single()
            return record["item"] if record else None

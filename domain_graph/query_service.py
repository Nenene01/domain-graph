from __future__ import annotations
from dataclasses import asdict
from datetime import datetime, timezone
from time import monotonic
from .audit import NullAudit
from .query_contract import Principal, QueryApiError, TraceRequest, sign_page_token, verify_page_token

class QueryService:
    def __init__(self, repository, audit=None, page_secret: bytes|None=None):
        self.repository=repository; self.audit=audit or NullAudit(); self.page_secret=page_secret
    def trace(self, req: TraceRequest, principal: Principal, request_id: str):
        started=monotonic(); status=500; error=None; nodes=[]; relations=[]
        try:
            now=datetime.now(timezone.utc)
            if not principal or principal.token_expires_at <= now: raise QueryApiError("unauthorized",401,"unauthorized")
            if "domain-graph:read" not in principal.scopes: raise QueryApiError("forbidden",403,"forbidden")
            if req.include_proposed and "domain-graph:read-proposed" not in principal.scopes: raise QueryApiError("forbidden",403,"forbidden")
            after=verify_page_token(req.page_token,req,principal,self.page_secret) if req.page_token else None
            if not self.repository.exists(req.start_id): return self._result(req,request_id,"not_registered",[],[],principal,started)
            raw=self.repository.paths(req.start_id,req.target_id,req.direction,req.relation_types,req.max_depth,req.include_proposed)
            if len(raw)>10000: raise QueryApiError("query_limit_exceeded",413)
            relations=[r for r in raw if not after or self._key(r)>after][:req.page_size]
            if req.target_id and not any(self._key(r) in {self._key(x) for x in relations} for r in raw):
                return self._result(req,request_id,"evidence_insufficient",[],[],principal,started)
            ids={req.start_id}|{x for r in relations for x in (r.get("from"),r.get("to"),r.get("fromId"),r.get("toId")) if x}
            source=getattr(self.repository,"graph",None)
            if source: nodes=[source.nodes[i] for i in sorted(ids) if i in source.nodes and (req.include_proposed or (source.nodes[i].get("approvalStatus")=="approved" and source.nodes[i].get("extractionMethod")!="ai_inferred"))]
            else: nodes=[]
            status="ok" if relations or not req.target_id else "evidence_insufficient"
            return self._result(req,request_id,status,nodes,relations,principal,started)
        except QueryApiError as exc:
            status=exc.status; error=exc.code; raise
        except (TimeoutError,):
            status=504; error="query_timeout"; raise QueryApiError("query_timeout",504,"query timed out")
        except Exception:
            status=503; error="service_unavailable"; raise QueryApiError("service_unavailable",503,"service unavailable")
        finally:
            if error: self._audit(req,request_id,principal,status,error,started,len(nodes),len(relations))
    @staticmethod
    def _key(r): return r.get("key") or f'{r.get("from",r.get("fromId"))}|{r.get("type")}|{r.get("to",r.get("toId"))}'
    def _result(self,req,rid,status,nodes,relations,principal,started):
        converted=[self._node(n) for n in nodes]; rs=[self._relation(r,req.direction) for r in relations]
        nxt=None; truncated=len(relations)>=req.page_size
        if truncated and self.page_secret and rs: nxt=sign_page_token(req,self._key(relations[-1]),principal,self.page_secret)
        result={"status":status,"query":{"startId":req.start_id,"targetId":req.target_id,"direction":req.direction,"maxDepth":req.max_depth,"includeProposed":req.include_proposed},"nodes":converted,"relations":rs,"page":{"pageSize":req.page_size,"nextPageToken":nxt,"truncated":truncated},"meta":{"requestId":rid}}
        self._audit(req,rid,principal,200,None,started,len(converted),len(rs)); return result
    def _audit(self,req,rid,p,status,error,start,nodes,relations):
        self.audit.record({"eventType":"graph_trace","occurredAt":datetime.now(timezone.utc).isoformat(),"requestId":rid,"principalSubjectId":getattr(p,"subject_id",None),"outcome":"error" if error else "success","httpStatus":status,"startId":req.start_id,"targetIdPresent":req.target_id is not None,"direction":req.direction,"relationTypes":list(req.relation_types),"maxDepth":req.max_depth,"pageSize":req.page_size,"includeProposed":req.include_proposed,"returnedNodeCount":nodes,"returnedRelationCount":relations,"durationMs":int((monotonic()-start)*1000),"errorCode":error})
    @staticmethod
    def _node(n):
        p=n.get("provenance",n); return {"id":n["id"],"type":n.get("type"),"title":n.get("title"),"approvalStatus":n.get("approvalStatus"),"extractionMethod":n.get("extractionMethod"),"confidence":n.get("confidence"),"provenance":_camel(p)}
    @staticmethod
    def _relation(r,direction):
        p=r.get("provenance",r); out={"key":r.get("key") or f'{r.get("from")}|{r.get("type")}|{r.get("to")}',"type":r.get("type"),"fromId":r.get("from",r.get("fromId")),"toId":r.get("to",r.get("toId")),"traversedDirection":direction,"evidenceLevel":"proposed_or_inferred" if r.get("approvalStatus")!="approved" or r.get("extractionMethod")=="ai_inferred" else "approved","approvalStatus":r.get("approvalStatus"),"extractionMethod":r.get("extractionMethod"),"confidence":r.get("confidence"),"provenance":_camel(p)}; return out
def _camel(p):
    names={"source_id":"sourceId","source_type":"sourceType","source_path":"sourcePath","source_locator":"sourceLocator","source_anchor":"sourceAnchor","source_revision":"sourceRevision","retrieved_at":"retrievedAt","updated_by":"updatedBy","observed_at":"observedAt","evidence_excerpt":"evidenceExcerpt","extraction_method":"extractionMethod"}
    return {names.get(k,k):v for k,v in p.items() if k in names or k in {"sourceId","sourceType","sourceLocator","sourceAnchor","sourceRevision","retrievedAt","updatedBy","observedAt","evidenceExcerpt","extractionMethod","confidence"}}

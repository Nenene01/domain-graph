"""Authenticated MCP application service. No driver or arbitrary Cypher leaks out."""
from __future__ import annotations
from collections import defaultdict, deque
from datetime import datetime, timezone
from time import monotonic
import uuid
from .audit import NullAudit
from .auth import Authenticator, READ_SCOPE, PROPOSED_SCOPE
from .mcp_contract import TOOLS, parse_tool_input, public_error, PROVENANCE_FIELDS
from .query_contract import QueryApiError

class RateLimiter:
    def __init__(self, subject_limit=30, tenant_limit=300, burst_subject=10, burst_tenant=60, now=None):
        self.subject_limit, self.tenant_limit, self.burst_subject, self.burst_tenant = subject_limit, tenant_limit, burst_subject, burst_tenant
        self.now = now or monotonic; self._events = defaultdict(deque)
    def allow(self, tenant, subject, tool):
        current = self.now(); allowed = True
        for key, limit, burst in ((f"s:{tenant}:{subject}:{tool}",self.subject_limit,self.burst_subject),(f"t:{tenant}",self.tenant_limit,self.burst_tenant)):
            q=self._events[key]
            while q and current-q[0] >= 60: q.popleft()
            if len(q) >= limit + burst: allowed=False
            q.append(current)
        return allowed

class McpToolService:
    def __init__(self, authenticator: Authenticator, query_service, repository=None, audit=None, limiter=None, *, require_tenant_aware=False):
        self.authenticator, self.query_service = authenticator, query_service
        self.repository = repository or query_service.repository
        if require_tenant_aware and not getattr(self.repository, "tenant_aware", False): raise RuntimeError("tenant-aware repository required")
        self.audit, self.limiter = audit or NullAudit(), limiter or RateLimiter()

    def call(self, tool: str, arguments, authorization: str | None, *, transport="stdio", request_id=None):
        rid = request_id if isinstance(request_id,str) and len(request_id)<=128 else str(uuid.uuid4()); started=monotonic(); principal=None; outcome="error"; error=None; result=None
        try:
            principal=self.authenticator.authenticate(authorization)
            parsed=parse_tool_input(tool, arguments)
            if tool not in TOOLS: raise QueryApiError("invalid_request",400)
            include_proposed = parsed[1] if tool == "domain_graph.get_provenance" else getattr(parsed, "include_proposed", False)
            if include_proposed and PROPOSED_SCOPE not in principal.scopes: raise QueryApiError("forbidden",403,"forbidden")
            if not self.limiter.allow(principal.tenant_id, principal.subject_id, tool): raise QueryApiError("rate_limited",429)
            if tool == "domain_graph.get_provenance":
                result = self._provenance(parsed[0], principal, include_proposed)
            else:
                if tool == "domain_graph.impact": parsed = parsed.__class__(parsed.start_id, None, parsed.direction, parsed.relation_types, parsed.max_depth, parsed.page_size, parsed.page_token, parsed.include_proposed)
                result=self.query_service.trace(parsed, principal, rid)
            outcome="success"; return {"structuredContent": result}
        except Exception as exc:
            error=public_error(exc)["code"]; raise QueryApiError(error, getattr(exc,"status",503), public_error(exc)["message"]) from None
        finally:
            self.audit.record({"eventType":"mcp_tool_call","occurredAt":datetime.now(timezone.utc).isoformat(),"requestId":rid,"tool":tool if tool in TOOLS else "unknown","transport":transport if transport in {"stdio","streamable_http"} else "stdio","tenantId":getattr(principal,"tenant_id",None),"projectCount":len(getattr(principal,"project_ids",())) if principal else 0,"principalSubjectId":getattr(principal,"subject_id",None),"tokenId":getattr(principal,"token_id",None),"outcome":outcome,"errorCode":error,"httpStatus":200 if outcome=="success" else (429 if error=="rate_limited" else 403 if error=="forbidden" else 401 if error=="unauthorized" else 400),"inputShape":self._shape(arguments),"returnedNodeCount":len((result or {}).get("nodes",[])) if isinstance(result,dict) else 0,"returnedRelationCount":len((result or {}).get("relations",[])) if isinstance(result,dict) else 0,"durationMs":int((monotonic()-started)*1000)})

    def _provenance(self, parsed, principal, include_proposed=False):
        item = self.repository.provenance(node_id=parsed) if isinstance(parsed,str) else self.repository.provenance(relation_key=parsed)
        if not item: raise QueryApiError("not_registered",404,"not registered")
        if not include_proposed and (item.get("approvalStatus") != "approved" or item.get("extractionMethod") == "ai_inferred"):
            raise QueryApiError("evidence_insufficient",404,"evidence insufficient")
        p=item.get("provenance",{}); item["provenance"]={k:v for k,v in p.items() if k in PROVENANCE_FIELDS}; return item
    @staticmethod
    def _shape(value):
        if not isinstance(value,dict): return {}
        return {"hasTargetId":isinstance(value.get("targetId"),str),"maxDepth":value.get("maxDepth",6),"pageSize":value.get("pageSize",25),"includeProposed":value.get("includeProposed",False)}

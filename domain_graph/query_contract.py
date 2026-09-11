"""Public contract and safe validation for the evidence-backed query API."""
from __future__ import annotations
import base64, hashlib, hmac, json, re, time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from .ingest import ALLOWED_RELATIONS, ID_PATTERN

MAX_BODY = 16 * 1024
class QueryApiError(ValueError):
    def __init__(self, code: str, status: int, message: str = "request is invalid"):
        self.code, self.status = code, status; super().__init__(message)

@dataclass(frozen=True)
class Principal:
    subject_id: str
    project_ids: frozenset[str]
    scopes: frozenset[str]
    token_expires_at: datetime
    # Optional for existing trusted query API callers. MCP production callers
    # must provide both values and are checked by its deployment policy.
    tenant_id: str | None = None
    token_id: str | None = None

@dataclass(frozen=True)
class TraceRequest:
    start_id: str; target_id: str|None = None; direction: str = "both"
    relation_types: tuple[str,...] = tuple(sorted(ALLOWED_RELATIONS)); max_depth: int = 6
    page_size: int = 25; page_token: str|None = None; include_proposed: bool = False

def parse_request(body: Any) -> TraceRequest:
    if not isinstance(body, dict): raise QueryApiError("invalid_request", 400)
    allowed={"startId","targetId","direction","relationTypes","maxDepth","pageSize","pageToken","includeProposed"}
    if set(body)-allowed: raise QueryApiError("invalid_request",400)
    start=body.get("startId")
    if not isinstance(start,str) or len(start)>128 or not ID_PATTERN.fullmatch(start): raise QueryApiError("invalid_request",400)
    target=body.get("targetId")
    if target is not None and (not isinstance(target,str) or len(target)>128 or not ID_PATTERN.fullmatch(target)): raise QueryApiError("invalid_request",400)
    direction=body.get("direction","both")
    if direction not in {"outbound","inbound","both"}: raise QueryApiError("invalid_request",400)
    types=body.get("relationTypes", list(sorted(ALLOWED_RELATIONS)))
    if not isinstance(types,list) or not types or len(set(types))!=len(types) or any(x not in ALLOWED_RELATIONS for x in types): raise QueryApiError("invalid_request",400)
    depth=body.get("maxDepth",6); size=body.get("pageSize",25)
    if type(depth) is not int or not 1<=depth<=6 or type(size) is not int or not 1<=size<=100: raise QueryApiError("query_limit_exceeded",413)
    token=body.get("pageToken")
    if token is not None and (not isinstance(token,str) or len(token)>4096): raise QueryApiError("invalid_page_token",400)
    proposed=body.get("includeProposed",False)
    if type(proposed) is not bool: raise QueryApiError("invalid_request",400)
    return TraceRequest(start,target,direction,tuple(types),depth,size,token,proposed)

def _canonical(req: TraceRequest) -> dict[str,Any]:
    return {"startId":req.start_id,"targetId":req.target_id,"direction":req.direction,"relationTypes":list(req.relation_types),"maxDepth":req.max_depth,"includeProposed":req.include_proposed}

def sign_page_token(req: TraceRequest, after: str, principal: Principal, secret: bytes, now: int|None=None) -> str:
    now=int(time.time() if now is None else now); payload={"request":_canonical(req),"after":after,"subject":principal.subject_id,"issuedAt":now,"expiresAt":now+600}
    raw=json.dumps(payload,separators=(",",":"),sort_keys=True).encode(); sig=hmac.new(secret,raw,hashlib.sha256).digest()
    return base64.urlsafe_b64encode(raw+b"."+sig).decode().rstrip("=")

def verify_page_token(token: str, req: TraceRequest, principal: Principal, secret: bytes, now: int|None=None) -> str:
    try:
        raw=base64.urlsafe_b64decode(token+"="*((4-len(token)%4)%4)); body,sig=raw.rsplit(b".",1)
        if not hmac.compare_digest(sig,hmac.new(secret,body,hashlib.sha256).digest()): raise ValueError
        p=json.loads(body); current=int(time.time() if now is None else now)
        if p["expiresAt"]<current or p["subject"]!=principal.subject_id or p["request"]!=_canonical(req) or not isinstance(p["after"],str): raise ValueError
        return p["after"]
    except Exception as exc: raise QueryApiError("invalid_page_token",400) from exc

def request_id(value: str|None) -> str:
    if isinstance(value,str) and len(value)<=128 and re.fullmatch(r"[A-Za-z0-9._:-]+",value): return value
    import uuid; return str(uuid.uuid4())

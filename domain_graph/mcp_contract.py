"""MCP tool schemas and public, non-sensitive contracts."""
from __future__ import annotations
import re
from typing import Any
from .ingest import ALLOWED_RELATIONS, ID_PATTERN
from .query_contract import QueryApiError, parse_request

TOOLS = ("domain_graph.trace", "domain_graph.get_provenance", "domain_graph.impact")
PROVENANCE_FIELDS = frozenset({"sourceId", "sourceType", "sourceLocator", "sourceAnchor", "sourceRevision", "retrievedAt", "updatedBy", "observedAt", "evidenceExcerpt", "extractionMethod", "confidence"})
TOOL_SCHEMAS = {name: {"type":"object", "additionalProperties":False} for name in TOOLS}
TOOL_SCHEMAS["domain_graph.trace"].update({"properties": {"startId":{"type":"string"}, "targetId":{"type":"string"}, "direction":{"enum":["outbound","inbound","both"]}, "relationTypes":{"type":"array"}, "maxDepth":{"type":"integer","minimum":1,"maximum":6}, "pageSize":{"type":"integer","minimum":1,"maximum":100}, "pageToken":{"type":"string"}, "includeProposed":{"type":"boolean"}}, "required":["startId"]})
TOOL_SCHEMAS["domain_graph.impact"] = dict(TOOL_SCHEMAS["domain_graph.trace"])
TOOL_SCHEMAS["domain_graph.get_provenance"].update({"properties":{"nodeId":{"type":"string"},"relationKey":{"type":"string"},"includeProposed":{"type":"boolean"}}, "oneOf":[{"required":["nodeId"]},{"required":["relationKey"]}]})

def parse_relation_key(value: Any) -> tuple[str, str, str]:
    if not isinstance(value, str) or len(value) > 386 or value.count("|") != 2: raise QueryApiError("invalid_request", 400)
    parts = value.split("|")
    if not ID_PATTERN.fullmatch(parts[0]) or parts[1] not in ALLOWED_RELATIONS or not ID_PATTERN.fullmatch(parts[2]): raise QueryApiError("invalid_request", 400)
    return parts[0], parts[1], parts[2]

def parse_tool_input(tool: str, arguments: Any):
    if tool not in TOOLS or not isinstance(arguments, dict): raise QueryApiError("invalid_request", 400)
    if tool in (TOOLS[0], TOOLS[2]): return parse_request(arguments)
    include = arguments.get("includeProposed", False)
    if type(include) is not bool: raise QueryApiError("invalid_request",400)
    keys = set(arguments) - {"includeProposed"}
    if keys not in ({"nodeId"}, {"relationKey"}): raise QueryApiError("invalid_request", 400)
    if "nodeId" in arguments and (not isinstance(arguments["nodeId"], str) or not ID_PATTERN.fullmatch(arguments["nodeId"])): raise QueryApiError("invalid_request",400)
    selected = arguments["nodeId"] if "nodeId" in arguments else parse_relation_key(arguments["relationKey"])
    return selected, include

def public_error(exc: Exception) -> dict[str, str]:
    code = exc.code if isinstance(exc, QueryApiError) else "service_unavailable"
    allowed = {"unauthorized","forbidden","invalid_request","query_limit_exceeded","query_timeout","rate_limited","not_registered","evidence_insufficient","service_unavailable"}
    return {"code": code if code in allowed else "service_unavailable", "message": code if code in allowed else "service unavailable"}

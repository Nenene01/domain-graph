"""Deterministic consistency rules, baseline diffing and safe audit."""
from __future__ import annotations
import hashlib, json
from datetime import datetime, timezone
from time import monotonic
from .audit import NullAudit
from .consistency_contract import ConsistencyCheckRequest, ConsistencyError, ConsistencyReport, RULE_VERSION, fingerprint

REQUIRED_PROVENANCE = ("source_id", "source_locator", "source_anchor", "retrieved_at", "updated_by", "observed_at")
RULES = ("requirement_without_implementation", "design_without_implementation", "implementation_without_requirement", "missing_provenance", "relation_direction_or_type_conflict", "conflicting_evidence")


class ConsistencyService:
    def __init__(self, repository, audit=None): self.repository, self.audit = repository, audit or NullAudit()
    def check(self, request: ConsistencyCheckRequest, *, request_id="request", baseline=None, tenant_id=None):
        started = monotonic(); run_id = self._run_id(request); error = None
        try:
            if not getattr(self.repository, "tenant_aware", False): raise ConsistencyError("forbidden", 403, "forbidden")
            for node_id in request.scope_node_ids:
                if not self.repository.exists(node_id): return self._report(request, request_id, run_id, "not_registered", [], [], started, error="not_registered")
            data = self.repository.snapshot(request.scope_node_ids, False)
            candidates = self.repository.candidates(request.scope_node_ids) if request.include_proposed else {"nodes": [], "edges": []}
            if not data["nodes"] and not candidates["nodes"]:
                return self._report(request, request_id, run_id, "not_registered", [], [], started, error="not_registered")
            findings = self._rules(data, request.baseline_revision)
            # Candidate-only evidence is never a fact, but a registered scope
            # must make the insufficiency explicit instead of silently passing.
            if candidates["nodes"] and not data["nodes"]:
                for candidate in candidates["nodes"]:
                    if candidate.get("type") in {"Requirement", "Design", "Code", "Feature"}:
                        rule = "requirement_without_implementation" if candidate.get("type") == "Requirement" else "implementation_without_requirement"
                        findings.append({"fingerprint": self._candidate_fingerprint(rule, candidate, request.baseline_revision), "ruleId": rule, "severity": "warning", "state": "evidence_insufficient", "subject": {"id": candidate.get("id"), "type": candidate.get("type")}, "expected": "approved evidence path", "evidence": [], "confidence": 0.0, "verification": "human approval and provenance are required"})
            findings = self._diff(findings, baseline)
            if len(findings) > request.max_findings: raise ConsistencyError("query_limit_exceeded", 413)
            return self._report(request, request_id, run_id, "ok", findings, self._candidate_refs(candidates), started)
        except ConsistencyError as exc:
            error = exc.code; raise
        except TimeoutError as exc:
            error = "query_timeout"; raise ConsistencyError("query_timeout", 504) from exc
        except Exception as exc:
            error = "service_unavailable"; raise ConsistencyError("service_unavailable", 503) from exc
        finally:
            if error: self._audit(request, request_id, run_id, "error", error, started, {})

    def _run_id(self, request):
        raw = json.dumps({"request": request.__dict__, "ruleVersion": RULE_VERSION}, sort_keys=True, separators=(",", ":"))
        return "CONSISTENCY-" + hashlib.sha256(raw.encode()).hexdigest()[:24]
    @staticmethod
    def _candidate_fingerprint(rule, candidate, basis):
        return fingerprint(rule, candidate.get("id", "unknown"), "approved evidence path", basis)
    def _fact(self, item):
        p = item.get("provenance") or {}
        return item.get("approvalStatus") == "approved" and item.get("extractionMethod") != "ai_inferred" and bool(item.get("evidenceExcerpt")) and all(p.get(k) for k in REQUIRED_PROVENANCE) and item.get("confidence") is not None
    def _rules(self, data, basis):
        nodes, edges = data["nodes"], data["edges"]; byid = {n["id"]: n for n in nodes}; impl = [e for e in edges if e.get("type") == "IMPLEMENTS" and self._fact(e)]
        out = []
        def add(rule, node, expected, severity="warning", evidence=()):
            refs = [self._evidence(byid.get(node["id"]) if node else None)] + [self._evidence(e) for e in evidence]
            refs = [r for r in refs if r]
            out.append({"fingerprint": fingerprint(rule, node["id"], expected, basis), "ruleId": rule, "severity": severity, "state": "open", "subject": {"id": node["id"], "type": node.get("type")}, "expected": expected, "evidence": refs, "confidence": 1.0 if all(self._fact(x) for x in ([node] + list(evidence))) else 0.0, "verification": expected})
        for n in nodes:
            if n.get("type") == "Requirement" and not any(e["to"] == n["id"] and byid.get(e["from"], {}).get("type") in {"Design", "Code"} for e in impl): add("requirement_without_implementation", n, "approved implementation path")
            if n.get("type") == "Design" and not any(e["to"] == n["id"] and byid.get(e["from"], {}).get("type") == "Code" for e in impl): add("design_without_implementation", n, "approved code implementation path")
            if n.get("type") in {"Code", "Feature"} and not any(e["from"] == n["id"] and byid.get(e["to"], {}).get("type") in {"Requirement", "Business", "Design"} for e in impl): add("implementation_without_requirement", n, "approved requirement or business path")
            if not self._fact(n): add("missing_provenance", n, "complete approved provenance", "error")
        for e in edges:
            if not self._fact(e):
                owner = byid.get(e.get("from"));
                if owner: add("missing_provenance", owner, "complete approved relation provenance", "error", [e])
        # Duplicate key assertions are only possible in external stores; retain a deterministic conflict hook.
        seen = {}
        for e in edges:
            key = e.get("key") or f'{e.get("from")}|{e.get("type")}|{e.get("to")}'
            digest = json.dumps({k:e.get(k) for k in ("type","from","to","approvalStatus","evidenceExcerpt","provenance")}, sort_keys=True, default=str)
            if key in seen and seen[key] != digest:
                owner = byid.get(e.get("from"));
                if owner: add("conflicting_evidence", owner, "unique approved evidence", "error", [e])
            seen[key] = digest
        return out
    def _evidence(self, item):
        if not item: return None
        p = item.get("provenance") or {}
        locator = p.get("source_locator") or p.get("sourceLocator")
        if not isinstance(locator, str) or locator.startswith("/") or ".." in locator.split("/"): locator = None
        return {"relationKey": item.get("key") or f'{item.get("from")}|{item.get("type")}|{item.get("to")}' if item.get("from") else None, "sourceId": p.get("source_id") or p.get("sourceId"), "sourceLocator": locator, "sourceAnchor": p.get("source_anchor") or p.get("sourceAnchor")}
    def _candidate_refs(self, candidates):
        return [{"id": x.get("id") or x.get("from"), "type": x.get("type"), "state": "candidate"} for x in candidates.get("nodes", []) + candidates.get("edges", [])]
    def _diff(self, findings, baseline):
        old = {x.get("fingerprint") for x in (baseline or {}).get("findings", [])}
        for f in findings:
            if f.get("state") != "evidence_insufficient": f["state"] = "existing" if f["fingerprint"] in old else "open"
        return sorted(findings, key=lambda x: ({"error":0,"warning":1,"info":2}[x["severity"]], x["ruleId"], x["subject"]["id"], x["fingerprint"]))
    def _report(self, request, request_id, run_id, status, findings, candidates, started, error=None):
        summary = {"error": sum(f["severity"] == "error" for f in findings), "warning": sum(f["severity"] == "warning" for f in findings), "info": sum(f["severity"] == "info" for f in findings), "suppressed": 0}
        report = ConsistencyReport(status, run_id, {"sourceRevision": request.baseline_revision or "unregistered", "includeProposed": request.include_proposed}, findings, candidates, summary, {"requestId": request_id})
        self._audit(request, request_id, run_id, "success", error, started, summary); return report
    def _audit(self, request, request_id, run_id, outcome, error, started, summary):
        self.audit.record({"eventType":"consistency_check", "occurredAt":datetime.now(timezone.utc).isoformat(), "requestId":request_id, "runId":run_id, "repositoryId":request.repository_id, "pullRequestIdPresent":request.pull_request_id is not None, "headShaDigest":hashlib.sha256(request.head_sha.encode()).hexdigest() if request.head_sha else None, "basisRevision":request.baseline_revision, "ruleVersion":RULE_VERSION, "includeProposed":request.include_proposed, "ruleCounts":summary, "severityCounts":summary, "outcome":outcome, "errorCode":error, "durationMs":int((monotonic()-started)*1000), "publisherAttempted":False, "approvalOutcome":"not_requested"})

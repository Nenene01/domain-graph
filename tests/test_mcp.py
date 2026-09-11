import time
import unittest
from datetime import datetime, timezone

from domain_graph.auth import Authenticator, DeploymentPolicy
from domain_graph.ingest import InMemoryGraph
from domain_graph.query_repository import InMemoryQueryRepository
from domain_graph.query_service import QueryService
from domain_graph.mcp_contract import parse_relation_key
from domain_graph.mcp_server import McpServer
from domain_graph.mcp_service import McpToolService, RateLimiter
from domain_graph.audit import MemoryAudit
from domain_graph.query_contract import QueryApiError

class Verifier:
    def __init__(self, claims): self.claims = claims
    def verify(self, token): return self.claims if token == "fixture-token" else (_ for _ in ()).throw(ValueError())

def fixture():
    g=InMemoryGraph(); g.ingest([
        {"id":"REQ-TEST-001","title":"fixture requirement","type":"Requirement","approvalStatus":"approved","extractionMethod":"human_authored","relations":[{"type":"IMPLEMENTS","to":"CODE-TEST-001"}],"provenance":{}},
        {"id":"CODE-TEST-001","title":"fixture code","type":"Code","approvalStatus":"approved","extractionMethod":"deterministic_import","relations":[],"provenance":{}},
    ]); return g

class McpTests(unittest.TestCase):
    def setUp(self):
        self.now=time.time(); claims={"iss":"issuer.fixture.invalid","aud":"domain-graph","exp":self.now+300,"sub":"subject-fixture","tenant_id":"TENANT-FIXTURE","project_ids":["PROJECT-FIXTURE"],"scopes":["domain-graph:read"]}
        self.auth=Authenticator(Verifier(claims),DeploymentPolicy("TENANT-FIXTURE",frozenset({"PROJECT-FIXTURE"}),"issuer.fixture.invalid","domain-graph"))
        self.audit=MemoryAudit(); repo=InMemoryQueryRepository(fixture()); self.service=McpToolService(self.auth,QueryService(repo),repo,self.audit,RateLimiter(1,1,0,0)); self.server=McpServer(self.service)
    def test_only_read_tools_and_injection_is_rejected(self):
        self.assertEqual(set(x["name"] for x in self.server.list_tools()), {"domain_graph.trace","domain_graph.get_provenance","domain_graph.impact"})
        with self.assertRaises(QueryApiError) as caught: self.server.call_tool("write",{},"Bearer fixture-token")
        self.assertEqual(caught.exception.code,"invalid_request")
        with self.assertRaises(QueryApiError): self.server.call_tool("domain_graph.trace",{"startId":"REQ-TEST-001|MATCH (n)"},"Bearer fixture-token")
    def test_auth_scope_and_safe_audit(self):
        with self.assertRaises(QueryApiError) as caught: self.server.call_tool("domain_graph.trace",{"startId":"REQ-TEST-001"},None)
        self.assertEqual(caught.exception.code,"unauthorized"); self.assertNotIn("fixture-token", repr(self.audit.events))
    def test_relation_parser_and_provenance(self):
        self.assertEqual(parse_relation_key("REQ-TEST-001|IMPLEMENTS|CODE-TEST-001")[1],"IMPLEMENTS")
        with self.assertRaises(QueryApiError): parse_relation_key("REQ-TEST-001|DETACH DELETE|CODE-TEST-001")
        result=self.server.call_tool("domain_graph.get_provenance",{"nodeId":"REQ-TEST-001"},"Bearer fixture-token")["structuredContent"]
        self.assertEqual(result["kind"],"node"); self.assertIn("provenance",result)
    def test_rate_limit_blocks_before_second_query(self):
        self.server.call_tool("domain_graph.trace",{"startId":"REQ-TEST-001"},"Bearer fixture-token")
        with self.assertRaises(QueryApiError) as caught: self.server.call_tool("domain_graph.trace",{"startId":"REQ-TEST-001"},"Bearer fixture-token")
        self.assertEqual(caught.exception.code,"rate_limited")

if __name__ == "__main__": unittest.main()

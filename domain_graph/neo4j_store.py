"""Optional Neo4j persistence adapter. The driver is imported only when used."""
from .ingest import InputValidationError, neo4j_statements, normalize_inputs


class Neo4jStore:
    def __init__(self, driver):
        self.driver = driver

    def ingest(self, records):
        nodes, edges = normalize_inputs(records)
        statements = neo4j_statements(nodes, edges)

        def work(tx):
            node_fields = ("type", "title", "approved", "approvalStatus", "extractionMethod", "confidence", "evidenceExcerpt",
                           "sourceId", "sourceType", "sourceLocator", "sourceAnchor", "sourceRevision", "retrievedAt", "updatedBy", "observedAt",
                           "issueStatus", "dueDate", "githubNumber", "githubDatabaseId", "githubSha", "githubState", "githubCreatedAt",
                           "githubUpdatedAt", "githubClosedAt", "githubMergedAt", "githubSubmittedAt", "githubCommittedAt", "githubUrl",
                           "headSha", "mergeCommitSha", "reviewState", "commitSha")
            for node in nodes:
                existing = tx.run("MATCH (n:DomainNode {id:$id}) RETURN n", id=node["id"]).single()
                if existing:
                    current = existing["n"]
                    expected = {field: node.get(field) for field in node_fields if field in node}
                    if any(current.get(field) != value for field, value in expected.items()):
                        raise InputValidationError("conflicting record", code="conflicting_record", record_id=node["id"])
            for edge in edges:
                key = f"{edge['from']}|{edge['type']}|{edge['to']}"
                existing = tx.run("MATCH ()-[r:RELATION {key:$key}]-() RETURN r", key=key).single()
                if existing:
                    current = existing["r"]
                    expected = {"type": edge["type"], "approvalStatus": edge["approvalStatus"], "extractionMethod": edge["extractionMethod"],
                                "confidence": edge["confidence"], "evidenceExcerpt": edge["evidenceExcerpt"],
                                "sourceId": edge["provenance"].get("source_id"), "sourceType": edge["provenance"].get("source_type"),
                                "sourceLocator": edge["provenance"].get("source_locator"), "sourceAnchor": edge["provenance"].get("source_anchor"),
                                "sourceRevision": edge["provenance"].get("source_revision"), "retrievedAt": edge["provenance"].get("retrieved_at"),
                                "updatedBy": edge["provenance"].get("updated_by"), "observedAt": edge["provenance"].get("observed_at")}
                    if any(current.get(field) != value for field, value in expected.items()):
                        raise InputValidationError("conflicting relation", code="conflicting_record", record_id=edge["from"])
            # MATCH in a relationship query otherwise yields zero rows and can
            # silently omit an unknown endpoint. Fail inside the write
            # transaction so every node/edge is rolled back together.
            for query, params in statements:
                tx.run(query, **params).consume()
            target_ids = [edge["to"] for edge in edges]
            result = tx.run(
                "UNWIND $ids AS id OPTIONAL MATCH (n:DomainNode {id:id}) RETURN count(n) AS count",
                ids=target_ids,
            ).single()
            if target_ids and (not result or int(result["count"]) != len(target_ids)):
                raise InputValidationError("unregistered target", code="unknown_target")

        # execute_write commits once, and the driver rolls back the transaction on error.
        with self.driver.session() as session:
            session.execute_write(work)

    def close(self):
        self.driver.close()

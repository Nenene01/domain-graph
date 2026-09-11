"""Optional Neo4j persistence adapter. The driver is imported only when used."""
from .ingest import InputValidationError, neo4j_statements, normalize_inputs


class Neo4jStore:
    def __init__(self, driver):
        self.driver = driver

    def ingest(self, records):
        nodes, edges = normalize_inputs(records)
        statements = neo4j_statements(nodes, edges)

        def work(tx):
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

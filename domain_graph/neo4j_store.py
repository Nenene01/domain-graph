"""Optional Neo4j persistence adapter. The driver is imported only when used."""
from .ingest import neo4j_statements, normalize_inputs


class Neo4jStore:
    def __init__(self, driver):
        self.driver = driver

    def ingest(self, records):
        nodes, edges = normalize_inputs(records)
        statements = neo4j_statements(nodes, edges)

        def work(tx):
            for query, params in statements:
                tx.run(query, **params).consume()

        # execute_write commits once, and the driver rolls back the transaction on error.
        with self.driver.session() as session:
            session.execute_write(work)

    def close(self):
        self.driver.close()

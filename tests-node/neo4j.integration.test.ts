import { describe, expect, it } from 'vitest';
import { readConfig } from '../src/config.js';
import { createNeo4jDriver, verifyNeo4jConnection } from '../src/neo4j.js';

describe('Neo4j connectivity', () => {
  it('connects to the configured Neo4j instance', async () => {
    if (process.env.RUN_NEO4J_TESTS !== 'true') {
      return;
    }

    const driver = createNeo4jDriver(readConfig());
    try {
      await expect(verifyNeo4jConnection(driver)).resolves.toBeUndefined();
    } finally {
      await driver.close();
    }
  });
});

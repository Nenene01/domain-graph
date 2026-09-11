import { describe, expect, it } from 'vitest';
import { readConfig } from '../src/config.js';

describe('readConfig', () => {
  it('reads required Neo4j settings without logging secrets', () => {
    const config = readConfig({
      NODE_ENV: 'test',
      NEO4J_URI: 'bolt://localhost:7687',
      NEO4J_USERNAME: 'neo4j',
      NEO4J_PASSWORD: 'test-secret',
    });

    expect(config).toEqual({
      nodeEnv: 'test',
      neo4jUri: 'bolt://localhost:7687',
      neo4jUsername: 'neo4j',
      neo4jPassword: 'test-secret',
    });
  });

  it('rejects missing credentials', () => {
    expect(() => readConfig({ NEO4J_URI: 'bolt://localhost:7687' })).toThrow(
      'Missing required environment variable: NEO4J_USERNAME',
    );
  });
});

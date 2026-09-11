import neo4j, { type Driver } from 'neo4j-driver';
import type { AppConfig } from './config.js';

export const createNeo4jDriver = (config: AppConfig): Driver =>
  neo4j.driver(
    config.neo4jUri,
    neo4j.auth.basic(config.neo4jUsername, config.neo4jPassword),
  );

/** Verify connectivity without exposing connection details or credentials. */
export const verifyNeo4jConnection = async (driver: Driver): Promise<void> => {
  await driver.verifyConnectivity();
};

import 'dotenv/config';

export interface AppConfig {
  nodeEnv: string;
  neo4jUri: string;
  neo4jUsername: string;
  neo4jPassword: string;
}

const required = (name: string, value: string | undefined): string => {
  if (!value?.trim()) {
    throw new Error(`Missing required environment variable: ${name}`);
  }
  return value;
};

export const readConfig = (env: NodeJS.ProcessEnv = process.env): AppConfig => ({
  nodeEnv: env.NODE_ENV?.trim() || 'development',
  neo4jUri: required('NEO4J_URI', env.NEO4J_URI),
  neo4jUsername: required('NEO4J_USERNAME', env.NEO4J_USERNAME),
  neo4jPassword: required('NEO4J_PASSWORD', env.NEO4J_PASSWORD),
});

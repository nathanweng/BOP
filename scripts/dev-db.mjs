import EmbeddedPostgres from 'embedded-postgres';
import { mkdir, access } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const databaseDir = resolve(root, '.data/postgres');
const socketDir = resolve(root, '.data/pg-socket');
await mkdir(socketDir, { recursive: true });
const database = new EmbeddedPostgres({
  databaseDir,
  user: process.env.POSTGRES_USER || 'bop',
  password: process.env.POSTGRES_PASSWORD || 'bop_local',
  port: Number(process.env.POSTGRES_PORT || 5432),
  persistent: true,
  createPostgresUser: false,
  postgresFlags: ['-h', '127.0.0.1', '-k', socketDir],
});
const initialized = await access(resolve(databaseDir, 'PG_VERSION')).then(() => true, () => false);
if (!initialized) await database.initialise();
await database.start();
let stopping = false;
async function stop() {
  if (stopping) return;
  stopping = true;
  await database.stop();
  process.exit(0);
}
process.once('SIGINT', stop);
process.once('SIGTERM', stop);
const name = process.env.POSTGRES_DB || 'bop';
const client = database.getPgClient();
try {
  await client.connect();
  const found = await client.query('SELECT 1 FROM pg_database WHERE datname = $1', [name]);
  if (found.rowCount === 0) await database.createDatabase(name);
  console.log(`Local PostgreSQL ready on 127.0.0.1:${process.env.POSTGRES_PORT || 5432}. Data persists in .data/postgres. Ctrl+C stops it.`);
} catch (error) {
  await database.stop();
  throw error;
} finally {
  await client.end();
}

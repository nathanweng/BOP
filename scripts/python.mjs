import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import ffmpeg from 'ffmpeg-static';
import ffprobe from 'ffprobe-static';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const python = resolve(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
if (!existsSync(python)) {
  console.error('Create .venv and install apps/api[test] first. See README.MD.');
  process.exit(1);
}
const environment = {
  ...process.env,
  DATABASE_URL: process.env.DATABASE_URL || 'postgresql+psycopg://bop:bop_local@127.0.0.1:5432/bop',
  FFMPEG_BINARY: process.env.FFMPEG_BINARY || ffmpeg,
  FFPROBE_BINARY: process.env.FFPROBE_BINARY || ffprobe.path,
  MEDIA_ROOT: process.env.MEDIA_ROOT || resolve(root, '.data/media'),
};

function run(args) {
  return new Promise((resolveExit, reject) => {
    const child = spawn(python, args, { cwd: resolve(root, 'apps/api'), env: environment, stdio: 'inherit' });
    const stop = () => child.kill('SIGTERM');
    process.once('SIGINT', stop);
    process.once('SIGTERM', stop);
    child.once('error', reject);
    child.once('exit', (code) => {
      process.off('SIGINT', stop);
      process.off('SIGTERM', stop);
      resolveExit(code ?? 1);
    });
  });
}

const [mode, ...args] = process.argv.slice(2);
if (mode === 'test') {
  process.exitCode = await run(['-m', 'pytest', ...args]);
} else if (mode === 'migrate') {
  process.exitCode = await run(['-m', 'alembic', 'upgrade', 'head']);
} else if (mode === 'dev') {
  const code = await run(['-m', 'alembic', 'upgrade', 'head']);
  process.exitCode = code || await run(['-m', 'uvicorn', 'bop_api.main:app', '--host', '127.0.0.1', '--port', '8000', '--reload', ...args]);
} else {
  console.error('Expected dev, migrate, or test.');
  process.exitCode = 1;
}

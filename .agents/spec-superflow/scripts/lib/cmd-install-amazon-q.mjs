// ssf install-amazon-q — deploy spec-superflow for Amazon Q Developer. Delegates to scripts/install-amazon-q.mjs
import { execFileSync } from 'node:child_process';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const installScript = join(__dirname, '..', 'install-amazon-q.mjs');

export async function run(args) {
  execFileSync(process.execPath, [installScript, ...args], { stdio: 'inherit' });
}

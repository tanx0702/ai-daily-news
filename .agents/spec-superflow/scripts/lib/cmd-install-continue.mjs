// ssf install-continue — deploy spec-superflow for Continue. Delegates to scripts/install-continue.mjs
import { execFileSync } from 'node:child_process';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const installScript = join(__dirname, '..', 'install-continue.mjs');

export async function run(args) {
  execFileSync(process.execPath, [installScript, ...args], { stdio: 'inherit' });
}

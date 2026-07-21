// ssf install-pi — deploy spec-superflow for Pi agent. Delegates to scripts/install-pi.mjs
import { execFileSync } from 'node:child_process';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const installScript = join(__dirname, '..', 'install-pi.mjs');

export async function run(args) {
  execFileSync(process.execPath, [installScript, ...args], { stdio: 'inherit' });
}

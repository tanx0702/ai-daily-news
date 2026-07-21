// ssf install-zcode — deploy spec-superflow skills/rules/scripts for ZCODE
// Delegates to scripts/install-zcode.mjs
import { execFileSync } from 'node:child_process';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const installScript = join(__dirname, '..', 'install-zcode.mjs');

export async function run(args) {
  execFileSync(process.execPath, [installScript, ...args], {
    stdio: 'inherit',
  });
}

import fs from 'node:fs';
import path from 'node:path';
import { readState } from '../../lib/state-loader.mjs';
import { computeContractHash } from '../../lib/hash.mjs';

export function checkContractCurrent(changeDir) {
  const contractPath = path.join(changeDir, 'execution-contract.md');
  if (!fs.existsSync(contractPath)) {
    return { pass: false, failures: ['execution-contract.md: missing'] };
  }

  const content = fs.readFileSync(contractPath, 'utf-8').trim();
  if (content.length === 0) {
    return { pass: false, failures: ['execution-contract.md: empty'] };
  }

  const state = readState(changeDir);
  const current = computeContractHash(changeDir);
  if (!state.contract_hash || state.contract_hash === 'null') {
    return {
      pass: false,
      failures: ['execution-contract.md is not recorded in state. Run ssf state init <change-dir> after contract generation.'],
    };
  }
  if (state.contract_hash !== current) {
    return {
      pass: false,
      failures: ['execution-contract.md is stale: contract hash mismatch. Re-run contract-builder or ssf state rebuild.'],
    };
  }

  return { pass: true, failures: [] };
}

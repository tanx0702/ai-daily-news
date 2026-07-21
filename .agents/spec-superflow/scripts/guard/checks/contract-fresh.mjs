// scripts/guard/checks/contract-fresh.mjs — check contract staleness via hash comparison
import { isContractFresh } from '../../lib/hash.mjs';

/**
 * Compare stored artifacts_hash in .spec-superflow.yaml against current artifact hashes.
 * Returns { pass, failures[] }.
 */
export function checkContractFresh(changeDir) {
  const fresh = isContractFresh(changeDir);
  if (fresh) {
    return { pass: true, failures: [] };
  }
  return {
    pass: false,
    failures: ['execution-contract.md is stale: artifacts hash mismatch. Re-run contract-builder to regenerate.'],
  };
}
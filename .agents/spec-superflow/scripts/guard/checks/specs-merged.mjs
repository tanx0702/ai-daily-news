// scripts/guard/checks/specs-merged.mjs — block closing while delta specs are unmerged
import { readState } from '../../lib/state-loader.mjs';
import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';

// A delta spec block is introduced by any of these requirement headers.
const DELTA_RE = /^##\s+(ADDED|MODIFIED|REMOVED|RENAMED)\s+Requirements/m;

function collectSpecFiles(specsDir) {
  const out = [];
  const entries = readdirSync(specsDir, { recursive: true, withFileTypes: true });
  for (const e of entries) {
    if (e.isFile() && /\.(md|markdown)$/i.test(e.name)) {
      // With recursive readdir, e.parentPath gives the directory.
      const dir = typeof e.parentPath === 'string' ? e.parentPath : specsDir;
      out.push(join(dir, e.name));
    }
  }
  return out;
}

function hasDeltaSpecs(changeDir) {
  const specsDir = join(changeDir, 'specs');
  if (!existsSync(specsDir)) return false;
  for (const file of collectSpecFiles(specsDir)) {
    const content = readFileSync(file, 'utf-8');
    if (DELTA_RE.test(content)) return true;
  }
  return false;
}

/**
 * Returns { pass, failures[] }.
 * Passes when either spec_merged is recorded, or there are no delta specs to merge.
 * Blocks `executing → closing` when delta specs exist but spec-merger hasn't run.
 */
export function checkSpecsMerged(changeDir) {
  const state = readState(changeDir);
  if (state.spec_merged === true || state.spec_merged === 'true') {
    return { pass: true, failures: [] };
  }
  if (!hasDeltaSpecs(changeDir)) {
    return { pass: true, failures: [] };
  }
  return {
    pass: false,
    failures: [
      'Delta specs exist in specs/ but spec-merger has not run (spec_merged not recorded). Run spec-merger to merge ADDED/MODIFIED/REMOVED/RENAMED requirements before closing.',
    ],
  };
}

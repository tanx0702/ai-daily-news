// ssf list — scan changes/ and report status
import { readdirSync, existsSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { loadConfig } from './config-loader.mjs';
import { readState } from './state-loader.mjs';

function detectChangeStatus(changeDir) {
  // Prefer the state file over file-existence heuristics.
  // The state file is maintained by ssf state transition and is the
  // authoritative source for the current workflow phase.
  const stateFile = join(changeDir, '.spec-superflow.yaml');
  if (existsSync(stateFile)) {
    const state = readState(changeDir);
    const st = state.state || 'exploring';
    const workflow = state.workflow || 'full';
    const detail = workflow === 'full' ? '' : ` (${workflow})`;
    const STATUS_MAP = {
      exploring: 'EXPLORING',
      specifying: 'SPECIFYING',
      bridging: 'BRIDGED',
      'approved-for-build': 'APPROVED',
      executing: 'EXECUTING',
      debugging: 'DEBUGGING',
      closing: 'CLOSED',
      abandoned: 'ABANDONED',
    };
    return {
      status: STATUS_MAP[st] || 'UNKNOWN',
      detail: `${st}${detail}`,
    };
  }

  // Fallback: infer from file existence when no state file exists
  const hasProposal = existsSync(join(changeDir, 'proposal.md'));
  const hasContract = existsSync(join(changeDir, 'execution-contract.md'));
  const hasAbandonment = existsSync(join(changeDir, 'abandonment-summary.md'));

  if (hasAbandonment) return { status: 'ABANDONED', detail: 'Change was abandoned' };
  if (!hasProposal) return { status: 'INCOMPLETE', detail: 'Missing proposal.md' };
  if (!hasContract) return { status: 'SPECIFYING', detail: 'Planning in progress (no state file)' };
  return { status: 'UNKNOWN', detail: 'Has artifacts but no state file — run ssf state init' };
}

export { detectChangeStatus };

export async function run(args) {
  const config = loadConfig(process.cwd());
  const changesDir = join(process.cwd(), 'changes');

  if (!existsSync(changesDir)) {
    console.log('No changes/ directory found.');
    return;
  }

  const dirs = readdirSync(changesDir).filter(f => {
    try { return statSync(join(changesDir, f)).isDirectory(); } catch { return false; }
  });

  if (dirs.length === 0) {
    console.log('No changes found in changes/');
    return;
  }

  console.log('Changes:');
  for (const dir of dirs) {
    const changeDir = join(changesDir, dir);
    const { status, detail } = detectChangeStatus(changeDir);
    const icon = status === 'CLOSED' ? '✅' : status === 'ABANDONED' ? '🚫' : status === 'APPROVED' ? '🔒' : status === 'EXECUTING' ? '⚡' : status === 'DEBUGGING' ? '🐛' : status === 'EXPLORING' ? '🔍' : status === 'SPECIFYING' ? '📝' : status === 'BRIDGED' ? '🌉' : status === 'UNKNOWN' ? '❓' : '⚠️';
    console.log(`  ${icon} ${dir}  [${status}]  ${detail}`);
  }
}

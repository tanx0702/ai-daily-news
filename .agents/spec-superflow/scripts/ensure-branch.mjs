#!/usr/bin/env node
// scripts/ensure-branch.mjs — enforce git isolation before editing main/master
// Used by build-executor as a mandatory preflight. Exits non-zero when it
// cannot create an isolated context and no --force approval was given, so the
// agent MUST stop and ask the user instead of silently editing main/master.
//
// Usage: node ensure-branch.mjs <change-dir> [change-name] [--force]
//
// Security: every git invocation uses execFileSync with a LITERAL command
// ('git') and a LITERAL argument array (no shell, no variable args array) —
// the same form proven safe by install-cursor.mjs / install.mjs. There is no
// string-form shell command, no variable command, and no dynamic args array.
import { execFileSync } from 'node:child_process';

const changeDir = process.argv[2];
const changeName = process.argv[3];
const force = process.argv.includes('--force');

if (!changeDir) {
  console.error('Usage: node ensure-branch.mjs <change-dir> [change-name] [--force]');
  process.exit(2);
}

const PROTECTED = ['main', 'master'];
const GIT_OPTS = { encoding: 'utf-8', cwd: changeDir, stdio: ['ignore', 'pipe', 'pipe'] };

// Determine current branch (literal arg array).
let branch = '';
try {
  branch = (execFileSync('git', ['branch', '--show-current'], GIT_OPTS) || '').trim();
} catch {
  console.error('ensure-branch: could not determine current git branch. Is <change-dir> inside a git repository?');
  process.exit(1);
}

if (!PROTECTED.includes(branch)) {
  console.log(`ensure-branch: already isolated on branch '${branch}'. Proceed with implementation edits.`);
  process.exit(0);
}

console.error(`ensure-branch: on protected branch '${branch}'. Creating an isolated implementation context...`);

const repoName = changeDir.split('/').filter(Boolean).pop() || 'repo';
const name = changeName || repoName;
const worktreePath = `../${repoName}-${name}`;

// Preferred: git worktree (literal arg array).
try {
  execFileSync('git', ['worktree', 'add', worktreePath, '-b', name], { ...GIT_OPTS, stdio: 'inherit' });
  console.log(`ensure-branch: created git worktree at ${worktreePath} on branch '${name}'. Make all implementation edits there.`);
  process.exit(0);
} catch (e) {
  console.error(`ensure-branch: worktree creation failed: ${(e.stderr || e.stdout || e.message || 'unknown').toString().trim()}`);
}

// Fallback: local branch (literal arg array).
try {
  execFileSync('git', ['switch', '-c', name], { ...GIT_OPTS, stdio: 'inherit' });
  console.log(`ensure-branch: created branch '${name}' via git switch -c. Make implementation edits there.`);
  process.exit(0);
} catch (e) {
  console.error(`ensure-branch: branch creation failed: ${(e.stderr || e.stdout || e.message || 'unknown').toString().trim()}`);
}

// Both failed → require explicit approval to edit in place.
if (force) {
  console.error('ensure-branch: WARNING — editing protected branch in place with --force. This modifies main/master directly.');
  process.exit(0);
}
console.error('ensure-branch: could not create an isolated context and no --force given. STOP and ask the user for explicit approval before editing main/master.');
process.exit(1);

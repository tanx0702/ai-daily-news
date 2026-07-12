// scripts/guard/checks/tasks-complete.mjs — verify all tasks in tasks.md are checked off
import fs from 'node:fs';
import path from 'node:path';

/**
 * Check that tasks.md has no unchecked items and at least one completed item.
 * Returns { pass, failures[] }.
 */
export function checkTasksComplete(changeDir) {
  const tasksPath = path.join(changeDir, 'tasks.md');
  if (!fs.existsSync(tasksPath)) {
    return { pass: false, failures: ['tasks.md: missing'] };
  }

  const content = fs.readFileSync(tasksPath, 'utf-8');
  const unchecked = content.match(/^[ \t]*- \[ \]/gm);

  if (unchecked && unchecked.length > 0) {
    return {
      pass: false,
      failures: [`tasks.md: ${unchecked.length} unchecked task(s) remaining`],
    };
  }

  const hasAny = content.match(/^[ \t]*- \[[xX]\]/gm);
  if (!hasAny) {
    return { pass: false, failures: ['tasks.md: no completed tasks found'] };
  }

  return { pass: true, failures: [] };
}
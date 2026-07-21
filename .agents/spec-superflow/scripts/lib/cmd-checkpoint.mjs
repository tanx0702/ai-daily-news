// scripts/lib/cmd-checkpoint.mjs - ssf checkpoint recovery records
import { parseArgs } from 'node:util';
import { getCheckpoint, listCheckpoints, saveCheckpoint } from './sdd-overlay.mjs';

const USAGE = 'Usage: ssf checkpoint <save|list|show> <change-dir> [options]';

export async function run(args) {
  const { positionals, values } = parseArgs({
    args,
    allowPositionals: true,
    options: {
      task: { type: 'string' }, next: { type: 'string' }, completed: { type: 'string' },
      verification: { type: 'string' }, review: { type: 'string' }, risk: { type: 'string' },
      'commit-start': { type: 'string' }, 'commit-end': { type: 'string' },
      json: { type: 'boolean', default: false },
    },
  });

  const subcommand = positionals[0];
  const changeDir = positionals[1];
  if (!subcommand || !changeDir) usage();

  if (!['save', 'list', 'show'].includes(subcommand)) {
    console.error(`Unknown checkpoint subcommand: ${subcommand}. Valid: save, list, show`);
    process.exit(2);
  }

  if (subcommand === 'save') {
    if (!hasText(values.task) || !hasText(values.next)) {
      console.error('Usage: ssf checkpoint save <change-dir> --task <id> --next <text>');
      process.exit(2);
    }

    const checkpoint = saveCheckpoint(changeDir, {
      taskId: values.task,
      next: values.next,
      completed: values.completed,
      evidence: values.verification,
      review: values.review,
      risk: values.risk,
      commitStart: values['commit-start'],
      commitEnd: values['commit-end'],
    });
    if (values.json) {
      console.log(JSON.stringify({ ok: true, checkpoint }));
    } else {
      console.log(`Checkpoint saved: ${checkpoint.task_id}`);
    }
    return;
  }

  if (subcommand === 'list') {
    const checkpoints = listCheckpoints(changeDir);
    if (values.json) {
      console.log(JSON.stringify({ ok: true, checkpoints }));
    } else if (checkpoints.length === 0) {
      console.log('No checkpoints found.');
    } else {
      for (const checkpoint of checkpoints) {
        console.log(`${checkpoint.task_id}  status=${checkpoint.stale ? 'stale' : 'current'}  stale=${checkpoint.stale}`);
      }
    }
    return;
  }

  const taskId = positionals[2];
  if (!taskId) usage('Usage: ssf checkpoint show <change-dir> <id>');
  const checkpoint = getCheckpoint(changeDir, taskId);
  if (!checkpoint) {
    console.error(`Checkpoint '${taskId}' was not found`);
    process.exit(1);
  }

  if (values.json) {
    console.log(JSON.stringify({ ok: true, checkpoint }));
  } else {
    console.log(renderCheckpoint(checkpoint));
  }
}

function hasText(value) {
  return typeof value === 'string' && value.trim().length > 0;
}

function usage(message = USAGE) {
  console.error(message);
  process.exit(2);
}

function renderCheckpoint(checkpoint) {
  return [
    `# Checkpoint: ${checkpoint.task_id}`,
    '',
    `Status: ${checkpoint.stale ? 'stale' : 'current'}`,
    `Created: ${checkpoint.created_at}`,
    '',
    '## Next',
    checkpoint.next,
    '',
    '## Completed',
    checkpoint.completed,
    '',
    '## Evidence',
    checkpoint.evidence,
    '',
    '## Review',
    checkpoint.review,
    '',
    '## Risk',
    checkpoint.risk,
    '',
    '## Commit Start',
    checkpoint.commit_start,
    '',
    '## Commit End',
    checkpoint.commit_end,
  ].join('\n');
}

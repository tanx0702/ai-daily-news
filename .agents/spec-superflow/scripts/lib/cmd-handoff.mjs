// ssf handoff contract lifecycle commands.
import { parseArgs } from 'node:util';
import {
  HANDOFF_DECISIONS, HANDOFF_TYPES, createHandoff, finishHandoff, listHandoffs, resolveHandoff,
} from './sdd-overlay.mjs';

const USAGE = 'Usage: ssf handoff <create|list|finish|resolve> <change-dir> [options]';

export async function run(args) {
  const { positionals, values } = parseArgs({
    args,
    allowPositionals: true,
    options: {
      type: { type: 'string' }, objective: { type: 'string' },
      'expected-output': { type: 'string' }, acceptance: { type: 'string' },
      boundary: { type: 'string', multiple: true }, decision: { type: 'string' },
      'acknowledge-source-drift': { type: 'boolean', default: false },
      json: { type: 'boolean', default: false },
    },
  });

  const subcommand = positionals[0];
  const changeDir = positionals[1];
  if (!subcommand || !changeDir) usage();

  if (!['create', 'list', 'finish', 'resolve'].includes(subcommand)) {
    console.error(`Unknown handoff subcommand: ${subcommand}. Valid: create, list, finish, resolve`);
    process.exit(2);
  }

  if (subcommand === 'create') {
    if (!hasText(values.type) || !hasText(values.objective)
      || !hasText(values['expected-output']) || !hasText(values.acceptance)) {
      usage('Usage: ssf handoff create <change-dir> --type <type> --objective <text> --expected-output <text> --acceptance <text>');
    }
    if (!HANDOFF_TYPES.has(values.type)) {
      throw new Error(`Unsupported handoff type '${values.type}'`);
    }

    const handoff = createHandoff(changeDir, {
      type: values.type,
      title: values.objective,
      question: `Expected output: ${values['expected-output']}`,
      context: `Acceptance: ${values.acceptance}`,
      source: renderBoundaries(values.boundary),
    });
    output(values.json, { ok: true, handoff }, `Handoff created: ${handoff.id}`);
    return;
  }

  if (subcommand === 'list') {
    const handoffs = listHandoffs(changeDir);
    output(values.json, { ok: true, handoffs }, handoffs.length === 0
      ? 'No handoffs found.'
      : handoffs.map(handoff => `${handoff.id}  type=${handoff.type}  status=${handoff.status}`).join('\n'));
    return;
  }

  const id = positionals[2];
  if (!hasText(id)) usage(`Usage: ssf handoff ${subcommand} <change-dir> <id>${subcommand === 'resolve' ? ' --decision <accept|reject|defer>' : ''}`);

  if (subcommand === 'finish') {
    const handoff = finishHandoff(changeDir, id);
    output(values.json, { ok: true, handoff }, `Handoff finished: ${handoff.id}`);
    return;
  }

  if (!hasText(values.decision)) {
    usage('Usage: ssf handoff resolve <change-dir> <id> --decision <accept|reject|defer>');
  }
  if (!HANDOFF_DECISIONS.has(values.decision)) {
    throw new Error(`Unsupported handoff decision '${values.decision}'`);
  }

  const handoff = resolveHandoff(changeDir, id, values.decision, values['acknowledge-source-drift']);
  output(values.json, { ok: true, handoff }, `Handoff resolved: ${handoff.id} (${handoff.decision})`);
}

function hasText(value) {
  return typeof value === 'string' && value.trim().length > 0;
}

function renderBoundaries(boundaries) {
  return boundaries?.length ? `Boundaries:\n${boundaries.map(boundary => `- ${boundary}`).join('\n')}` : 'Not recorded';
}

function output(json, payload, text) {
  console.log(json ? JSON.stringify(payload) : text);
}

function usage(message = USAGE) {
  console.error(message);
  process.exit(2);
}

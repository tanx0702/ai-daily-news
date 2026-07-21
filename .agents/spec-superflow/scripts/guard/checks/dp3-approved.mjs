import { readState } from '../../lib/state-loader.mjs';

export function checkDp3Approved(changeDir) {
  const state = readState(changeDir);
  const decision = typeof state.dp_3_result === 'string' ? state.dp_3_result.trim() : '';

  if (!decision) {
    return {
      pass: false,
      failures: ['DP-3 (dp_3_result) is not recorded — minimal contract approval is required before hotfix build'],
    };
  }

  if (!/^approved\b/i.test(decision)) {
    return {
      pass: false,
      failures: [`DP-3 (dp_3_result) must start with "approved"; got: ${decision}`],
    };
  }

  return { pass: true, failures: [] };
}

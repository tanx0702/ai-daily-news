// scripts/guard/checks/tests-passing.mjs — verify verification outcome is recorded as pass
import { readState } from '../../lib/state-loader.mjs';

/**
 * Check that .spec-superflow.yaml records a passing verification outcome.
 *
 * Historically only `test_result: pass` was accepted, but release-archivist
 * records the verification result in `dp_6_result` (e.g. "pass: ..."). The
 * closing transition was unreachable because nothing ever wrote `test_result`,
 * so we accept either signal. See BUG-A (issue #28 root cause).
 *
 * Returns { pass, failures[] }.
 */
export function checkTestsPassing(changeDir) {
  const state = readState(changeDir);
  const markers = [state.test_result, state.dp_6_result];
  const passed = markers.some((v) => {
    if (typeof v !== 'string') return false;
    const normalized = v.trim().replace(/^["']|["']$/g, '');
    return normalized.toLowerCase().startsWith('pass');
  });
  if (passed) {
    return { pass: true, failures: [] };
  }
  return {
    pass: false,
    failures: [
      `verification outcome missing: test_result is '${state.test_result || 'null'}' and dp_6_result is '${state.dp_6_result || 'null'}' — expected a result starting with 'pass'. Run release-archivist verification (DP-6) first.`,
    ],
  };
}
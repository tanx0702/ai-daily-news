# Issue 15 Guard Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship spec-superflow `0.8.10` with guarded fast-path transitions, fail-closed state transitions, build-executor branch isolation guidance, synchronized version metadata, and a PR/tag release path.

**Architecture:** Keep the fix small and local. `scripts/guard/guard.mjs` owns transition legality, `scripts/lib/cmd-state.mjs` owns safe state writes, `skills/build-executor/SKILL.md` owns implementation-phase process discipline, and release/version files document the patch.

**Tech Stack:** Node.js 22 native test runner, ESM scripts, shell/git/GitHub CLI, Markdown skills/docs.

---

## File Structure

- Modify `scripts/guard/guard.mjs`: add workflow-mode constraints for fast-path transitions and return structured guard failures.
- Modify `tests/lib/guard-transitions.test.mjs`: add fast-path mode-gating regression tests.
- Modify `scripts/lib/cmd-state.mjs`: make guard execution fail closed before writing `.spec-superflow.yaml`.
- Modify `tests/lib/cmd-state.test.mjs`: add CLI-level regression tests that state is not changed on rejected fast-path transitions.
- Modify `skills/build-executor/SKILL.md`: add branch/worktree preflight before implementation.
- Modify `CHANGELOG.md`: add `0.8.10` release entry.
- Modify versioned files via `node scripts/spec-superflow.mjs version 0.8.10`: `package.json`, manifests, README/INSTALL/docs, phase guard files, hook version text.

## Task 1: Guard Fast-Path Mode Tests

**Files:**
- Modify: `tests/lib/guard-transitions.test.mjs`

- [ ] **Step 1: Add failing mode-gating tests**

Insert these tests inside the existing `describe('Fast-path validation', () => { ... })` block after the two support tests:

```js
  it('SHALL reject hotfix fast-path when workflow is full', () => {
    const result = runGuard('exploring', 'bridging', dir, 'full');
    assert.equal(result.ok, false, 'exploring -> bridging must be rejected in full workflow');
    assert.match(result.stderr, /workflow-mode|fast-path|hotfix|tweak/i);
  });

  it('SHALL reject tweak fast-path when workflow is full', () => {
    const result = runGuard('exploring', 'approved-for-build', dir, 'full');
    assert.equal(result.ok, false, 'exploring -> approved-for-build must be rejected in full workflow');
    assert.match(result.stderr, /workflow-mode|fast-path|tweak/i);
  });
```

- [ ] **Step 2: Run the focused test and verify failure**

Run:

```bash
node --test --experimental-strip-types tests/lib/guard-transitions.test.mjs --test-name-pattern="Fast-path validation"
```

Expected: the two new tests fail because both transitions currently pass in `full`.

- [ ] **Step 3: Commit the failing tests**

```bash
git add tests/lib/guard-transitions.test.mjs
git commit -m "test: cover fast-path workflow gating"
```

## Task 2: Guard Fast-Path Mode Implementation

**Files:**
- Modify: `scripts/guard/guard.mjs`
- Test: `tests/lib/guard-transitions.test.mjs`

- [ ] **Step 1: Add mode constraints and helper**

In `scripts/guard/guard.mjs`, after `TRANSITION_CHECKS`, add:

```js
const TRANSITION_WORKFLOW_REQUIREMENTS = {
  'exploring:bridging': ['hotfix', 'tweak'],
  'exploring:approved-for-build': ['tweak'],
};

function checkWorkflowAllowed(key, workflow) {
  const allowed = TRANSITION_WORKFLOW_REQUIREMENTS[key];
  if (!allowed || allowed.includes(workflow)) {
    return { pass: true, checks: [] };
  }

  return {
    pass: false,
    checks: [{
      dimension: 'workflow-mode',
      pass: false,
      failures: [
        `${key.replace(':', ' -> ')} is a fast-path transition allowed only for workflow ${allowed.join(' or ')}; current workflow is ${workflow}`,
      ],
    }],
  };
}
```

- [ ] **Step 2: Call helper after transition lookup**

In `main()`, immediately after the `if (!dimensions) { ... }` block and before `if (dimensions.length === 0)`, add:

```js
  const workflowCheck = checkWorkflowAllowed(key, workflow);
  if (!workflowCheck.pass) {
    if (useJson) {
      console.log(JSON.stringify(workflowCheck, null, 2));
    } else {
      console.error('Guard checks failed:');
      for (const c of workflowCheck.checks) {
        for (const f of c.failures) {
          console.error(`  [FAIL] ${c.dimension}: ${f}`);
        }
      }
    }
    process.exit(1);
  }
```

- [ ] **Step 3: Run focused tests**

Run:

```bash
node --test --experimental-strip-types tests/lib/guard-transitions.test.mjs --test-name-pattern="Fast-path validation"
```

Expected: all Fast-path validation tests pass.

- [ ] **Step 4: Run guard transition tests**

Run:

```bash
node --test --experimental-strip-types tests/lib/guard-transitions.test.mjs
```

Expected: all tests in this file pass.

- [ ] **Step 5: Commit implementation**

```bash
git add scripts/guard/guard.mjs tests/lib/guard-transitions.test.mjs
git commit -m "fix: gate fast-path transitions by workflow mode"
```

## Task 3: State Transition Fail-Closed Tests

**Files:**
- Modify: `tests/lib/cmd-state.test.mjs`

- [ ] **Step 1: Add CLI fast-path rejection tests**

Inside `describe('cmd-state: transition', () => { ... })`, after the existing `persists state across invocations` test, add:

```js
  it('rejects exploring to approved-for-build when workflow is auto', () => {
    rmSync(join(tempDir, '.spec-superflow.yaml'), { force: true });
    ssf(`state init ${tempDir}`);

    const result = ssf(`state transition ${tempDir} approved-for-build`);
    assert.equal(result.exitCode, 1);
    assert.match(result.stderr || result.stdout, /workflow-mode|fast-path|tweak/i);

    const check = ssf(`state get ${tempDir} state`);
    assert.equal(check.stdout.trim(), 'exploring');
  });

  it('rejects exploring to bridging when workflow is full', () => {
    rmSync(join(tempDir, '.spec-superflow.yaml'), { force: true });
    ssf(`state init ${tempDir}`);
    ssf(`state set ${tempDir} workflow full`);

    const result = ssf(`state transition ${tempDir} bridging`);
    assert.equal(result.exitCode, 1);
    assert.match(result.stderr || result.stdout, /workflow-mode|fast-path|hotfix|tweak/i);

    const check = ssf(`state get ${tempDir} state`);
    assert.equal(check.stdout.trim(), 'exploring');
  });
```

- [ ] **Step 2: Run focused test and verify current behavior**

Run:

```bash
node --test --experimental-strip-types tests/lib/cmd-state.test.mjs --test-name-pattern="cmd-state: transition"
```

Expected: tests fail if `cmd-state` still writes state after guard failure. If Task 2 is already implemented, the first failure may already be fixed; continue with Task 4 for fail-closed hardening.

- [ ] **Step 3: Commit tests**

```bash
git add tests/lib/cmd-state.test.mjs
git commit -m "test: cover state transition fast-path rejection"
```

## Task 4: State Transition Fail-Closed Implementation

**Files:**
- Modify: `scripts/lib/cmd-state.mjs`
- Test: `tests/lib/cmd-state.test.mjs`

- [ ] **Step 1: Replace fail-open guard handling**

In `scripts/lib/cmd-state.mjs`, replace the block from `// If guard fails with exit 2...` through the closing brace before `state.state = toState;` with:

```js
      const guardOutput = guardResult.stdout.toString();
      let parsed;
      try {
        parsed = JSON.parse(guardOutput);
      } catch {
        const stderr = guardResult.stderr.toString().trim();
        console.error(`Guard check failed for ${fromState} -> ${toState}:`);
        console.error('  [guard-error] Guard did not return valid JSON.');
        if (stderr) console.error(`  ${stderr}`);
        process.exit(1);
      }

      if (guardResult.status !== 0 || !parsed.pass) {
        const failures = (parsed.checks || [])
          .filter(c => !c.pass)
          .flatMap(c => (c.failures || []).map(f => `[${c.dimension}] ${f}`));
        console.error(`Guard check failed for ${fromState} -> ${toState}:`);
        for (const f of failures) console.error(`  ${f}`);
        if (parsed.error) console.error(`  ${parsed.error}`);
        if (failures.length === 0 && !parsed.error) {
          console.error('  [guard-error] Guard failed without a structured failure message.');
        }
        process.exit(1);
      }
```

This parses guard output for every transition, not only when the exit status is non-zero.

- [ ] **Step 2: Run focused state tests**

Run:

```bash
node --test --experimental-strip-types tests/lib/cmd-state.test.mjs --test-name-pattern="cmd-state: transition"
```

Expected: all transition tests pass.

- [ ] **Step 3: Run full state command tests**

Run:

```bash
node --test --experimental-strip-types tests/lib/cmd-state.test.mjs
```

Expected: all tests in this file pass.

- [ ] **Step 4: Commit implementation**

```bash
git add scripts/lib/cmd-state.mjs tests/lib/cmd-state.test.mjs
git commit -m "fix: fail closed on guard transition errors"
```

## Task 5: Build Executor Branch Isolation

**Files:**
- Modify: `skills/build-executor/SKILL.md`

- [ ] **Step 1: Add branch/worktree preflight**

In `skills/build-executor/SKILL.md`, after the config check line in `## Required Inputs`, add:

```markdown
Branch/worktree preflight before any implementation edit:
1. Run `git branch --show-current` and `git status --short`.
2. If the branch is `main` or `master`, create or switch to a feature branch/worktree before editing:
   - Preferred: `git worktree add ../<repo>-<change-name> -b <change-name>`
   - Acceptable: `git switch -c <change-name>`
3. If isolation cannot be created, stop and ask the user for explicit approval before editing `main` or `master`.
4. Report the chosen branch/worktree before implementation starts.
```

- [ ] **Step 2: Scan for contradictory branch guidance**

Run:

```bash
rg -n "worktree|branch|main|master" skills/build-executor/SKILL.md docs README.md INSTALL.md
```

Expected: no stronger contradictory instruction says to continue silently on `main`/`master`.

- [ ] **Step 3: Commit skill update**

```bash
git add skills/build-executor/SKILL.md
git commit -m "docs: require branch isolation before execution"
```

## Task 6: Version, Changelog, Verification, PR, Tag

**Files:**
- Modify: `CHANGELOG.md`
- Modify via script: versioned manifests/docs

- [ ] **Step 1: Sync version to 0.8.10**

Run:

```bash
node scripts/spec-superflow.mjs version 0.8.10
```

Expected: command reports version strings updated or already correct.

- [ ] **Step 2: Add changelog entry**

At the top of `CHANGELOG.md`, above `## [0.8.9] - 2026-07-04`, add:

```markdown
## [0.8.10] - 2026-07-05

### Fixed
- **Guard fast-path gating** — `exploring -> bridging` now requires `hotfix` or `tweak`, and `exploring -> approved-for-build` now requires `tweak`; `full`/`auto` workflows no longer skip `contract-builder` and approval gates.
- **State transition safety** — `ssf state transition` now fails closed when guard execution fails, times out, or returns invalid output, preventing state writes after unreliable guard checks.
- **Execution branch isolation** — `build-executor` now requires branch/worktree preflight before implementation and stops for explicit approval before editing `main` or `master`.
```

- [ ] **Step 3: Run version consistency**

Run:

```bash
node scripts/check-version-consistency.mjs
```

Expected: pass, all files at `0.8.10`.

- [ ] **Step 4: Run build and tests**

Run:

```bash
npm run build
npm test
```

Expected: TypeScript build passes; all tests pass.

- [ ] **Step 5: Run doctor**

Run:

```bash
node scripts/spec-superflow.mjs doctor
```

Expected: pass or only known non-blocking warnings unrelated to this patch.

- [ ] **Step 6: Commit release prep**

```bash
git add -A
git commit -m "chore: prepare 0.8.10 release"
```

- [ ] **Step 7: Push branch**

```bash
git push -u origin fix/issue-15-guard-hardening
```

Expected: branch pushed successfully.

- [ ] **Step 8: Open PR to main**

Run:

```bash
gh pr create --repo MageByte-Zero/spec-superflow --base main --head fix/issue-15-guard-hardening --title "Fix issue 15 guard fast-path bypass" --body "## Summary
- Gate fast-path transitions by workflow mode
- Make state transitions fail closed on guard errors
- Add build-executor branch/worktree preflight
- Prepare 0.8.10 version and changelog

## Verification
- npm run build
- npm test
- node scripts/check-version-consistency.mjs
- node scripts/spec-superflow.mjs doctor

Fixes #15"
```

Expected: GitHub returns a PR URL.

- [ ] **Step 9: Tag after PR merge**

After the PR is merged and local `main` is updated:

```bash
git switch main
git pull --ff-only origin main
git tag v0.8.10
git push origin v0.8.10
```

Expected: tag push triggers `.github/workflows/ci.yml` release job.

## Plan Self-Review

- Spec coverage: guard mode gating is Task 1-2, fail-closed state transition is Task 3-4, branch isolation is Task 5, release/version/PR/tag is Task 6.
- Red-flag scan: no blocked planning terms remain; commands and expected outcomes are explicit.
- Type consistency: all function and file names match existing repository paths and Node ESM style.

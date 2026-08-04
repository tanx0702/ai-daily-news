# X Production Feed Implementation Plan

**Goal:** Feed public X updates from 19 verified accounts into the existing daily candidate pipeline, with a hard cap of three selected X items.

**Architecture:** GitHub Actions publishes a schema-validated JSON snapshot to `x-feed`; the VPS reads that public snapshot through a bounded collector and degrades to RSS-only when it is unavailable. Existing editorial and publishing modules consume normal candidates unchanged.

**Constraints:** No database, Redis, VPS GitHub token, or direct X request from VPS. Keep the manual `X Web Probe` workflow. Preserve the existing quality gate and WeChat behavior.

### Task 1: Batch feed contract

Files: `config/x_sources.json`, `scripts/x_web_feed.py`, `tests/test_x_web_feed.py`.

1. Write tests for loading valid configured accounts, isolating a failed account, and emitting only public fields.
2. Implement the batch script by reusing `run_probe`, collecting successful reports, and writing `x-feed-v1` JSON.
3. Verify `python -m pytest -q tests/test_x_web_feed.py` passes.

### Task 2: GitHub publisher

Files: `.github/workflows/x-feed.yml`, `tests/test_x_web_feed.py`.

1. Test that the workflow is scheduled, manually dispatchable, uses `x-feed`, and has no VPS or secrets reference.
2. Add the workflow to run the batch script, publish the data file to `x-feed`, and upload diagnostics.
3. Trigger a manual run and inspect the published snapshot.

### Task 3: VPS collector integration

Files: `src/collectors/x_feed.py`, `src/collector.py`, `tests/test_x_feed_collector.py`, `.env.advanced.example`.

1. Write tests for snapshot age validation, X candidate normalization, disabled mode, and the three-item cap.
2. Implement a bounded HTTP reader that accepts only the expected schema and public X URLs.
3. Add X candidates to the existing collection merge and source balancing logic, preserving RSS-only fallback.
4. Verify the focused tests and the full suite.

### Task 4: Production dry run

1. Push the branch and run the X feed workflow.
2. Merge the implementation after checks pass.
3. Deploy the existing Docker service, force a dry run with `SKIP_WECHAT_DRAFT=1`, and inspect selection diagnostics for X candidates.

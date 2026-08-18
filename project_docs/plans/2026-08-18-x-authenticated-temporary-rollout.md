# Authenticated X Temporary Rollout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Temporarily replace the empty anonymous X snapshot with a VPS-generated authenticated snapshot for several days, without making X access a direct dependency of the daily pipeline.

**Architecture:** A root-only wrapper launches a one-shot container on the existing `ai-news_egress` Docker network, where `twscrape` can reach `proxy:7890`, and writes an atomic `x-feed-v1` file. `XFeedCollector` reads an explicitly mounted local snapshot first and falls back to the current HTTPS GitHub snapshot only when the local file is unavailable or invalid. No `src.main` code calls X or `twscrape`.

**Tech Stack:** Python 3.12+ project code, Docker one-shot runtime, `twscrape==0.20.0`, SQLite session state, Docker bind mount, host cron + `flock`, pytest.

## Global Constraints

- This is a temporary rollout, not a claim that X access is stable or policy-safe; stop and roll back on account warning, session invalidation, or unexpected response behavior.
- Use the existing one-account session only. No account rotation, password login, CAPTCHA handling, browser automation, or proxy rotation.
- Keep the Cookie file, `accounts.db`, raw POC responses, logs, and proxy details under `/root/ai-news-x-poc/` with mode `600`; none enter Git, `.env`, `docs/`, or application logs.
- The collector writes only public `x-feed-v1` fields. A valid fresh empty snapshot is authoritative and must not resurrect an older remote snapshot.
- `X_FEED_MAX_AGE_HOURS` remains at most six hours; X remains optional and capped by `DAILY_X_MAX_ITEMS <= 5`.
- The current GitHub workflow remains available for rollback, but production local-file precedence makes it non-authoritative during the trial.
- `SKIP_WECHAT_DRAFT=1` is used for every trial verification; no real WeChat draft call is a test step.

---

### Task 1: Add Tested Authenticated Snapshot Producer

**Files:**
- Create: `scripts/x_authenticated_feed.py`
- Create: `requirements-x.txt`
- Create: `tests/test_x_authenticated_feed.py`

**Interfaces:**
- `collect_authenticated_feed(client, sources, *, per_source_limit: int, timeout_seconds: int, now: datetime | None = None) -> dict[str, object]`
- `write_authenticated_feed(feed: Mapping[str, object], output_path: Path) -> Path`
- CLI: `python -m scripts.x_authenticated_feed --sources config/x_sources.json --db /root/ai-news-x-poc/accounts.db --output /root/ai-news-x-poc/feed/x-feed.json --per-source-limit 12 --timeout-seconds 45`

- [ ] **Step 1: Write failing mapping and degradation tests**

```python
def test_collect_authenticated_feed_maps_tweet_objects_to_x_feed_v1():
    feed = asyncio.run(collect_authenticated_feed(FakeClient(), [OPENAI], per_source_limit=3, timeout_seconds=45))

    assert feed["schema_version"] == "x-feed-v1"
    assert feed["tweet_count"] == 1
    assert feed["tweets"][0]["url"] == "https://x.com/OpenAI/status/42"
    assert feed["tweets"][0]["created_at"] == "2026-08-18T05:00:00Z"

def test_source_failure_is_recorded_without_stopping_other_sources():
    feed = asyncio.run(collect_authenticated_feed(FailingClient(), [OPENAI, ANTHROPIC], per_source_limit=3, timeout_seconds=45))

    assert feed["tweet_count"] == 1
    assert feed["failures"] == [{"handle": "AnthropicAI", "reason": "rate_limited"}]

def test_atomic_writer_never_leaves_partial_json(tmp_path):
    target = tmp_path / "feed" / "x-feed.json"
    write_authenticated_feed(EMPTY_FEED, target)
    assert json.loads(target.read_text(encoding="utf-8"))["schema_version"] == "x-feed-v1"
    assert not list(target.parent.glob(".x-feed.json.*.tmp"))
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `python -m pytest -q tests/test_x_authenticated_feed.py`

Expected: collection fails because `scripts.x_authenticated_feed` does not exist.

- [ ] **Step 3: Add the isolated dependency manifest**

Create `requirements-x.txt` containing exactly `twscrape==0.20.0`. Keep it separate from `requirements.txt` so the public `web` image does not acquire a private-session client merely to consume a snapshot.

- [ ] **Step 4: Implement the fake-client-compatible producer**

Define a small protocol for `user_by_login()` and `user_tweets()`. Use `asyncio.wait_for` around each source operation, truncate the returned page to `per_source_limit`, reject missing numeric ID/text/date/user fields, normalize dates to UTC ISO, construct `https://x.com/<handle>/status/<id>`, and preserve `thread_id`, `reply_to_id`, and `quoted_id` only when numeric. Map exceptions to bounded reason codes (`timeout`, `rate_limited`, `source_not_found`, `network_error`, `invalid_response`, `unexpected_error`).

- [ ] **Step 5: Implement the real twscrape adapter behind lazy import**

Only the CLI path imports `twscrape`. Instantiate `API(db, raise_when_no_account=True, wait_timeout=30, wait_interval=1, proxy=os.getenv("TWS_PROXY") or None)`, call `api.user_by_login(handle)`, then `api.user_tweets(user.id, limit=per_source_limit)`. Set `TWS_TELEMETRY=0` in the documented command. Do not read the Cookie bootstrap file in the producer; the tested SQLite session is the only runtime credential source.

- [ ] **Step 6: Run focused tests and commit the producer**

Run: `python -m pytest -q tests/test_x_authenticated_feed.py tests/test_x_web_feed.py`

Expected: all focused tests pass. Commit: `feat(source): 增加临时认证 X 快照采集器`.

### Task 2: Make The Existing Collector Prefer A Local Snapshot

**Files:**
- Modify: `src/collectors/x_feed.py`
- Modify: `src/collector.py`
- Modify: `tests/test_x_feed_collector.py`

**Interfaces:**
- Extend `XFeedCollector(..., local_snapshot_path: str = "")`.
- Add `X_FEED_LOCAL_PATH`, empty by default; retain `X_FEED_URL` as HTTPS fallback.

- [ ] **Step 1: Write failing local-first tests**

```python
def test_fresh_local_snapshot_is_consumed_without_http(tmp_path, monkeypatch):
    path = tmp_path / "x-feed.json"
    path.write_text(json.dumps(_feed(NOW)), encoding="utf-8")
    monkeypatch.setattr("src.collectors.x_feed.requests.get", pytest.fail)
    assert len(XFeedCollector("https://fallback.invalid/feed", local_snapshot_path=str(path), now=NOW).fetch()) == 1

def test_fresh_local_empty_snapshot_does_not_use_remote_fallback(tmp_path, monkeypatch):
    path = tmp_path / "x-feed.json"
    path.write_text(json.dumps(_empty_feed(NOW)), encoding="utf-8")
    monkeypatch.setattr("src.collectors.x_feed.requests.get", pytest.fail)
    assert XFeedCollector("https://fallback.invalid/feed", local_snapshot_path=str(path), now=NOW).fetch() == []
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m pytest -q tests/test_x_feed_collector.py`

Expected: failure because the collector does not accept a local path.

- [ ] **Step 3: Implement one shared validation path**

Read local JSON when `local_snapshot_path` is non-empty, then apply the existing schema, freshness, tweet normalization, allowlist, and status URL checks. Treat a valid empty local feed as terminal. Fall back to HTTPS only for missing, unreadable, stale, or invalid local input. Log source-specific reason codes without payload text.

- [ ] **Step 4: Wire the environment at `_fetch_x`**

Pass `os.environ.get("X_FEED_LOCAL_PATH", "")` from `src/collector.py` and preserve all existing X caps and evidence behavior.

- [ ] **Step 5: Run regression tests and commit**

Run: `python -m pytest -q tests/test_x_feed_collector.py tests/test_collector.py tests/test_draft_decision.py`

Expected: all pass. Commit: `feat(source): 优先读取本地 X 快照`.

### Task 3: Add Temporary Deployment Configuration And Documentation

**Files:**
- Modify: `docker-compose.yml`
- Modify: `.env.advanced.example`
- Modify: `tests/test_environment_templates.py`
- Modify: `project_docs/configuration.md`
- Modify: `project_docs/sources.md`
- Modify: `project_docs/operations.md`
- Modify: `AGENTS.md`

- [ ] **Step 1: Add configuration-template tests first**

Assert the advanced template documents `AI_NEWS_X_FEED_DIR` and `X_FEED_LOCAL_PATH` only as comments, contains no Cookie names or values, and the compose web service mounts the feed directory read-only.

- [ ] **Step 2: Add the read-only web bind mount**

Mount `${AI_NEWS_X_FEED_DIR:-./runtime/x-feed}` to `/app/runtime/x-feed:ro` in `web`. Do not mount the session directory into `web` or `nginx`; no new host port or Compose service is added.

- [ ] **Step 3: Document server-only trial commands**

Document the root-only wrapper launching the producer in the `ai-news_egress` network with `TWS_PROXY=http://proxy:7890`, writing `/root/ai-news-x-poc/feed/x-feed.json`, setting `AI_NEWS_X_FEED_DIR=/root/ai-news-x-poc/feed` and `X_FEED_LOCAL_PATH=/app/runtime/x-feed/x-feed.json`, then `docker compose up -d --force-recreate`.

- [ ] **Step 4: Document rollback and four-hour trial cron**

Document removing `X_FEED_LOCAL_PATH` or setting `ENABLE_X_COLLECTOR=0` to return to the GitHub snapshot, and a `flock`-guarded host cron that runs the producer before the existing 08:00 task. State that this is temporary, not a production guarantee, and that the Cookie bootstrap file must be deleted after session import.

- [ ] **Step 5: Run documentation/config tests and commit**

Run: `python -m pytest -q tests/test_environment_templates.py tests/test_x_authenticated_feed.py tests/test_x_feed_collector.py`

Expected: all pass. Commit: `docs(source): 记录临时认证 X 快照部署`.

### Task 4: Deploy, Observe, And Roll Back Safely

- [ ] **Step 1: Run the full local verification gate**

Run: `python -m pytest -q`; `git diff --check`; `git status --short`; `docker compose config`.

- [ ] **Step 2: Merge the reviewed branch into `master` and update the VPS**

Only after the full gate passes, merge the focused commits into `master`, push `origin/master`, then on the VPS run `git pull --ff-only` from `/opt/ai-news`. Never copy Cookie files into the repository.

- [ ] **Step 3: Run one authenticated snapshot before recreating `web`**

Run the producer manually with `TWS_PROXY=http://proxy:7890`, inspect only schema/count/age/failure counts, and stop if the output is stale, malformed, or contains zero unexpectedly.

- [ ] **Step 4: Enable local consumption and run a safe daily dry run**

Set only the two path variables in the server `.env`, run `docker compose up -d --force-recreate`, then run `docker compose exec -e SKIP_WECHAT_DRAFT=1 -T web python -m src.main`. Verify the decision and X source health; no WeChat API call is allowed.

- [ ] **Step 5: Monitor for several days**

Record each collector run's generated time, source success/failure counts, tweet count, session status, and daily `DraftDecision`. Stop the trial on account warning, repeated timeouts, malformed responses, or secret leakage. The local snapshot must never be older than six hours.

- [ ] **Step 6: Verify rollback path**

Remove the local path variable, force-recreate `web`, and confirm the collector returns to the HTTPS GitHub snapshot without code changes. Keep the temporary branch and server POC artifacts separate from the public feed.

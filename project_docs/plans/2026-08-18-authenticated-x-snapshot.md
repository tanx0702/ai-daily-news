# Authenticated X Snapshot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Status:** Rejected for implementation on 2026-08-18. Do not begin implementation or place an X session on the VPS. X's current automation rules prohibit non-API website scripting and state that it may result in permanent account suspension. This document is retained only as a record of the investigated architecture and its rejected risk profile.

> **Superseded decision:** Do not use `twscrape`, browser cookies, internal GraphQL endpoints, Nitter, browser automation, proxy rotation, or a dedicated X account for production collection. Any future X integration must use an official X API entitlement or a separately vetted licensed data provider.

**Goal:** Replace the unreliable anonymous GitHub Runner browser scrape with a VPS-local authenticated X snapshot producer, while preserving the production daily pipeline's snapshot-only input boundary and its conservative fact/evidence checks.

**Architecture:** A separately scheduled `x-collector` Compose job uses one dedicated account's `auth_token` and `ct0` session through `twscrape` to read whitelisted account timelines. It emits the existing `x-feed-v1` JSON contract by atomic file replacement in a root-only host directory. The `web` container consumes that mounted file first, validates it exactly as it validates the current HTTPS snapshot, and falls back to the existing public GitHub `x-feed` URL only when no usable local file exists. `python -m src.main` never imports or calls `twscrape`.

**Tech Stack:** Python 3.12, `twscrape==0.20.0`, `httpx` backend, SQLite session state managed by twscrape, Docker Compose profile, host cron + `flock`, pytest, existing `x-feed-v1` schema.

## Decision Record

- Select `vladkens/twscrape` as the primary adapter. It is Python-native, MIT licensed, supports cookie-session setup, SQLite state, and bounded account availability handling; its `v0.20.0` release and source updates were current on 2026-08-07.
- Start with **one dedicated X account** and no automatic account rotation. A lost session, account restriction, or rate limit produces a logged, fresh empty snapshot and leaves the rest of the daily pipeline running. It must not trigger credential creation, password login, account creation, CAPTCHA solving, residential proxies, browser fingerprinting, or any attempt to evade X controls.
- Use `API(raise_when_no_account=True, wait_timeout=30, wait_interval=1)` so one scheduled job never blocks indefinitely on a rate lock. Set `TWS_TELEMETRY=0` and use the library's default `httpx` backend in MVP.
- Do not add Node.js, Playwright, Nitter, Camofox, or a hosted scraping vendor to the production data path. Keep the current public GitHub workflow temporarily as an observable fallback only; it is not expected to restore anonymous collection.
- Retain `x-feed-v1`, the six-hour freshness rule, source allowlist, `/status/<tweet_id>` URL check, `DAILY_X_MAX_ITEMS <= 5`, and the rule that X remains optional. A valid fresh empty local snapshot is authoritative and must not be replaced with an older remote snapshot.

## Global Constraints

- `src.main` and Flask remain snapshot consumers only. The new authenticated network client runs solely in the separately invoked `x-collector` job.
- Never put `auth_token`, `ct0`, passwords, account email, `accounts.db`, response bodies, proxy credentials, or browser exports in Git, `.env`, `docs/`, logs, diagnostics, images, or command-line arguments.
- Store secrets and mutable twscrape state under `/root/ai-news-x/` on the VPS with directory mode `700` and file mode `600`. The only bind mount into `web` is the public-data-only output directory, read-only.
- The collector emits only the current public fields needed by `x-feed-v1`. It records bounded reason codes and counts, never raw error responses or cookie-bearing request data.
- All source, authentication, request, parse, timeout, rate-limit, and write failures are logged and degrade to an empty fresh snapshot. They cannot terminate other source collection or convert an unverified post into a fact brief.
- The local-file consumer accepts a valid fresh empty snapshot as a successful result. It tries the existing HTTPS `X_FEED_URL` only when the local snapshot is absent, unreadable, stale, or contract-invalid.
- The collector may use the existing internal egress proxy only for ordinary server connectivity when explicitly configured. It must use one fixed route per run, not per-account proxy rotation.
- The current public `x-feed` GitHub branch remains data-only and non-secret. No X cookies or GitHub Actions secrets are added to it.
- Update `AGENTS.md`, `.env.advanced.example`, `project_docs/architecture.md`, `project_docs/sources.md`, `project_docs/configuration.md`, and `project_docs/operations.md` with the approved deployment boundary.

## Target Flow

```text
host cron + flock (every four hours)
        |
        v
docker compose --profile x-collector run --rm x-collector
        |  reads /var/lib/ai-news-x/session/accounts.db and private cookie bootstrap
        |  calls X authenticated timeline endpoints through twscrape
        v
atomic /root/ai-news-x/feed/x-feed.json  (x-feed-v1, public fields only)
        |
        +-- read-only bind mount --> web:/app/runtime/x-feed/x-feed.json
                                      |
                                      v
                    XFeedCollector: local snapshot -> HTTPS GitHub fallback
                                      |
                                      v
                       existing candidate, evidence, clustering, DraftDecision flow
```

---

### Task 1: Establish A Shared Snapshot Contract And Atomic Writer

**Files:**
- Create: `scripts/x_feed_contract.py`
- Modify: `scripts/x_web_feed.py`
- Create: `tests/test_x_feed_contract.py`
- Modify: `tests/test_x_web_feed.py`

**Interfaces:**
- Produces: `normalize_x_snapshot_tweet(raw_tweet: Mapping[str, object], source: Mapping[str, object]) -> dict[str, object] | None`
- Produces: `build_x_feed(*, source_count: int, tweets: Sequence[Mapping[str, object]], failures: Sequence[Mapping[str, str]], generated_at: datetime | None = None) -> dict[str, object]`
- Produces: `write_x_feed_atomically(feed: Mapping[str, object], path: Path) -> Path`

- [ ] **Step 1: Write contract regression tests before moving implementation**

```python
def test_atomic_writer_replaces_a_complete_snapshot(tmp_path: Path):
    target = tmp_path / "x-feed.json"
    target.write_text('{"previous": true}\n', encoding="utf-8")

    write_x_feed_atomically(FRESH_EMPTY_FEED, target)

    assert json.loads(target.read_text(encoding="utf-8"))["schema_version"] == "x-feed-v1"
    assert not list(tmp_path.glob(".x-feed.json.*.tmp"))

def test_normalizer_rejects_non_numeric_id_and_mismatched_handle():
    assert normalize_x_snapshot_tweet(INVALID_TWEET, OPENAI_SOURCE) is None
```

- [ ] **Step 2: Confirm the tests fail for the missing module**

Run: `python -m pytest -q tests/test_x_feed_contract.py tests/test_x_web_feed.py`

Expected: collection/import failure because `scripts.x_feed_contract` does not exist.

- [ ] **Step 3: Extract only the already-accepted schema logic**

Move the schema version, public numeric-ID validation, UTC date normalization, tweet projection, feed count calculation, and JSON writer from `scripts/x_web_feed.py` into `scripts/x_feed_contract.py`. Write through a sibling temporary file, flush and `fsync`, then call `Path.replace()` so `web` never observes partial JSON. Keep output shape byte-compatible apart from normal JSON whitespace.

- [ ] **Step 4: Make the anonymous workflow use the shared writer without changing behavior**

Keep `scripts.x_web_feed.collect_x_feed()` and its `run_probe()` boundary intact. Delegate its normalization/feed writing to the new module. A no-tweet run must still exit `0` and publish a fresh empty `x-feed-v1` document.

- [ ] **Step 5: Verify contract and existing workflow tests**

Run: `python -m pytest -q tests/test_x_feed_contract.py tests/test_x_web_feed.py tests/test_x_feed_collector.py`

Expected: all pass; the existing test asserting a fresh empty anonymous snapshot remains green.

### Task 2: Add The Bounded Authenticated Timeline Producer

**Files:**
- Modify: `requirements.txt`
- Create: `scripts/x_auth_feed.py`
- Create: `tests/test_x_auth_feed.py`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: `python -m scripts.x_auth_feed --sources config/x_sources.json --state-dir /var/lib/ai-news-x/session --cookie-file /var/lib/ai-news-x/session/cookie-bootstrap.txt --output /var/lib/ai-news-x/feed/x-feed.json --max-tweets-per-source 12 --timeout-seconds 30`
- Produces: the shared `x-feed-v1` document and a process status of `0` after either partial success or a fresh empty snapshot; returns non-zero only for local configuration/permission errors that prevent a trustworthy output from being written.
- Provides: `AuthenticatedTimelineClient` protocol with `seed_cookie_session()`, `user_by_login(handle)`, and `user_tweets(user_id, limit)` operations so tests do not import `twscrape` or contact X.

- [ ] **Step 1: Write isolated producer tests with a fake timeline client**

```python
def test_authenticated_feed_maps_a_timeline_to_the_existing_contract(tmp_path: Path):
    feed = asyncio.run(collect_authenticated_feed([OPENAI_SOURCE], FakeClient([TWEET])))

    assert feed["tweet_count"] == 1
    assert feed["tweets"][0]["url"] == "https://x.com/OpenAI/status/42"
    assert feed["tweets"][0]["thread_id"] == "40"

def test_one_source_rate_limit_preserves_another_source_and_uses_reason_code(tmp_path: Path):
    feed = asyncio.run(collect_authenticated_feed([OPENAI_SOURCE, META_SOURCE], FakeClient()))

    assert feed["tweet_count"] == 1
    assert feed["failures"] == [{"handle": "AIatMeta", "reason": "rate_limited"}]

def test_no_active_account_writes_fresh_empty_snapshot_without_secret_material(tmp_path: Path, caplog):
    assert main(AUTH_ARGS) == 0
    assert json.loads(OUTPUT.read_text(encoding="utf-8"))["tweet_count"] == 0
    assert "auth_token=" not in caplog.text
```

- [ ] **Step 2: Confirm RED**

Run: `python -m pytest -q tests/test_x_auth_feed.py`

Expected: import failure because `scripts.x_auth_feed` does not exist.

- [ ] **Step 3: Pin and isolate the dependency**

Add `twscrape==0.20.0` to `requirements.txt`. Do not add the optional `curl-cffi` backend. Add a repository ignore rule for local `runtime/` data if it is used in local development; no fixture may contain real credentials.

- [ ] **Step 4: Implement a thin twscrape adapter**

Create `TwscrapeTimelineClient` in `scripts/x_auth_feed.py`. It creates `API(accounts_db, raise_when_no_account=True, wait_timeout=30, wait_interval=1)`, reads the bootstrap cookie file only inside the process, and calls `api.pool.add_account_cookies(local_account_label, cookie_string)`. It resolves each configured `handle` using `user_by_login`, streams no more than `--max-tweets-per-source` values from `user_tweets`, then maps only `id`, `rawContent`, `date`, `user.name`, `user.username`, `conversationId`, reply ID, and quoted ID into the shared normalizer.

Filter retweets and empty text before writing. Classify known failures into `no_active_account`, `rate_limited`, `network_error`, `source_not_found`, `invalid_response`, or `unexpected_error`; emit only the enum and handle. A successfully read source that simply has no eligible posts is not a failure. Never perform search, replies, followers, password login, or account management requests.

- [ ] **Step 5: Implement secure bootstrap lifecycle**

Use a private plain-text cookie header file containing only `auth_token` and `ct0` and a local, non-X account label supplied as a command argument or root-only settings file. Read it with an explicit mode check (`0600`), update the persistent `accounts.db`, then overwrite/unlink the bootstrap file only when the account database has an active session. If it is absent, use the existing database; if both are unusable, write a fresh empty snapshot with `no_active_account` rather than waiting or borrowing any other account.

- [ ] **Step 6: Verify unit coverage without live X access**

Run: `python -m pytest -q tests/test_x_auth_feed.py tests/test_x_feed_contract.py tests/test_x_web_feed.py`

Expected: all tests pass without an X cookie, network call, or `twscrape` account database checked into the workspace.

### Task 3: Consume A Protected Local Snapshot Before The Public Fallback

**Files:**
- Modify: `src/collectors/x_feed.py`
- Modify: `src/collector.py`
- Modify: `tests/test_x_feed_collector.py`
- Modify: `tests/test_collector.py`

**Interfaces:**
- Extends: `XFeedCollector(feed_url: str, ..., local_snapshot_path: str = "")`
- Adds configuration: `X_FEED_LOCAL_PATH` (empty by default; container path only, e.g. `/app/runtime/x-feed/x-feed.json`)
- Keeps configuration: `X_FEED_URL` as the HTTPS fallback and existing `X_FEED_MAX_AGE_HOURS` validation.

- [ ] **Step 1: Add local-first consumer tests**

```python
def test_local_fresh_snapshot_is_used_without_an_http_request(tmp_path: Path, monkeypatch):
    path = tmp_path / "x-feed.json"
    path.write_text(json.dumps(_feed(NOW)), encoding="utf-8")
    monkeypatch.setattr("src.collectors.x_feed.requests.get", pytest.fail)

    assert len(XFeedCollector("https://fallback.invalid/feed", local_snapshot_path=str(path), now=NOW).fetch()) == 1

def test_valid_local_empty_snapshot_does_not_resurrect_remote_items(tmp_path: Path, monkeypatch):
    path = tmp_path / "x-feed.json"
    path.write_text(json.dumps(_empty_feed(NOW)), encoding="utf-8")
    monkeypatch.setattr("src.collectors.x_feed.requests.get", pytest.fail)

    assert XFeedCollector("https://fallback.invalid/feed", local_snapshot_path=str(path), now=NOW).fetch() == []

def test_stale_local_snapshot_falls_back_to_valid_https_snapshot(tmp_path: Path, monkeypatch):
    ...
```

- [ ] **Step 2: Confirm RED**

Run: `python -m pytest -q tests/test_x_feed_collector.py tests/test_collector.py`

Expected: failures because the collector has no local path argument and always requests HTTPS.

- [ ] **Step 3: Implement source-independent parsing and fallback outcome tracking**

Refactor `XFeedCollector.fetch()` so file and HTTPS loaders both feed one schema/freshness validator. Distinguish `unavailable_or_invalid` from `valid_empty`: only the former reaches the HTTPS fallback. Keep the HTTPS allowlist unchanged, treat the local path as an explicit file input, and log `local_snapshot_unavailable`, `local_snapshot_invalid`, `local_snapshot_stale`, `local_snapshot_empty`, or `local_snapshot_loaded` without printing its contents.

- [ ] **Step 4: Wire the optional environment variable at the orchestration edge**

In `src/collector.py::_fetch_x`, pass `os.environ.get("X_FEED_LOCAL_PATH", "")`. Do not add a twscrape import to `src/` or alter source balancing, evidence binding, the maximum of five X briefs, or `DraftDecision`.

- [ ] **Step 5: Verify regression coverage**

Run: `python -m pytest -q tests/test_x_feed_collector.py tests/test_collector.py tests/test_briefing_config.py tests/test_draft_decision.py`

Expected: all pass; no local snapshot can bypass the x-feed schema, freshness, handle, or status-link requirements.

### Task 4: Add The Isolated Compose Job And VPS Scheduling Contract

**Files:**
- Modify: `docker-compose.yml`
- Modify: `.env.advanced.example`
- Modify: `tests/test_environment_templates.py`
- Create: `tests/test_x_auth_deployment.py`
- Modify: `project_docs/operations.md`
- Modify: `project_docs/configuration.md`

**Interfaces:**
- Adds Compose profile/service: `x-collector`, which is stopped by default and has no published ports.
- Adds optional variables: `AI_NEWS_X_STATE_DIR=/root/ai-news-x`, `X_FEED_LOCAL_PATH=/app/runtime/x-feed/x-feed.json`, `X_AUTH_COLLECTOR_PROXY=`.
- Uses host scheduler command: `docker compose --profile x-collector run --rm x-collector` guarded by `/usr/bin/flock -n /tmp/ai-news-x.lock`.

- [ ] **Step 1: Specify deployment file assertions first**

```python
def test_authenticated_x_collector_is_profiled_and_never_publishes_a_port():
    compose = Path("docker-compose.yml").read_text(encoding="utf-8")
    assert "x-collector:" in compose
    assert 'profiles: ["x-collector"]' in compose
    assert "X_FEED_LOCAL_PATH" in compose
    assert "ports:" not in _service_block(compose, "x-collector")

def test_advanced_template_documents_paths_but_not_cookie_values():
    text = Path(".env.advanced.example").read_text(encoding="utf-8")
    assert "# AI_NEWS_X_STATE_DIR=/root/ai-news-x" in text
    assert "auth_token=" not in text
    assert "ct0=" not in text
```

- [ ] **Step 2: Confirm RED**

Run: `python -m pytest -q tests/test_x_auth_deployment.py tests/test_environment_templates.py`

Expected: failures because no `x-collector` service or documented variables exist.

- [ ] **Step 3: Implement narrow Compose mounts and environment**

Add a profiled `x-collector` service built from the existing image. Mount `./config` read-only and `${AI_NEWS_X_STATE_DIR:-./runtime/x}` at `/var/lib/ai-news-x`; mount only `${AI_NEWS_X_STATE_DIR:-./runtime/x}/feed` into `web` at `/app/runtime/x-feed:ro`. Do not mount the collector's `session/` subdirectory into `web` or `nginx`. Give `x-collector` only the `egress` network, no port, no restart policy, and a command that selects the source JSON, private state directory, and output path. Use `TWS_TELEMETRY=0`; pass a single optional fixed `TWS_PROXY` only when `X_AUTH_COLLECTOR_PROXY` is configured.

- [ ] **Step 4: Document exact private-server setup and schedule**

Document these operational actions without printing a real cookie:

```bash
install -d -m 700 /root/ai-news-x/session /root/ai-news-x/feed
install -m 600 /dev/null /root/ai-news-x/session/cookie-bootstrap.txt
cd /opt/ai-news
docker compose --profile x-collector run --rm x-collector
```

Document a `CRON_TZ=Asia/Shanghai` host crontab with runs at `02:07, 06:07, 10:07, 14:07, 18:07, 22:07`, plus the existing daily job at `08:00`. The collector cron must log only to the existing protected logs directory and must not overlap itself. State how to refresh an expired cookie through the root-only bootstrap file and how to inspect `tweet_count`, failure reason counts, and snapshot age without exposing data.

- [ ] **Step 5: Update configuration and source architecture documentation**

Clarify that the authenticated collector is an optional producer job on the VPS, `X_FEED_LOCAL_PATH` has precedence, the GitHub `x-feed` branch remains a fallback, and `src.main` remains snapshot-only. Record failure/degradation behavior and X's five-item final cap. Update `AGENTS.md` because the source architecture and deployment boundary change.

- [ ] **Step 6: Verify deployment/documentation contracts**

Run: `python -m pytest -q tests/test_x_auth_deployment.py tests/test_environment_templates.py tests/test_x_web_feed.py`

Expected: all pass; default `docker compose up -d` still starts only `web` and `nginx` (plus an explicitly enabled existing proxy profile).

### Task 5: VPS Proof Of Capability, Rollout, And Acceptance Checks

**Files:**
- Modify only if needed after a failed test: the files from Tasks 1-4; no broad production refactor.
- Runtime only: `/root/ai-news-x/session/`, `/root/ai-news-x/feed/x-feed.json`, `/opt/ai-news/logs/x-collector-cron.log`.

- [ ] **Step 1: Build and run all automated checks before server access**

Run:

```bash
python -m pytest -q
git diff --check
git status --short
docker compose config
```

Expected: tests pass, no whitespace errors, generated runtime paths are untracked/ignored, and the default Compose configuration is valid.

- [ ] **Step 2: Complete the manual credential prerequisite**

The maintainer creates or designates one dedicated X account and places only its current `auth_token`/`ct0` cookie header in the root-only bootstrap file using an out-of-band terminal session. Do not paste it into Codex, chat, GitHub, a shell history, or `.env`.

- [ ] **Step 3: Run the live collector once on the VPS**

Run:

```bash
cd /opt/ai-news
docker compose --profile x-collector run --rm x-collector
python -c 'import json; from pathlib import Path; p=Path("/root/ai-news-x/feed/x-feed.json"); x=json.loads(p.read_text()); print({k:x.get(k) for k in ("schema_version", "generated_at", "source_count", "successful_source_count", "failed_source_count", "tweet_count")})'
```

Expected: a valid fresh `x-feed-v1` document. A zero count is a legitimate controlled degradation, not a success criterion to bypass; investigate the sanitized failure code and account status before changing code.

- [ ] **Step 4: Verify the web consumer and daily dry run on the VPS**

Run:

```bash
docker compose up -d --force-recreate
docker compose exec -e SKIP_WECHAT_DRAFT=1 -T web python -m src.main
curl -fsS https://tankex.xyz/health
```

Expected: the collector logs `local_snapshot_loaded` or `local_snapshot_empty`; the daily run never calls the WeChat draft API. Its final decision can remain `block` when fewer than five verified facts exist, but it must not fail due to X transport or schema handling.

- [ ] **Step 5: Enable cron only after a successful controlled run**

Install the documented collector cron entry, then wait for one scheduled execution. Verify the output timestamp, `tweet_count`/failure counts, no secret leakage in logs, and the following daily run's X source-health entry. Keep the anonymous GitHub workflow enabled for one week of comparison; remove it only through a separately approved cleanup change after proving it provides no useful fallback.

- [ ] **Step 6: Perform final repository checks and commit**

Run:

```bash
python -m pytest -q
git diff --check
git status --short
git diff --staged
```

Expected: all checks pass; no secret, runtime output, generated snapshot, or log is staged. Commit in focused Chinese Conventional Commit units, for example `feat(source): 增加受控 X 快照采集器`; deploy only the reviewed `master` commit.

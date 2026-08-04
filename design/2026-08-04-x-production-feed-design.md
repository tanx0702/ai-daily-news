# X Production Feed Design

## Goal

Make verified public X accounts available to the next daily edition without changing the existing RSS, HTML, Docker, or WeChat publishing flow.

## Data flow

GitHub Actions runs every four hours and reads `config/x_sources.json`. It uses the existing public-page probe for every configured account, combines successful public tweets into `x-feed.json`, and publishes that file to the dedicated `x-feed` branch. The VPS collector fetches the public raw JSON URL, validates its age and schema, then converts records into ordinary news candidates with `source_type: x`.

The existing deduplication, editorial selection, summarizer, quality gate, HTML renderer, and WeChat draft creation remain the only downstream path. A failed feed download, stale snapshot, malformed JSON, or failed account is logged and skipped without interrupting the RSS edition.

## Source policy

- Include the 25 X handles that returned public posts in the validation runs.
- Corporate model accounts are `primary` and marked `official` in repository-controlled configuration.
- Researchers are `research`; AI news and analysis accounts are `media`.
- X candidates are limited to three per edition through `DAILY_X_MAX_ITEMS`, default `3`.
- The current news window still applies. Records older than `DAILY_NEWS_HOURS` are discarded.
- The existing quality gate remains mandatory. A tweet is a source record, not independent corroboration of an external factual claim.

## Storage and deployment

`x-feed.json` is generated data and is written only to the `x-feed` branch. The production code branch remains free of generated reports. The public raw URL avoids adding a database, Redis, a VPS-side GitHub token, or a new network service.

## Validation

Unit tests cover source configuration parsing, feed schema and age validation, candidate normalization, per-source failure isolation, and the three-item X cap. A manual GitHub feed run produces the first snapshot. A production dry run uses that snapshot with `SKIP_WECHAT_DRAFT=1`; only the next scheduled production run is allowed to create the normal WeChat draft.

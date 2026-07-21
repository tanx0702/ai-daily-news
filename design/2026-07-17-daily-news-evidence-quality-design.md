# Daily News Evidence Quality Design

## Status

Approved design direction: scheme A, evidence-first per-item governance.

## Problem

The daily job must always collect current AI news and create a WeChat draft, but the current pipeline can produce drafts with unsupported summaries, irrelevant stories, misleading covers, and repeated generic images.

The observed causes are structural rather than model-specific:

- The summarizer overwrites `item["summary"]`, so the quality gate later receives generated text as both the source summary and generated summary.
- Candidate selection has only a small reserve and balances broad source types, not individual outlets, source trust, or topics.
- `og:image` and RSS image URLs are treated as article visuals without detecting generic placeholders, duplicate images, or headline conflicts.
- A global quality status reports success even when it cannot prove an individual summary is supported by source evidence.
- All text tasks share one model configuration, so changing the review model also changes summary generation.

## Goals

1. Create a current daily report and WeChat draft every run. A defect in one item must not cancel the daily workflow.
2. Preserve immutable source evidence through the entire pipeline so every generated statement can be checked against it.
3. Replace or downgrade unsafe items individually, using fresh reserve candidates before reducing the number of articles.
4. Prevent repeated, generic, corrupted, unsupported, or misleading images from appearing in the article or cover.
5. Make every fallback explicit in machine-readable and human-readable debug artifacts.
6. Allow text generation, quality review, and optional visual review to use separate providers and models.

## Non-Goals

- The system does not automatically publish a WeChat article. It continues to create drafts for manual publishing.
- The system does not invent news to fill a fixed count when current sources do not supply enough eligible items.
- Visual validation does not require AI image generation. A locally generated, text-free topic cover is the safe fallback.

## Data Contract

Every collected item gains immutable evidence fields before any LLM call. Existing presentation fields remain available for templates and WeChat publishing.

```text
source_title: str                 # Original title exactly as collected
source_summary: str               # Original RSS/API summary exactly as collected
source_excerpt: str               # Optional cleaned source-page excerpt
source_url: str                   # Canonical source URL
source_name: str                  # Publisher or platform
source_tier: primary|media|community|research
source_published_at: str

chinese_title: str                # Generated display title
summary: str                      # Generated display summary
highlight_text: str | None        # Generated editorial highlight

quality_state: candidate|ready|source_only|replace
quality_reasons: list[str]
quality_evidence: list[dict]      # Statement-to-source evidence mapping

media_state: trusted|text_only|rejected
media_reasons: list[str]
media_hash: str | None
media_perceptual_hash: str | None
```

`source_*` values are never replaced. `summary` and `chinese_title` remain generated display fields only. The quality gate must only compare generated fields with `source_*` evidence.

## Source Supply And Editorial Selection

### Source Tiers

- `primary`: official model, product, cloud, research-lab, and open-source release feeds.
- `media`: established AI and technology reporting outlets.
- `community`: Hacker News, GitHub, Hugging Face, and similar community signals. These require source labeling and adequate engagement.
- `research`: arXiv and research-lab publications.

`config/rss_sources.json` stores the tier, publisher name, enabled state, and per-source collection settings with each feed. Broken feeds are recorded in the daily source-health report and automatically receive no selection priority until a successful fetch restores health.

### Candidate Pool

The collector produces a fresh, deduplicated candidate pool of 30 items rather than only the target ten plus a small reserve. Relevance screening runs before expensive generation. The selector draws the report from this pool using the following rules:

- `DAILY_TOP_N=10` remains the preferred final count.
- A publisher may contribute at most two selected items.
- A normalized topic cluster may contribute at most two selected items.
- At least two selected items must be from the combined `primary` and `research` tiers when eligible candidates exist.
- Community entries retain their platform label and cannot be rewritten as official announcements.
- Low-relevance finance, generic semiconductor, and non-AI business items are rejected before text generation unless their retained source evidence explicitly establishes a direct AI connection.

When an item is later rejected, the next eligible candidate from the pool replaces it. If fewer than ten current, trustworthy items remain, the report contains the actual number rather than stale or invented filler. The daily draft is still created.

## Generation And Per-Item Quality Governance

### Structured Generation

The summarizer receives only `source_title`, `source_summary`, and `source_excerpt`. It returns structured JSON for each item:

```json
{
  "chinese_title": "...",
  "summary": "...",
  "claims": [
    {"text": "...", "evidence_field": "source_title|source_summary|source_excerpt", "evidence_quote": "..."}
  ]
}
```

Each generated sentence must have at least one quoted source span. A title-only source is allowed to produce a conservative title translation, but not a speculative explanatory summary.

### Per-Item States

- `ready`: generated text is supported by source evidence and passes relevance and wording checks.
- `replace`: an unsupported claim, wrong entity, wrong number, false official attribution, or low AI relevance requires replacement from the candidate pool.
- `source_only`: the item is topical and current but evidence is too sparse for a safe summary. Render a conservative title/source/link card without inferred details.
- `candidate`: not yet selected or awaiting validation.

The quality gate validates title, summary, highlight, and cover candidate against immutable evidence. It checks numerical values, named entities, model/product names, source attribution, certainty words, and community-versus-official wording. It may correct wording, but it cannot upgrade a `replace` item to `ready` without source support.

LLM review failure does not pretend to be a successful review. Deterministic checks still run; candidates without adequate evidence become `source_only` or are replaced. The final report separately records `llm_review_status=passed|failed|skipped` and the per-item state.

## Image And Cover Governance

### Article Images

All remote image candidates are downloaded before selection. A candidate is trusted only when it passes all of the following checks:

1. Its magic bytes and decoded image type match an allowlist, then it is re-encoded as JPEG or PNG for WeChat upload.
2. Its dimensions and aspect ratio meet configurable editorial thresholds.
3. Its exact and perceptual hashes are not already assigned to another selected article.
4. It is not a known generic publisher asset, a repeated placeholder, a logo-only image, or a tiny thumbnail.
5. Its page context, alt/caption text, or optional visual review does not conflict with the selected article.

An item without a trusted image uses a stable text-only card. It never borrows a generic image from another article.

### Cover

The cover is selected after final item replacement. A source image may be used only if it is trusted, unique, high resolution, and semantically consistent with the top story. OCR and an optional visual-review model reject source images whose visible text, model name, product, or subject conflicts with the headline. The observed `GPT 5.6` image for a Shanghai AI Lab story is the specific failure this rule must catch.

When no source image passes, the cover is a local, text-free topic graphic. Optional image generation is attempted only when the image provider health check is passing. A provider `401`, `403`, unsupported model, or timeout opens a short-lived circuit breaker and immediately uses the local fallback rather than repeated requests.

## Model Configuration

Text, quality review, and visual review have independent OpenAI-compatible settings. The quality settings fall back to text settings for backward compatibility.

```text
LLM_API_KEY, LLM_MODEL, LLM_API_BASE
QUALITY_LLM_API_KEY, QUALITY_LLM_MODEL, QUALITY_LLM_API_BASE
VISION_API_KEY, VISION_MODEL, VISION_API_BASE
IMAGE_API_KEY, IMAGE_MODEL, IMAGE_API_BASE
```

Additional controls:

```text
DAILY_CANDIDATE_POOL_N=30
DAILY_MAX_ITEMS_PER_SOURCE=2
DAILY_MAX_ITEMS_PER_TOPIC=2
DAILY_MIN_PRIMARY_OR_RESEARCH=2
ENABLE_COVER_VISUAL_REVIEW=true
SKIP_WECHAT_DRAFT=false
```

`SKIP_WECHAT_DRAFT=true` runs the full pipeline and writes local artifacts and reports without creating a WeChat draft. It is the required validation mode for manual testing.

## Daily Output And Observability

Every run creates the daily HTML, archive entry, latest JSON, cover, WeChat preview, and an internal report. Creating the WeChat draft remains unconditional after these artifacts exist, unless `SKIP_WECHAT_DRAFT=true` is set for a test run.

The daily debug report includes:

- source health, raw counts, and candidate-pool counts by tier and outlet;
- final diversity quotas and every rejected/replaced item with its reason;
- LLM request outcome by stage and provider;
- immutable evidence and generated-claim validation result for every final item;
- image candidate hashes, reuse detection, conversion format, cover review result, and upload outcome;
- final item count, `ready`/`source_only` counts, and WeChat draft result.

Public HTML and WeChat article content do not expose internal diagnostic state.

## Failure Behavior

| Failure | Per-item behavior | Daily behavior |
| --- | --- | --- |
| Text LLM unavailable | Use conservative source-only representation | Generate report and draft |
| Quality LLM unavailable | Use deterministic checks; replace weak items or source-only downgrade | Generate report and draft; record failed review |
| Source image unusable | Use text-only article card | Continue |
| Cover validation or image provider fails | Use local text-free cover | Continue |
| A feed fails | Use remaining healthy feeds and reserve candidates | Continue and record feed health |
| Fewer than ten trustworthy items | Publish the actual current verified count | Generate report and draft |

## Acceptance Criteria

1. A quality review fixture proves that source evidence and generated content differ when the summarizer adds an unsupported claim; the item is replaced or downgraded.
2. A daily selection fixture proves one publisher and one topic cannot exceed their configured quotas while qualified reserve items exist.
3. A media fixture with identical and near-identical URLs/images proves only one selected item receives that visual.
4. A cover fixture containing an unrelated visible model name proves the source image is rejected and a local cover is selected.
5. A WeChat upload fixture with an unsupported image format proves the normalized JPEG/PNG upload path succeeds or degrades to a text card without failing the draft.
6. Provider-failure fixtures prove the report is still rendered and `SKIP_WECHAT_DRAFT=true` avoids draft creation.
7. An end-to-end dry run emits a report with source health, item states, evidence validation, visual validation, and final selection counts.

## Implementation Boundaries

The first implementation should remain within the current Python pipeline and Jinja/WeChat rendering flow. It should add focused modules for immutable evidence, source selection policy, and media validation rather than expanding the already large collector, quality gate, and cover modules further. Existing environment variable names and `AGNES_*` compatibility remain intact while the newer split configuration takes precedence.

#!/bin/sh

set -eu

POC_DIR=${X_AUTH_POC_DIR:-/root/ai-news-x-poc}
PROJECT_DIR=${X_AUTH_PROJECT_DIR:-/opt/ai-news}

exec docker run --rm \
  --network ai-news_egress \
  -e TWS_PROXY=http://proxy:7890 \
  -e TWS_TELEMETRY=0 \
  -v "${PROJECT_DIR}:/app:ro" \
  -v "${POC_DIR}:/poc" \
  -w /app \
  ai-news-web:latest \
  sh -lc 'pip install -q -r requirements-x.txt && python -m scripts.x_authenticated_feed \
    --sources /app/config/x_sources.json \
    --db /poc/accounts.db \
    --output /poc/feed/x-feed.json \
    --per-source-limit "${X_AUTH_PER_SOURCE_LIMIT:-3}" \
    --timeout-seconds "${X_AUTH_TIMEOUT_SECONDS:-15}"'

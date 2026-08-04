import json
from pathlib import Path

from scripts.x_web_feed import collect_x_feed, load_x_sources


def test_load_x_sources_returns_only_configured_public_profiles(tmp_path: Path):
    source_path = tmp_path / "x_sources.json"
    source_path.write_text(
        json.dumps(
            {
                "schema_version": "x-sources-v1",
                "sources": [
                    {
                        "name": "OpenAI",
                        "handle": "OpenAI",
                        "tier": "primary",
                        "official": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    assert load_x_sources(source_path) == [
        {
            "name": "OpenAI",
            "handle": "OpenAI",
            "tier": "primary",
            "official": True,
            "url": "https://x.com/OpenAI",
        }
    ]


def test_collect_x_feed_keeps_successful_source_when_another_probe_fails(
    tmp_path: Path, monkeypatch
):
    def fake_run_probe(target_url: str, output_dir: Path) -> int:
        if target_url.endswith("/Unavailable"):
            report = {
                "schema_version": "x-web-probe-v1",
                "target_url": target_url,
                "tweet_count": 0,
                "tweets": [],
                "errors": [],
            }
            exit_code = 1
        else:
            report = {
                "schema_version": "x-web-probe-v1",
                "target_url": target_url,
                "tweet_count": 1,
                "tweets": [
                    {
                        "tweet_id": "42",
                        "text": "发布新的 AI 模型",
                        "author": "OpenAI",
                        "created_at": "2026-08-04T00:00:00.000Z",
                        "like_count": 0,
                        "repost_count": 0,
                        "reply_count": 0,
                        "quote_count": 0,
                    }
                ],
                "errors": [],
            }
            exit_code = 0
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "probe-report.json").write_text(
            json.dumps(report), encoding="utf-8"
        )
        return exit_code

    monkeypatch.setattr("scripts.x_web_feed.run_probe", fake_run_probe)

    feed = collect_x_feed(
        [
            {"name": "OpenAI", "handle": "OpenAI", "tier": "primary", "official": True},
            {"name": "Unavailable", "handle": "Unavailable", "tier": "media", "official": False},
        ],
        tmp_path,
    )

    assert feed["schema_version"] == "x-feed-v1"
    assert feed["successful_source_count"] == 1
    assert feed["failed_source_count"] == 1
    assert feed["tweet_count"] == 1
    assert feed["tweets"] == [
        {
            "tweet_id": "42",
            "text": "发布新的 AI 模型",
            "author": "OpenAI",
            "created_at": "2026-08-04T00:00:00.000Z",
            "url": "https://x.com/OpenAI/status/42",
            "source_name": "OpenAI",
            "source_handle": "OpenAI",
            "source_tier": "primary",
            "official": True,
        }
    ]


def test_x_feed_workflow_publishes_a_scheduled_snapshot_without_vps_access():
    workflow = Path(".github/workflows/x-feed.yml").read_text(encoding="utf-8")
    normalized = workflow.lower()

    assert "workflow_dispatch:" in workflow
    assert "schedule:" in workflow
    assert "contents: write" in normalized
    assert "ref: x-feed" in normalized
    assert "x-feed.json" in workflow
    assert "python -m scripts.x_web_feed" in workflow
    assert "vps" not in normalized
    assert "ssh " not in normalized
    assert "scp " not in normalized
    assert "secrets." not in normalized

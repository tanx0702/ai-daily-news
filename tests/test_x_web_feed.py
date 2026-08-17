import json
from collections import Counter
from pathlib import Path

from scripts.x_web_feed import collect_x_feed, load_x_sources, main


def test_production_x_sources_keep_expanded_tier_distribution():
    sources = load_x_sources(Path("config/x_sources.json"))

    assert len(sources) == 35
    assert Counter(item["tier"] for item in sources) == Counter(
        {"primary": 20, "research": 10, "media": 5}
    )
    assert all(item["url"].startswith("https://x.com/") for item in sources)
    assert len({item["handle"].lower() for item in sources}) == 35


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
            "created_at": "2026-08-04T00:00:00Z",
            "url": "https://x.com/OpenAI/status/42",
            "source_name": "OpenAI",
            "source_handle": "OpenAI",
            "source_tier": "primary",
            "official": True,
            "thread_id": "42",
            "reply_to_id": "",
            "quoted_id": "",
        }
    ]


def test_collect_x_feed_normalizes_legacy_graphql_date_and_keeps_thread_ids(
    tmp_path: Path, monkeypatch
):
    def fake_run_probe(_target_url: str, output_dir: Path) -> int:
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "probe-report.json").write_text(
            json.dumps(
                {
                    "schema_version": "x-web-probe-v1",
                    "tweet_count": 1,
                    "tweets": [
                        {
                            "tweet_id": "42",
                            "text": "回复并引用",
                            "author": "OpenAI",
                            "created_at": "Sun Aug 03 06:00:00 +0000 2026",
                            "thread_id": "40",
                            "reply_to_id": "41",
                            "quoted_id": "39",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        return 0

    monkeypatch.setattr("scripts.x_web_feed.run_probe", fake_run_probe)

    feed = collect_x_feed(
        [{"name": "OpenAI", "handle": "OpenAI", "tier": "primary", "official": True}],
        tmp_path,
    )

    assert feed["tweets"] == [
        {
            "tweet_id": "42",
            "text": "回复并引用",
            "author": "OpenAI",
            "created_at": "2026-08-03T06:00:00Z",
            "url": "https://x.com/OpenAI/status/42",
            "source_name": "OpenAI",
            "source_handle": "OpenAI",
            "source_tier": "primary",
            "official": True,
            "thread_id": "42",
            "reply_to_id": "",
            "quoted_id": "",
            "thread_id": "40",
            "reply_to_id": "41",
            "quoted_id": "39",
        }
    ]


def test_x_feed_main_publishes_a_fresh_empty_snapshot_when_all_probes_fail(
    tmp_path: Path, monkeypatch
):
    output_path = tmp_path / "x-feed.json"

    monkeypatch.setattr(
        "scripts.x_web_feed.load_x_sources",
        lambda _path: [
            {
                "name": "OpenAI",
                "handle": "OpenAI",
                "tier": "primary",
                "official": True,
            }
        ],
    )
    monkeypatch.setattr(
        "scripts.x_web_feed.collect_x_feed",
        lambda _sources, _work_dir: {
            "schema_version": "x-feed-v1",
            "generated_at": "2026-08-17T10:00:00Z",
            "source_count": 1,
            "successful_source_count": 0,
            "failed_source_count": 1,
            "failures": [{"handle": "openai", "reason": "no_public_tweets"}],
            "tweet_count": 0,
            "tweets": [],
        },
    )

    assert main(
        [
            "--sources",
            str(tmp_path / "sources.json"),
            "--work-dir",
            str(tmp_path / "work"),
            "--output",
            str(output_path),
        ]
    ) == 0
    assert json.loads(output_path.read_text(encoding="utf-8"))["tweet_count"] == 0


def test_x_feed_workflow_publishes_a_scheduled_snapshot_without_vps_access():
    workflow = Path(".github/workflows/x-feed.yml").read_text(encoding="utf-8")
    normalized = workflow.lower()

    assert "workflow_dispatch:" in workflow
    assert "schedule:" in workflow
    assert 'cron: "7 2,6,10,14,18,22 * * *"' in workflow
    assert "timeout-minutes: 20" in workflow
    assert "contents: write" in normalized
    assert "ref: x-feed" in normalized
    assert "x-feed.json" in workflow
    assert "python -m scripts.x_web_feed" in workflow
    assert "vps" not in normalized
    assert "ssh " not in normalized
    assert "scp " not in normalized
    assert "secrets." not in normalized

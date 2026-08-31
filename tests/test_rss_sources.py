import json
from pathlib import Path


def test_primary_rss_sources_include_google_ai_and_nvidia_ai():
    source_path = Path(__file__).resolve().parents[1] / "config" / "rss_sources.json"
    sources = json.loads(source_path.read_text(encoding="utf-8"))["sources"]
    by_name = {source["name"]: source for source in sources}

    assert by_name["Google AI Blog"] == {
        "name": "Google AI Blog",
        "url": "https://blog.google/technology/ai/rss/",
        "region": "overseas",
        "tier": "primary",
    }
    assert by_name["NVIDIA AI Blog"] == {
        "name": "NVIDIA AI Blog",
        "url": "https://blogs.nvidia.com/blog/category/deep-learning/feed/",
        "region": "overseas",
        "tier": "primary",
    }


def test_media_rss_sources_include_independent_ai_reporting():
    source_path = Path(__file__).resolve().parents[1] / "config" / "rss_sources.json"
    sources = json.loads(source_path.read_text(encoding="utf-8"))["sources"]
    by_name = {source["name"]: source for source in sources}

    assert by_name["VentureBeat AI"]["url"] == "https://venturebeat.com/category/ai/feed/"
    assert by_name["Ars Technica Technology Lab"]["url"] == "https://feeds.arstechnica.com/arstechnica/technology-lab"
    assert by_name["IEEE Spectrum AI"]["url"] == "https://spectrum.ieee.org/feeds/topic/artificial-intelligence.rss"
    assert all(by_name[name].get("tier") == "media" for name in [
        "VentureBeat AI",
        "Ars Technica Technology Lab",
        "IEEE Spectrum AI",
    ])


def test_rss_sources_include_verified_fact_plane_feeds():
    source_path = Path(__file__).resolve().parents[1] / "config" / "rss_sources.json"
    sources = json.loads(source_path.read_text(encoding="utf-8"))["sources"]
    by_name = {source["name"]: source for source in sources}

    assert len(by_name) == len(sources)
    assert by_name["Hugging Face Blog"] == {
        "name": "Hugging Face Blog",
        "url": "https://huggingface.co/blog/feed.xml",
        "region": "overseas",
        "tier": "primary",
    }
    assert by_name["MIT Technology Review AI"] == {
        "name": "MIT Technology Review AI",
        "url": "https://www.technologyreview.com/topic/artificial-intelligence/feed/",
        "region": "overseas",
        "tier": "media",
    }
    assert by_name["Ars Technica AI"] == {
        "name": "Ars Technica AI",
        "url": "https://arstechnica.com/ai/feed/",
        "region": "overseas",
        "tier": "media",
    }
    assert "36氪 AI" not in by_name
    assert "机器之心" not in by_name

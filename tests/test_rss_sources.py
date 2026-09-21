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


def test_rss_sources_include_probed_high_freshness_feeds():
    """Feeds verified in production for reachability and 36h freshness.

    Probing showed the official blogs (OpenAI/HF/DeepMind) publish rarely, so the
    supply bottleneck is high-frequency feeds: aggregators and fast AI media.
    """
    source_path = Path(__file__).resolve().parents[1] / "config" / "rss_sources.json"
    sources = json.loads(source_path.read_text(encoding="utf-8"))["sources"]
    by_name = {source["name"]: source for source in sources}

    assert by_name["Hacker News Newest AI"] == {
        "name": "Hacker News Newest AI",
        "url": "https://hnrss.org/newest?q=AI+OR+LLM+OR+GPT+OR+Claude",
        "region": "overseas",
        "tier": "community",
    }
    assert by_name["The Decoder"]["url"] == "https://the-decoder.com/feed/"
    assert by_name["The Decoder"]["tier"] == "media"
    assert by_name["MarkTechPost"]["url"] == "https://www.marktechpost.com/feed/"
    assert by_name["MarkTechPost"]["tier"] == "media"
    assert by_name["TechCrunch"]["url"] == "https://techcrunch.com/feed/"
    assert by_name["APPSO"]["url"] == "https://www.ifanr.com/feed"
    assert by_name["APPSO"]["region"] == "china"


def test_rss_sources_exclude_probed_unreachable_feeds():
    """Feeds that 404/302/429 or never publish within the window must stay out."""
    source_path = Path(__file__).resolve().parents[1] / "config" / "rss_sources.json"
    sources = json.loads(source_path.read_text(encoding="utf-8"))["sources"]
    by_name = {source["name"]: source for source in sources}

    for name in (
        "Anthropic News",
        "Meta AI Blog",
        "Mistral AI",
        "Cohere Blog",
        "Stability AI",
        "Groq Blog",
        "Perplexity Blog",
        "Reddit LocalLLaMA",
        "新智元",
        # Round 3: stable feeds rejected for poor AI relevance.
        "Lobsters AI",
        "爱范儿",
    ):
        assert name not in by_name, name


def test_rss_sources_include_round_three_chinese_and_media_feeds():
    """Second expansion round: Chinese tech media carry high 36h AI volume.

    Probed twice for stability; each returns AI-relevant items inside the
    window. Chinese supply was the thinnest area, so most additions are there.
    """
    source_path = Path(__file__).resolve().parents[1] / "config" / "rss_sources.json"
    sources = json.loads(source_path.read_text(encoding="utf-8"))["sources"]
    by_name = {source["name"]: source for source in sources}

    for name, url, region in (
        ("InfoQ 中文 AI", "https://www.infoq.cn/feed/ai", "china"),
        ("雷峰网", "https://www.leiphone.com/feed", "china"),
        ("钛媒体", "https://www.tmtpost.com/rss.xml", "china"),
        ("Simon Willison", "https://simonwillison.net/atom/everything/", "overseas"),
        ("Unite.AI", "https://www.unite.ai/feed/", "overseas"),
        ("InfoQ AI", "https://feed.infoq.com/ai-ml-data-eng/", "overseas"),
    ):
        assert name in by_name, name
        assert by_name[name]["url"] == url, name
        assert by_name[name]["region"] == region, name

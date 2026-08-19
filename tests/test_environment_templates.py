import re
import unittest
from pathlib import Path


CORE_ENV_NAMES = {
    "LLM_API_KEY",
    "LLM_MODEL",
    "LLM_API_BASE",
    "IMAGE_API_KEY",
    "IMAGE_MODEL",
    "IMAGE_API_BASE",
    "WECHAT_APP_ID",
    "WECHAT_APP_SECRET",
    "WECHAT_TOKEN",
    "DOMAIN",
    "PAGES_URL",
}

ADVANCED_ENV_NAMES = {
    "QUALITY_LLM_API_KEY",
    "QUALITY_LLM_MODEL",
    "QUALITY_LLM_API_BASE",
    "DAILY_TOP_N",
    "DAILY_MIN_ITEMS",
    "DAILY_CANDIDATE_POOL_N",
    "DAILY_X_TARGET_ITEMS",
    "DAILY_X_MAX_ITEMS",
    "DAILY_MIN_FACT_ITEMS",
    "DAILY_MAX_OPINION_ITEMS",
    "X_FEED_MAX_AGE_HOURS",
    "SEMANTIC_DEDUP_WINDOW_HOURS",
    "SEMANTIC_DEDUP_MAX_LLM_CALLS",
    "SEMANTIC_DEDUP_TIMEOUT",
    "ENABLE_AI_COVER_GENERATION",
    "COVER_RENDER_MODE",
    "SKIP_WECHAT_DRAFT",
    "EDITORIAL_REVIEW_USERNAME",
    "EDITORIAL_REVIEW_PASSWORD",
}

EXPECTED_BRIEFING_DEFAULTS = {
    "DAILY_TOP_N": "15",
    "DAILY_MIN_ITEMS": "5",
    "DAILY_CANDIDATE_POOL_N": "45",
    "DAILY_MIN_FACT_ITEMS": "3",
    "DAILY_MAX_OPINION_ITEMS": "3",
    "DAILY_X_TARGET_ITEMS": "5",
    "DAILY_X_MAX_ITEMS": "8",
    "X_FEED_MAX_AGE_HOURS": "6",
    "SEMANTIC_DEDUP_WINDOW_HOURS": "48",
    "SEMANTIC_DEDUP_MAX_LLM_CALLS": "20",
    "SEMANTIC_DEDUP_TIMEOUT": "45",
}

RETIRED_PRODUCTION_NAMES = {
    "ENABLE_LLM_QUALITY_GATE",
    "QUALITY_GATE_STRICT",
    "ENABLE_PUBLISH_SAFETY_FILTER",
    "DAILY_SAFETY_RESERVE_N",
    "WECHAT_USE_AI_TEMPLATE",
}


def _active_environment_names(path: Path) -> set[str]:
    names = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.fullmatch(r"([A-Z][A-Z0-9_]*)=.*", line)
        if match:
            names.add(match.group(1))
    return names


def _commented_environment_names(path: Path) -> set[str]:
    names = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.fullmatch(r"#\s*([A-Z][A-Z0-9_]*)=.*", line)
        if match:
            names.add(match.group(1))
    return names


class EnvironmentTemplateTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[1]
        self.core_template = self.root / ".env.example"
        self.advanced_template = self.root / ".env.advanced.example"

    def test_core_template_contains_only_production_setup_variables(self):
        self.assertSetEqual(_active_environment_names(self.core_template), CORE_ENV_NAMES)

    def test_advanced_template_is_comment_only_and_documents_optional_overrides(self):
        self.assertSetEqual(_active_environment_names(self.advanced_template), set())
        self.assertTrue(
            ADVANCED_ENV_NAMES.issubset(_commented_environment_names(self.advanced_template))
        )

        advanced_text = self.advanced_template.read_text(encoding="utf-8")
        for name, value in EXPECTED_BRIEFING_DEFAULTS.items():
            self.assertRegex(advanced_text, rf"(?m)^# {name}={value}$")
        for name in RETIRED_PRODUCTION_NAMES:
            self.assertNotRegex(advanced_text, rf"(?m)^#\s*{name}=")
        self.assertNotRegex(advanced_text, r"(?m)^#\s*(?:AGNES|OPENAI)_[A-Z0-9_]*=")

    def test_deployment_docs_point_to_the_advanced_template(self):
        for name in ("README.md", "AGENTS.md"):
            text = (self.root / name).read_text(encoding="utf-8")
            self.assertIn(".env.advanced.example", text)
            self.assertIn("QUALITY_LLM_*", text)


if __name__ == "__main__":
    unittest.main()

"""Final-display fact contract validation with optional read-only LLM review."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from html import unescape
import json
import logging
import re
from typing import Callable
from urllib.parse import urlparse

from src.briefing.config import BriefingConfig
from src.briefing.models import (
    BriefItem,
    BuiltBrief,
    MergedEvent,
    RebuildRequest,
    ValidationResult,
)
from src.llm_config import LLMConfig


logger = logging.getLogger(__name__)

_COMMENTARY_MARKERS = (
    "值得关注",
    "趋势",
    "前景",
    "领先",
    "重磅",
    "革命性",
    "影响深远",
    "标志着",
    "将改变",
    "必将",
    "建议",
    "推荐",
)
_ATTRIBUTION_MARKERS = (
    "称",
    "分享",
    "表示",
    "帖子",
    "发文",
    "透露",
    "据",
    "研究者",
    "开发者",
    "媒体",
)
_ACTION_GROUPS = {
    "release": ("发布", "推出", "上线", "release", "launch", "roll out"),
    "acquisition": ("收购", "合并", "acquire", "acquisition", "merge"),
    "funding": ("融资", "投资", "估值", "funding", "raise", "valuation"),
    "open_source": ("开源", "open source", "open-source"),
}
_ALLOWED_QUALITY_REASONS = {
    "missing_evidence",
    "missing_source_url",
    "stale_item",
    "unsupported_claim",
    "community_claim_overstated",
    "github_activity_only",
    "invalid_builder_response",
    "translation_failed",
}


def _default_client_factory(**kwargs):
    from openai import OpenAI

    return OpenAI(**kwargs)


def _normalized_text(value: object) -> str:
    text = unescape(str(value or "")).replace("\xa0", " ")
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _comparison_text(value: object) -> str:
    text = _normalized_text(value).lower()
    return re.sub(r"[\s，,。.!！?？:：;；、\"'“”‘’（）()【】\[\]]+", "", text)


def _sentences(value: str) -> list[str]:
    return [
        part.strip()
        for part in re.split(r"(?<=[。！？!?])\s*", value.strip())
        if part.strip()
    ]


def _contains_chinese(value: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in value)


def _display_claims(draft: BuiltBrief) -> list[str]:
    display_units = [draft.chinese_title.strip()]
    display_units.extend(
        sentence.rstrip("。！？!? ") for sentence in _sentences(draft.brief)
    )
    claims = [
        claim.strip()
        for unit in display_units
        for claim in re.split(r"[，,；;]", unit)
        if claim.strip()
    ]
    return [claim for claim in claims if claim]


def _binding_covers(display_claim: str, binding_claim: str) -> bool:
    display = _comparison_text(display_claim)
    binding = _comparison_text(binding_claim)
    return bool(display and binding and display == binding)


def _quote_match_text(value: object) -> str:
    text = unescape(str(value or "")).replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def _meaningful_tokens(value: str) -> set[str]:
    tokens = set(re.findall(r"[A-Za-z][A-Za-z0-9.+-]*|\d+(?:[.,]\d+)?", value))
    return {token.lower() for token in tokens if token.lower() not in {"the", "a", "an"}}


def _chinese_bigrams(value: str) -> set[str]:
    chars = "".join(re.findall(r"[\u4e00-\u9fff]", value))
    return {chars[index:index + 2] for index in range(max(len(chars) - 1, 0))}


def _cross_language_action_matches(claim: str, quote: str) -> bool:
    claim_lower = claim.lower()
    quote_lower = quote.lower()
    for markers in _ACTION_GROUPS.values():
        if any(marker in claim_lower for marker in markers):
            return any(marker in quote_lower for marker in markers)
    return True


def _requires_cross_language_semantic_review(draft: BuiltBrief) -> bool:
    return any(
        _contains_chinese(binding.claim)
        and not _contains_chinese(binding.source_quote)
        for binding in draft.evidence_bindings
    )


def _claim_related_to_quote(claim: str, quote: str) -> bool:
    claim_cmp = _comparison_text(claim)
    quote_cmp = _comparison_text(quote)
    if claim_cmp in quote_cmp or quote_cmp in claim_cmp:
        return True
    claim_tokens = _meaningful_tokens(claim)
    quote_tokens = _meaningful_tokens(quote)
    if claim_tokens and claim_tokens & quote_tokens:
        protected = {
            token
            for token in claim_tokens
            if any(char.isdigit() for char in token)
            or any(char.isupper() for char in re.findall(r"[A-Za-z]+", claim))
        }
        if protected and not protected <= quote_tokens:
            return False
        return True
    if _contains_chinese(claim) and not _contains_chinese(quote):
        return _cross_language_action_matches(claim, quote)
    claim_bigrams = _chinese_bigrams(claim)
    quote_bigrams = _chinese_bigrams(quote)
    return bool(claim_bigrams and len(claim_bigrams & quote_bigrams) >= 2)


def _unsupported_protected_tokens(display: str, evidence: str) -> bool:
    evidence_lower = _normalized_text(evidence).lower()
    numeric_tokens = re.findall(r"\d+(?:[.,]\d+)?", display)
    if any(token.lower() not in evidence_lower for token in numeric_tokens):
        return True
    latin_tokens = re.findall(r"[A-Za-z][A-Za-z0-9.+-]*", display)
    protected = {
        token
        for token in latin_tokens
        if any(char.isdigit() for char in token)
        or token.isupper()
        or token.lower() in {
            "openai", "anthropic", "claude", "gemini", "llama", "qwen",
            "deepseek", "mistral", "api", "github",
        }
    }
    return any(token.lower() not in evidence_lower for token in protected)


def _unsupported_action(display: str, evidence: str) -> bool:
    display_lower = display.lower()
    evidence_lower = evidence.lower()
    for markers in _ACTION_GROUPS.values():
        if any(marker in display_lower for marker in markers) and not any(
            marker in evidence_lower for marker in markers
        ):
            return True
    return False


def _valid_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme.lower() in {"http", "https"} and bool(parsed.netloc)


def _parse_aware(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _response_content(response: object) -> str:
    try:
        content = response.choices[0].message.content
    except (AttributeError, IndexError, TypeError) as exc:
        raise ValueError("quality LLM response has no content") from exc
    if not isinstance(content, str) or not content.strip():
        raise ValueError("quality LLM response is empty")
    return content.strip()


class BriefValidator:
    """Validate final text and construct the immutable accepted item."""

    def __init__(
        self,
        config: BriefingConfig,
        quality_llm_config: LLMConfig | None = None,
        *,
        client_factory: Callable[..., object] | None = None,
        timeout: int = 45,
    ) -> None:
        self.config = config
        self.quality_llm_config = quality_llm_config
        self.client_factory = client_factory or _default_client_factory
        self.timeout = timeout
        self._client: object | None = None
        self._quality_circuit_open = False

    def validate(
        self,
        event: MergedEvent,
        draft: BuiltBrief,
        *,
        generation_attempt: int,
        now: datetime | None = None,
    ) -> ValidationResult:
        permanent_reason = self._source_contract_reason(event, draft, now=now)
        if permanent_reason:
            return ValidationResult(
                "reject",
                (permanent_reason,),
                "rules_only",
            )

        issue_reasons = self._display_contract_reasons(event, draft)
        if issue_reasons:
            return self._issue_result(
                event.event_key,
                issue_reasons,
                generation_attempt,
                validation_mode="rules_only",
            )

        requires_semantic_review = _requires_cross_language_semantic_review(draft)
        if not self.quality_llm_config or not self.quality_llm_config.api_key:
            if requires_semantic_review:
                return self._cross_language_review_required_result(
                    event.event_key,
                    generation_attempt,
                )
            return self._accept(
                event,
                draft,
                "rules_only",
                ("quality_llm_unavailable", "rules_only_used"),
            )
        if self._quality_circuit_open:
            if requires_semantic_review:
                return self._cross_language_review_required_result(
                    event.event_key,
                    generation_attempt,
                )
            return self._accept(
                event,
                draft,
                "rules_only",
                ("quality_llm_unavailable", "rules_only_used"),
            )

        try:
            review = self._run_quality_review(event, draft)
        except Exception as exc:
            logger.warning("Quality LLM unavailable; using deterministic rules: %s", exc)
            self._quality_circuit_open = True
            if requires_semantic_review:
                return self._cross_language_review_required_result(
                    event.event_key,
                    generation_attempt,
                )
            return self._accept(
                event,
                draft,
                "rules_only",
                ("quality_llm_unavailable", "rules_only_used"),
            )

        if review is None:
            if requires_semantic_review:
                return self._cross_language_review_required_result(
                    event.event_key,
                    generation_attempt,
                )
            return self._accept(
                event,
                draft,
                "rules_only",
                ("quality_llm_invalid_response", "rules_only_used"),
            )
        action, reasons = review
        if action == "accept":
            return self._accept(event, draft, "rules_and_llm", ())
        return self._issue_result(
            event.event_key,
            reasons,
            generation_attempt,
            validation_mode="rules_and_llm",
        )

    def _source_contract_reason(
        self,
        event: MergedEvent,
        draft: BuiltBrief,
        *,
        now: datetime | None,
    ) -> str | None:
        source = event.canonical_evidence
        if draft.event_key != event.event_key:
            return "invalid_builder_response"
        if not source.source_title.strip() or not source.evidence_text.strip():
            return "missing_evidence"
        if not _valid_http_url(source.url):
            return "missing_source_url"
        published_at = _parse_aware(source.published_at)
        if published_at is None:
            return "stale_item"
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        age = current - published_at
        if age < timedelta(minutes=-5) or age > timedelta(hours=self.config.news_hours):
            return "stale_item"
        return None

    def _display_contract_reasons(
        self,
        event: MergedEvent,
        draft: BuiltBrief,
    ) -> tuple[str, ...]:
        source = event.canonical_evidence
        display = f"{draft.chinese_title}\n{draft.brief}".strip()
        if (
            not draft.chinese_title.strip()
            or not draft.brief.strip()
            or not _contains_chinese(draft.chinese_title)
            or not 1 <= len(_sentences(draft.brief)) <= 2
            or any(not _contains_chinese(sentence) for sentence in _sentences(draft.brief))
            or not draft.evidence_bindings
        ):
            return ("invalid_builder_response",)
        if any(marker in display for marker in _COMMENTARY_MARKERS):
            return ("unsupported_claim",)
        if source.channel == "github":
            evidence_lower = source.evidence_text.lower()
            activity_markers = ("star", "commit", "recent push", "近期活跃")
            publishable_markers = ("release", "readme", "announcement", "发布说明")
            if any(marker in evidence_lower for marker in activity_markers) and not any(
                marker in evidence_lower for marker in publishable_markers
            ):
                return ("github_activity_only",)
        if (
            source.channel == "x"
            and not source.is_official
            and any(
                marker in display.lower()
                for markers in _ACTION_GROUPS.values()
                for marker in markers
            )
            and not any(marker in display for marker in _ATTRIBUTION_MARKERS)
        ):
            return ("community_claim_overstated",)

        evidence_normalized = _normalized_text(source.evidence_text)
        evidence_quote_text = _quote_match_text(source.evidence_text)
        display_claims = _display_claims(draft)
        for display_claim in display_claims:
            if not any(
                _binding_covers(display_claim, binding.claim)
                for binding in draft.evidence_bindings
            ):
                return ("unsupported_claim",)
        for binding in draft.evidence_bindings:
            if binding.source_url != source.url:
                return ("unsupported_claim",)
            quote = _quote_match_text(binding.source_quote)
            if not quote or quote not in evidence_quote_text:
                return ("unsupported_claim",)
            if not _claim_related_to_quote(binding.claim, quote):
                return ("unsupported_claim",)
        if _unsupported_protected_tokens(display, evidence_normalized):
            return ("unsupported_claim",)
        if _unsupported_action(display, evidence_normalized):
            return ("unsupported_claim",)
        return ()

    def _issue_result(
        self,
        event_key: str,
        reasons: tuple[str, ...],
        generation_attempt: int,
        *,
        validation_mode: str,
    ) -> ValidationResult:
        if generation_attempt == 1 and not any(
            reason in {"github_activity_only", "missing_evidence", "missing_source_url", "stale_item"}
            for reason in reasons
        ):
            return ValidationResult(
                "rebuild",
                reasons,
                validation_mode,
                rebuild_request=RebuildRequest(event_key, reasons, 2),
            )
        return ValidationResult("reject", reasons, validation_mode)

    def _cross_language_review_required_result(
        self,
        event_key: str,
        generation_attempt: int,
    ) -> ValidationResult:
        return self._issue_result(
            event_key,
            ("unsupported_claim",),
            generation_attempt,
            validation_mode="rules_only",
        )

    def _accept(
        self,
        event: MergedEvent,
        draft: BuiltBrief,
        validation_mode: str,
        reasons: tuple[str, ...],
    ) -> ValidationResult:
        item = BriefItem(
            event_key=event.event_key,
            chinese_title=draft.chinese_title,
            brief=draft.brief,
            canonical_source=event.canonical_evidence,
            related_sources=event.related_evidence,
            published_at=event.canonical_evidence.published_at,
            evidence_bindings=draft.evidence_bindings,
            content_origin=draft.content_origin,
            validation_mode=validation_mode,
        )
        return ValidationResult(
            "accept",
            reasons,
            validation_mode,
            validated_item=item,
        )

    def _run_quality_review(
        self,
        event: MergedEvent,
        draft: BuiltBrief,
    ) -> tuple[str, tuple[str, ...]] | None:
        response = self._client_instance().chat.completions.create(
            model=self.quality_llm_config.model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "只复核给定中文标题、事实摘要和 canonical evidence 的一致性。"
                        "不得返回或修改正文。严格返回 json 对象 {\"items\":[{\"index\":1,"
                        "\"event_key\":\"...\",\"action\":\"accept|rebuild|reject\","
                        "\"reason_codes\":[]}]}。"
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "index": 1,
                            "event_key": event.event_key,
                            "chinese_title": draft.chinese_title,
                            "brief": draft.brief,
                            "evidence_bindings": [
                                binding.to_dict() for binding in draft.evidence_bindings
                            ],
                            "canonical_evidence": event.canonical_evidence.to_dict(),
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            temperature=0,
            max_tokens=700,
            response_format={"type": "json_object"},
        )
        try:
            payload = json.loads(_response_content(response))
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict) or set(payload) != {"items"}:
            return None
        items = payload["items"]
        if not isinstance(items, list) or len(items) != 1:
            return None
        item = items[0]
        if not isinstance(item, dict) or set(item) != {
            "index",
            "event_key",
            "action",
            "reason_codes",
        }:
            return None
        if item["index"] != 1 or item["event_key"] != event.event_key:
            return None
        if item["action"] not in {"accept", "rebuild", "reject"}:
            return None
        reasons = item["reason_codes"]
        if not isinstance(reasons, list) or not all(
            isinstance(reason, str) and reason in _ALLOWED_QUALITY_REASONS
            for reason in reasons
        ):
            return None
        if item["action"] == "accept" and reasons:
            return None
        if item["action"] != "accept" and not reasons:
            return None
        return item["action"], tuple(dict.fromkeys(reasons))

    def _client_instance(self):
        if self._client is None:
            self._client = self.client_factory(
                api_key=self.quality_llm_config.api_key,
                base_url=self.quality_llm_config.base_url,
                timeout=self.timeout,
            )
        return self._client

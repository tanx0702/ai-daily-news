"""Final-display fact contract validation with optional read-only LLM review."""

from __future__ import annotations

from collections import Counter
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from html import unescape
import json
import logging
import re
from typing import Callable
from urllib.parse import urlparse

from src.briefing.config import BriefingConfig
from src.briefing.display_targets import display_targets, summary_sentences
from src.briefing.models import (
    BriefItem,
    BuiltBrief,
    MergedEvent,
    RebuildRequest,
    ValidationResult,
)
from src.briefing.publishability import (
    EVENT_ACTION_MARKERS,
    asserted_action_types,
    claim_supported_by_quote,
    validate_display_publishability,
    validate_source_publishability,
    validate_update_display_publishability,
    validate_update_source_publishability,
)
from src.llm_config import LLMConfig


logger = logging.getLogger(__name__)

_COMMENTARY_PATTERNS = (
    "值得关注的是",
    "这意味着",
    "这标志着",
    "将产生深远影响",
    "建议读者",
    "我们建议",
)
_UNTRANSLATED_TITLE_WORDS = {
    "a", "acquire", "acquires", "an", "and", "announces", "are", "as",
    "at", "be", "been", "being", "benchmarks", "by", "can", "capitalizes",
    "classes", "could", "denies", "did", "do", "does", "fastest", "for",
    "from", "frustration", "has", "have", "if", "in", "into", "is", "it",
    "its", "may", "more", "new", "not", "of", "on", "open", "optimization",
    "or", "out", "output", "over", "parameters", "problem", "report", "rival",
    "setting", "solver", "source", "startup", "style", "than", "that", "the",
    "their", "this", "to", "tried", "up", "was", "were", "will", "with",
}
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
_ACTION_GROUPS = EVENT_ACTION_MARKERS
_CROSS_LANGUAGE_ANCHOR_STOPWORDS = {
    "acquire", "acquisition", "agentic", "and", "company", "for", "from",
    "funding", "in", "initiative", "introduce", "introducing", "lab", "launch",
    "merge", "model", "models", "new", "of", "on", "open", "out", "platform",
    "product", "products", "raise", "release", "roll", "service", "services",
    "source", "system", "systems", "technology", "the", "to", "tool", "tools",
    "valuation", "vendor", "with", "work", "workflow", "workflows",
}
_CROSS_LANGUAGE_RULE_ONLY_MARKERS = tuple(
    sorted(
        {
            *(
                marker
                for markers in _ACTION_GROUPS.values()
                for marker in markers
                if any("\u4e00" <= char <= "\u9fff" for char in marker)
            ),
            *_ATTRIBUTION_MARKERS,
            "公司", "厂商", "平台", "实验室", "团队", "机构", "模型", "产品",
            "工具", "系统", "服务", "项目", "版本", "该", "其", "一个", "一款",
            "于", "年", "并", "与", "和", "的", "了", "已", "已经", "将", "在",
            "为", "向", "由", "新", "正式",
        },
        key=len,
        reverse=True,
    )
)
_UPDATE_CROSS_LANGUAGE_RULE_ONLY_MARKERS = tuple(
    sorted(
        {
            *_CROSS_LANGUAGE_RULE_ONLY_MARKERS,
            "得分", "高出", "低于", "排名", "位列", "快于", "慢于",
            "速度", "延迟", "测试", "基准",
        },
        key=len,
        reverse=True,
    )
)
_ALLOWED_QUALITY_REASONS = {
    "missing_evidence",
    "missing_source_url",
    "stale_item",
    "unsupported_claim",
    "community_claim_overstated",
    "github_activity_only",
    "invalid_builder_response",
    "translation_failed",
    "semantic_review_rejected",
    "unsupported_commentary",
    "missing_target_binding",
    "unexpected_target_binding",
    "quote_not_found",
    "source_url_mismatch",
    "protected_token_missing",
    "action_not_supported",
    "claim_quote_mismatch",
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
    return list(summary_sentences(value))


def _contains_chinese(value: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in value)


def _has_untranslated_title_prose(value: str) -> bool:
    protected = re.sub(r"https?://\S+", " ", value, flags=re.I)
    protected = re.sub(
        r"(?<![A-Za-z0-9_.-])[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+",
        " ",
        protected,
    )
    protected = re.sub(r'[`\"]([A-Za-z][^`\"]*)[`\"]', " ", protected)
    words = re.findall(r"(?<![A-Za-z0-9])[A-Za-z][A-Za-z-]*(?![A-Za-z0-9])", protected)
    return any(word == word.lower() and word in _UNTRANSLATED_TITLE_WORDS for word in words)


def _display_claims(draft: BuiltBrief) -> list[str]:
    return list(display_targets(draft.chinese_title, draft.brief).values())


def _binding_covers(display_claim: str, binding_claim: str) -> bool:
    display = _comparison_text(display_claim)
    binding = _comparison_text(binding_claim)
    return bool(display and binding and display == binding)


def _quote_match_text(value: object) -> str:
    text = unescape(str(value or "")).replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def _meaningful_tokens(value: str) -> set[str]:
    tokens = set(re.findall(r"[A-Za-z][A-Za-z0-9.+-]*|\d+(?:[.,]\d+)?", value))
    return {
        token.strip(".,;:!?)]}\"'’”").lower()
        for token in tokens
        if token.strip(".,;:!?)]}\"'’”").lower()
        not in {"the", "a", "an"}
    }


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


def _cross_language_anchors(value: str) -> set[str]:
    token_pattern = r"(?<![A-Za-z0-9])[A-Za-z][A-Za-z0-9.+-]*"
    return {
        token.strip(".,;:!?)]}\"'’”").lower()
        for token in re.findall(token_pattern, value)
        if token.strip(".,;:!?)]}\"'’”").lower()
        and token.strip(".,;:!?)]}\"'’”").lower()
        not in _CROSS_LANGUAGE_ANCHOR_STOPWORDS
    }


def _cross_language_rule_only_verifiable(claim: str, quote: str) -> bool:
    return _cross_language_rule_only_verifiable_with_markers(
        claim,
        quote,
        _CROSS_LANGUAGE_RULE_ONLY_MARKERS,
    )


def _cross_language_rule_only_verifiable_with_markers(
    claim: str,
    quote: str,
    allowed_markers: tuple[str, ...],
) -> bool:
    claim_anchors = _cross_language_anchors(claim)
    if not claim_anchors or not claim_anchors <= _cross_language_anchors(quote):
        return False
    residual = "".join(re.findall(r"[\u4e00-\u9fff]", claim))
    for marker in allowed_markers:
        residual = residual.replace(marker, "")
    return not residual


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
        self.diagnostics: Counter[str] = Counter(
            quality_llm_success_count=0,
            quality_llm_timeout_count=0,
            quality_llm_invalid_response_count=0,
            quality_llm_unavailable_count=0,
            quality_llm_circuit_open_count=0,
        )

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

        if event.canonical_evidence.content_type == "attributed_opinion":
            opinion_reasons = self._opinion_contract_reasons(event, draft)
            if opinion_reasons:
                return self._issue_result(
                    event.event_key,
                    opinion_reasons,
                    generation_attempt,
                    validation_mode="rules_only",
                    audited_draft=draft,
                )
        else:
            source_editorial = (
                validate_update_source_publishability(event.canonical_evidence)
                if event.canonical_evidence.content_type == "ai_update"
                else validate_source_publishability(event.canonical_evidence)
            )
            if not source_editorial.accepted:
                return ValidationResult(
                    "reject",
                    source_editorial.reason_codes,
                    "rules_only",
                    audited_draft=draft,
                )

        issue_reasons = self._display_contract_reasons(event, draft)
        if issue_reasons:
            return self._issue_result(
                event.event_key,
                issue_reasons,
                generation_attempt,
                validation_mode="rules_only",
            )

        normalized_draft, _removed_sentences = self._normalize_title_restatements(
            event, draft
        )
        draft = normalized_draft

        if not self.quality_llm_config or not self.quality_llm_config.api_key:
            self.diagnostics["quality_llm_unavailable_count"] += 1
            return self._rules_only_result(
                event,
                draft,
                generation_attempt,
                ("quality_llm_unavailable", "rules_only_used"),
            )
        if self._quality_circuit_open:
            self.diagnostics["quality_llm_circuit_open_count"] += 1
            return self._rules_only_result(
                event,
                draft,
                generation_attempt,
                ("quality_llm_unavailable", "rules_only_used"),
            )

        try:
            review = self._run_quality_review(event, draft)
        except Exception as exc:
            logger.warning("Quality LLM unavailable; using deterministic rules: %s", exc)
            self._quality_circuit_open = True
            if isinstance(exc, TimeoutError) or "timeout" in str(exc).lower():
                self.diagnostics["quality_llm_timeout_count"] += 1
            else:
                self.diagnostics["quality_llm_unavailable_count"] += 1
            return self._rules_only_result(
                event,
                draft,
                generation_attempt,
                ("quality_llm_unavailable", "rules_only_used"),
            )

        if review is None:
            self.diagnostics["quality_llm_invalid_response_count"] += 1
            return self._rules_only_result(
                event,
                draft,
                generation_attempt,
                ("quality_llm_invalid_response", "rules_only_used"),
            )
        action, reasons = review
        self.diagnostics["quality_llm_success_count"] += 1
        if action == "accept":
            return self._accept(event, draft, "rules_and_llm", ())
        return self._issue_result(
            event.event_key,
            ("semantic_review_rejected",),
            generation_attempt,
            validation_mode="rules_and_llm",
            audited_draft=draft,
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
        if draft.content_type != source.content_type:
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
            or not _contains_chinese(draft.chinese_title)
            or not 0 <= len(_sentences(draft.brief)) <= 2
            or any(not _contains_chinese(sentence) for sentence in _sentences(draft.brief))
            or not draft.evidence_bindings
        ):
            return ("invalid_builder_response",)
        if (
            draft.content_origin == "llm"
            and _has_untranslated_title_prose(draft.chinese_title)
        ):
            return ("translation_failed",)
        if any(pattern in display for pattern in _COMMENTARY_PATTERNS):
            return ("unsupported_commentary",)
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
        bindings_by_claim: dict[str, list[object]] = {}
        for display_claim in display_claims:
            matching = [
                binding
                for binding in draft.evidence_bindings
                if _binding_covers(display_claim, binding.claim)
            ]
            if not matching:
                return ("missing_target_binding",)
            bindings_by_claim.setdefault(display_claim, []).extend(matching)
        if any(
            not any(
                _binding_covers(display_claim, binding.claim)
                for display_claim in display_claims
            )
            for binding in draft.evidence_bindings
        ):
            return ("unexpected_target_binding",)
        for binding in draft.evidence_bindings:
            if binding.source_url != source.url:
                return ("source_url_mismatch",)
            quote = _quote_match_text(binding.source_quote)
            if not quote or quote not in evidence_quote_text:
                return ("quote_not_found",)
        if source.content_type != "attributed_opinion":
            editorial = (
                validate_update_display_publishability(
                    draft.chinese_title,
                    draft.brief,
                    source,
                )
                if source.content_type == "ai_update"
                else validate_display_publishability(
                    draft.chinese_title,
                    draft.brief,
                    source,
                )
            )
            if not editorial.accepted:
                return editorial.reason_codes
        if _unsupported_protected_tokens(display, evidence_normalized):
            return ("protected_token_missing",)
        if _unsupported_action(display, evidence_normalized):
            return ("action_not_supported",)
        for display_claim, bindings in bindings_by_claim.items():
            combined_quotes = " ".join(
                _quote_match_text(binding.source_quote) for binding in bindings
            )
            if _unsupported_action(display_claim, combined_quotes):
                return ("action_not_supported",)
            if (
                display_claim == draft.chinese_title
                and asserted_action_types(display_claim)
                and not claim_supported_by_quote(
                    display_claim,
                    source.source_title,
                    source=source,
                )
            ):
                return ("claim_quote_mismatch",)
            cross_language = (
                _contains_chinese(display_claim)
                and not _contains_chinese(combined_quotes)
            )
            related = _claim_related_to_quote(display_claim, combined_quotes)
            if cross_language:
                claim_anchors = _cross_language_anchors(display_claim)
                quote_anchors = _cross_language_anchors(combined_quotes)
                if claim_anchors and not claim_anchors <= quote_anchors:
                    return ("claim_quote_mismatch",)
                quote_tokens = _meaningful_tokens(combined_quotes)
                numeric_overlap = set(
                    re.findall(r"\d+(?:[.,]\d+)?", display_claim)
                ) & set(re.findall(r"\d+(?:[.,]\d+)?", combined_quotes))
                latin_overlap = {
                    token.lower()
                    for token in re.findall(r"[A-Za-z][A-Za-z0-9.+-]*", display_claim)
                } & quote_tokens
                action_overlap = _cross_language_action_matches(
                    display_claim,
                    combined_quotes,
                ) and any(
                    marker in display_claim.lower()
                    for markers in _ACTION_GROUPS.values()
                    for marker in markers
                )
                related = bool(numeric_overlap or latin_overlap or action_overlap)
            if not related:
                return ("claim_quote_mismatch",)
        return ()

    def _opinion_contract_reasons(
        self,
        event: MergedEvent,
        draft: BuiltBrief,
    ) -> tuple[str, ...]:
        source = event.canonical_evidence
        if (
            draft.content_type != "attributed_opinion"
            or not source.opinion_eligible
            or not source.original_post
            or not source.context_complete
        ):
            return ("opinion_author_not_allowed",)
        author = source.opinion_author.strip()
        if not author or author.lower() not in draft.chinese_title.lower():
            return ("opinion_attribution_missing",)
        if draft.opinion_author.strip().lower() != author.lower():
            return ("opinion_attribution_missing",)
        return ()

    def _normalize_title_restatements(
        self,
        event: MergedEvent,
        draft: BuiltBrief,
    ) -> tuple[BuiltBrief, tuple[str, ...]]:
        """Remove only summary sentences whose evidence is entirely in the title."""
        sentences = list(_sentences(draft.brief))
        if not sentences:
            return replace(
                draft,
                brief="",
                brief_mode="title_only",
                brief_reason=draft.brief_reason or "brief_empty",
            ), ()

        source_title = _quote_match_text(event.canonical_evidence.source_title)
        title_claim = draft.chinese_title.strip()
        title_bindings = [
            binding
            for binding in draft.evidence_bindings
            if _binding_covers(title_claim, binding.claim)
        ]
        kept: list[str] = []
        kept_bindings = list(title_bindings)
        removed: list[str] = []
        for sentence in sentences:
            sentence_bindings = [
                binding
                for binding in draft.evidence_bindings
                if _binding_covers(sentence, binding.claim)
            ]
            restates_generated_title = (
                _comparison_text(sentence) == _comparison_text(title_claim)
            )
            supported_only_by_source_title = sentence_bindings and all(
                _quote_match_text(binding.source_quote) in source_title
                for binding in sentence_bindings
            )
            if restates_generated_title or supported_only_by_source_title:
                removed.append(sentence)
                continue
            kept.append(sentence)
            kept_bindings.extend(sentence_bindings)

        if not removed:
            return draft, ()
        brief = "。".join(kept) + "。" if kept else ""
        normalized = replace(
            draft,
            brief=brief,
            evidence_bindings=tuple(kept_bindings),
            brief_mode="expanded" if kept else "title_only",
            brief_reason="brief_restates_title",
        )
        return normalized, tuple(removed)

    def _rules_only_relationship_reasons(
        self,
        draft: BuiltBrief,
    ) -> tuple[str, ...]:
        allowed_markers = (
            _UPDATE_CROSS_LANGUAGE_RULE_ONLY_MARKERS
            if draft.content_type == "ai_update"
            else _CROSS_LANGUAGE_RULE_ONLY_MARKERS
        )
        for display_claim in _display_claims(draft):
            combined_quotes = " ".join(
                _quote_match_text(binding.source_quote)
                for binding in draft.evidence_bindings
                if _binding_covers(display_claim, binding.claim)
            )
            if (
                _contains_chinese(display_claim)
                and not _contains_chinese(combined_quotes)
                and not _cross_language_rule_only_verifiable_with_markers(
                    display_claim, combined_quotes, allowed_markers
                )
            ):
                return ("claim_quote_mismatch",)
        return ()

    def _rules_only_result(
        self,
        event: MergedEvent,
        draft: BuiltBrief,
        generation_attempt: int,
        degradation_reasons: tuple[str, ...],
    ) -> ValidationResult:
        relationship_reasons = self._rules_only_relationship_reasons(draft)
        if relationship_reasons:
            return self._issue_result(
                event.event_key,
                relationship_reasons,
                generation_attempt,
                validation_mode="rules_only",
                audited_draft=draft,
            )
        return self._accept(event, draft, "rules_only", degradation_reasons)

    def _issue_result(
        self,
        event_key: str,
        reasons: tuple[str, ...],
        generation_attempt: int,
        *,
        validation_mode: str,
        audited_draft: BuiltBrief | None = None,
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
                audited_draft=audited_draft,
            )
        return ValidationResult(
            "reject",
            reasons,
            validation_mode,
            audited_draft=audited_draft,
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
            brief_mode=draft.brief_mode,
            brief_reason=draft.brief_reason,
            content_type=draft.content_type,
            opinion_author=draft.opinion_author,
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
                max_retries=0,
            )
        return self._client

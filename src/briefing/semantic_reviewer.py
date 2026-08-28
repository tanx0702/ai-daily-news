"""Bounded read-only LLM review for prefiltered semantic duplicate pairs."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import json
import logging
import re
from typing import Callable
import unicodedata

from src.briefing.semantic import (
    EventDocument,
    shared_strong_subjects,
)
from src.llm_config import LLMConfig, structured_llm_request_options


logger = logging.getLogger(__name__)
_BROAD_ORGANIZATIONS = {
    "anthropic",
    "deepseek",
    "google",
    "google deepmind",
    "meta",
    "microsoft",
    "mistral ai",
    "nvidia",
    "openai",
    "qwen",
}
_GENERIC_SUBJECTS = {
    "ai",
    "company",
    "companies",
    "executive",
    "executives",
    "leader",
    "leaders",
    "organization",
    "organizations",
    "team",
    "teams",
}


class SemanticResponseError(ValueError):
    """Raised only when an LLM response cannot satisfy the JSON contract."""


def _default_client_factory(**kwargs):
    from openai import OpenAI

    return OpenAI(**kwargs)


@dataclass(frozen=True, slots=True)
class SemanticReview:
    relationship: str
    comparison_mode: str
    reason_code: str | None = None

    def __post_init__(self) -> None:
        if self.relationship not in {"same_event", "distinct", "uncertain"}:
            raise ValueError(f"invalid semantic relationship: {self.relationship}")
        if self.comparison_mode not in {"rules", "rules_and_llm"}:
            raise ValueError(f"invalid comparison mode: {self.comparison_mode}")


class SemanticDuplicateReviewer:
    """Classify evidence pairs without altering either input document."""

    def __init__(
        self,
        quality_llm_config: LLMConfig | None,
        *,
        client_factory: Callable[..., object] | None = None,
        timeout: int = 45,
        max_calls: int = 20,
    ) -> None:
        self.quality_llm_config = quality_llm_config
        self.client_factory = client_factory or _default_client_factory
        self.timeout = timeout
        self.max_calls = max_calls
        self._client: object | None = None
        self._calls = 0
        self._circuit_open = False
        self.diagnostics: Counter[str] = Counter(
            semantic_llm_success_count=0,
            semantic_llm_timeout_count=0,
            semantic_llm_invalid_response_count=0,
            semantic_llm_unavailable_count=0,
            semantic_llm_circuit_open_count=0,
            semantic_dedup_budget_exhausted_count=0,
        )

    def review(self, left: EventDocument, right: EventDocument) -> SemanticReview:
        if self._circuit_open:
            self.diagnostics["semantic_llm_circuit_open_count"] += 1
            return SemanticReview(
                "uncertain", "rules", "semantic_llm_circuit_open"
            )
        if self._calls >= self.max_calls:
            self.diagnostics["semantic_dedup_budget_exhausted_count"] += 1
            return SemanticReview(
                "uncertain", "rules", "semantic_dedup_budget_exhausted"
            )
        if not self._available():
            self.diagnostics["semantic_llm_unavailable_count"] += 1
            return SemanticReview(
                "uncertain", "rules", "semantic_llm_unavailable"
            )

        self._calls += 1
        try:
            payload = self._request(left, right)
        except SemanticResponseError as exc:
            self.diagnostics["semantic_llm_invalid_response_count"] += 1
            logger.warning("Semantic duplicate LLM returned invalid content: %s", exc)
            return SemanticReview(
                "uncertain", "rules", "semantic_llm_invalid_response"
            )
        except Exception as exc:
            self._circuit_open = True
            if isinstance(exc, TimeoutError) or "timeout" in str(exc).casefold():
                self.diagnostics["semantic_llm_timeout_count"] += 1
            else:
                self.diagnostics["semantic_llm_unavailable_count"] += 1
            logger.warning("Semantic duplicate LLM unavailable: %s", exc)
            return SemanticReview(
                "uncertain", "rules", "semantic_llm_unavailable"
            )

        relationship = _validate_payload(payload, left, right)
        if relationship is None:
            self.diagnostics["semantic_llm_invalid_response_count"] += 1
            return SemanticReview(
                "uncertain", "rules", "semantic_llm_invalid_response"
            )
        self.diagnostics["semantic_llm_success_count"] += 1
        return SemanticReview(relationship, "rules_and_llm")

    def _available(self) -> bool:
        config = self.quality_llm_config
        return bool(
            config
            and config.api_key.strip()
            and config.model.strip()
            and config.base_url.strip()
        )

    def _request(
        self,
        left: EventDocument,
        right: EventDocument,
    ) -> object:
        response = self._client_instance().chat.completions.create(
            model=self.quality_llm_config.model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "只判断两条冻结来源证据是否描述同一现实新闻事件。"
                        "不得改写或补充事实。严格返回 json 对象，且只能包含"
                        '{"relationship":"same_event|distinct|uncertain",'
                        '"shared_subjects":[],"shared_action":""}。'
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "left": _document_payload(left),
                            "right": _document_payload(right),
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            temperature=0,
            max_tokens=250,
            response_format={"type": "json_object"},
            **structured_llm_request_options(self.quality_llm_config),
        )
        try:
            return json.loads(_response_content(response))
        except json.JSONDecodeError as exc:
            raise SemanticResponseError("semantic LLM response is invalid JSON") from exc

    def _client_instance(self):
        if self._client is None:
            self._client = self.client_factory(
                api_key=self.quality_llm_config.api_key,
                base_url=self.quality_llm_config.base_url,
                timeout=self.timeout,
                max_retries=0,
            )
        return self._client


def _document_payload(document: EventDocument) -> dict[str, object]:
    return {
        "source_title": document.evidence.source_title,
        "evidence_text": document.evidence.evidence_text,
        "url": document.evidence.url,
        "published_at": document.evidence.published_at,
        "organizations": sorted(document.features.organizations),
        "people": sorted(document.features.people),
        "models": sorted(document.features.models),
        "actions": sorted(document.features.actions),
        "qualifiers": sorted(document.features.qualifiers),
    }


def _response_content(response: object) -> str:
    try:
        content = response.choices[0].message.content
    except (AttributeError, IndexError, TypeError) as exc:
        raise SemanticResponseError("semantic LLM response has no content") from exc
    if not isinstance(content, str) or not content.strip():
        raise SemanticResponseError("semantic LLM response is empty")
    return content.strip()


def _validate_payload(
    payload: object,
    left: EventDocument,
    right: EventDocument,
) -> str | None:
    if not isinstance(payload, dict) or set(payload) != {
        "relationship",
        "shared_subjects",
        "shared_action",
    }:
        return None
    relationship = payload["relationship"]
    subjects = payload["shared_subjects"]
    action = payload["shared_action"]
    if relationship not in {"same_event", "distinct", "uncertain"}:
        return None
    if not isinstance(subjects, list) or not all(
        isinstance(subject, str) and subject.strip() for subject in subjects
    ):
        return None
    if not isinstance(action, str):
        return None
    if relationship == "same_event" and (not subjects or not action.strip()):
        return None
    if relationship == "same_event":
        normalized_shared_strong_subjects = {
            _comparison_text(subject)
            for subject in shared_strong_subjects(left, right)
        }
        if not any(
            _comparison_text(subject) in normalized_shared_strong_subjects
            and _comparison_text(subject) not in _BROAD_ORGANIZATIONS
            and _comparison_text(subject) not in _GENERIC_SUBJECTS
            for subject in subjects
        ):
            return None
    if not all(
        _anchored(subject, left.text) and _anchored(subject, right.text)
        for subject in subjects
    ):
        return None
    if action.strip():
        normalized_action = _comparison_text(action)
        if normalized_action not in left.features.actions:
            return None
        if normalized_action not in right.features.actions:
            return None
    return str(relationship)


def _anchored(value: str, text: str) -> bool:
    normalized_value = _comparison_text(value)
    normalized_text = _comparison_text(text)
    if not normalized_value:
        return False
    if any("\u4e00" <= character <= "\u9fff" for character in normalized_value):
        return normalized_value in normalized_text
    return bool(
        re.search(
            rf"(?<![a-z0-9]){re.escape(normalized_value)}(?![a-z0-9])",
            normalized_text,
        )
    )


def _comparison_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", normalized).strip()

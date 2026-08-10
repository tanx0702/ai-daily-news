"""Evidence-bound Chinese fact brief generation with bounded attempts."""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import re
from typing import Callable, Mapping, Sequence

from src.briefing.config import BriefingConfig
from src.briefing.models import BuiltBrief, EvidenceBinding, MergedEvent
from src.llm_config import LLMConfig


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class BuildResult:
    event_key: str
    generation_attempt: int
    draft: BuiltBrief | None
    reason_code: str | None
    circuit_open: bool = False


def _default_client_factory(**kwargs):
    from openai import OpenAI

    return OpenAI(**kwargs)


def _response_content(response: object) -> str:
    try:
        content = response.choices[0].message.content
    except (AttributeError, IndexError, TypeError) as exc:
        raise ValueError("LLM response has no assistant content") from exc
    if not isinstance(content, str) or not content.strip():
        raise ValueError("LLM response content is empty")
    return content.strip()


def _is_nonrecoverable(error: Exception) -> bool:
    status_code = getattr(error, "status_code", None)
    if status_code in {401, 403, 404}:
        return True
    text = str(error).lower()
    markers = (
        "401",
        "403",
        "unauthorized",
        "invalid api key",
        "invalid_api_key",
        "invalid model",
        "model not found",
        "unsupported protocol",
        "invalid url",
        "invalid base url",
    )
    return any(marker in text for marker in markers)


def _contains_chinese(value: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in value)


def _source_fallback(event: MergedEvent, input_index: int) -> BuiltBrief | None:
    evidence = event.canonical_evidence
    title = evidence.source_title.strip()
    if not title or not _contains_chinese(title):
        return None

    body = evidence.evidence_text.strip()
    remainder = body
    if title and title in remainder:
        remainder = remainder.replace(title, "", 1).strip(" \n。；;：:")
    sentences = [
        part.strip()
        for part in re.split(r"(?<=[。！？!?])\s*|\n+", remainder)
        if part.strip() and _contains_chinese(part)
    ][:2]
    if not sentences:
        sentences = [title]
    brief = "".join(sentences)
    claims = [title]
    claims.extend(
        claim.strip().rstrip("。！？!? ")
        for sentence in sentences
        for claim in re.split(r"[，,；;]", sentence)
        if claim.strip().rstrip("。！？!? ")
    )
    bindings = [
        EvidenceBinding(claim=claim, source_quote=claim, source_url=evidence.url)
        for claim in dict.fromkeys(claims)
    ]
    return BuiltBrief(
        event_key=event.event_key,
        input_index=input_index,
        chinese_title=title,
        brief=brief,
        evidence_bindings=tuple(bindings),
        content_origin="source",
    )


def _strict_item(
    raw: object,
    *,
    event: MergedEvent,
    input_index: int,
) -> BuiltBrief | None:
    if not isinstance(raw, dict):
        return None
    required = {
        "index",
        "event_key",
        "chinese_title",
        "brief",
        "evidence_bindings",
    }
    if set(raw) != required:
        return None
    if isinstance(raw["index"], bool) or not isinstance(raw["index"], int):
        return None
    if raw["index"] != input_index or raw["event_key"] != event.event_key:
        return None
    if not isinstance(raw["chinese_title"], str) or not raw["chinese_title"].strip():
        return None
    if not isinstance(raw["brief"], str) or not raw["brief"].strip():
        return None
    raw_bindings = raw["evidence_bindings"]
    if not isinstance(raw_bindings, list) or not raw_bindings:
        return None

    bindings: list[EvidenceBinding] = []
    for binding in raw_bindings:
        if not isinstance(binding, dict) or set(binding) != {
            "claim",
            "source_quote",
            "source_url",
        }:
            return None
        if not all(
            isinstance(binding[field], str) and binding[field].strip()
            for field in ("claim", "source_quote", "source_url")
        ):
            return None
        bindings.append(
            EvidenceBinding(
                claim=binding["claim"].strip(),
                source_quote=binding["source_quote"].strip(),
                source_url=binding["source_url"].strip(),
            )
        )

    return BuiltBrief(
        event_key=event.event_key,
        input_index=input_index,
        chinese_title=raw["chinese_title"].strip(),
        brief=raw["brief"].strip(),
        evidence_bindings=tuple(bindings),
        content_origin="llm",
    )


class BriefBuilder:
    """Generate final-display drafts while sharing a two-attempt item budget."""

    def __init__(
        self,
        config: BriefingConfig,
        llm_config: LLMConfig,
        *,
        client_factory: Callable[..., object] | None = None,
        timeout: int = 30,
    ) -> None:
        self.config = config
        self.llm_config = llm_config
        self.client_factory = client_factory or _default_client_factory
        self.timeout = timeout
        self._client: object | None = None
        self._circuit_open = False

    def build_batch(
        self,
        events: Sequence[MergedEvent],
        attempts: Mapping[str, int],
        rebuild_reasons: Mapping[str, Sequence[str]] | None = None,
    ) -> tuple[BuildResult, ...]:
        results: list[BuildResult] = []
        for start in range(0, len(events), self.config.builder_batch_size):
            chunk = list(events[start:start + self.config.builder_batch_size])
            results.extend(
                self._build_chunk(chunk, attempts, rebuild_reasons or {})
            )
        return tuple(results)

    def _build_chunk(
        self,
        events: list[MergedEvent],
        attempts: Mapping[str, int],
        rebuild_reasons: Mapping[str, Sequence[str]],
    ) -> list[BuildResult]:
        if not events:
            return []
        current_attempts = {
            event.event_key: min(int(attempts.get(event.event_key, 0)) + 1, 2)
            for event in events
        }

        if not self.llm_config.api_key or self._circuit_open:
            self._circuit_open = True
            return [
                self._fallback_result(event, index, current_attempts[event.event_key])
                for index, event in enumerate(events, 1)
            ]

        payload = {
            "events": [
                {
                    "index": index,
                    "event_key": event.event_key,
                    "source_title": event.canonical_evidence.source_title,
                    "evidence_text": event.canonical_evidence.evidence_text,
                    "source_url": event.canonical_evidence.url,
                    "publisher": event.canonical_evidence.publisher_name,
                    "channel": event.canonical_evidence.channel,
                    "is_official": event.canonical_evidence.is_official,
                    "rebuild_reasons": list(rebuild_reasons.get(event.event_key, ())),
                }
                for index, event in enumerate(events, 1)
            ]
        }
        try:
            response = self._client_instance().chat.completions.create(
                model=self.llm_config.model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "你是 AI 圈事实快讯编辑。只根据每条 canonical source 证据生成中文标题和"
                            "一至两句事实摘要，不写评论、趋势、影响分析或输入外事实。为标题和摘要中的"
                            "每个原子事实返回 claim/source_quote/source_url；quote 必须逐字来自 evidence_text，"
                            "url 必须等于该条 source_url。严格返回 JSON 对象 {\"items\":[...]}，每条必须"
                            "包含且只包含 index、event_key、chinese_title、brief、evidence_bindings。"
                        ),
                    },
                    {
                        "role": "user",
                        "content": json.dumps(payload, ensure_ascii=False),
                    },
                ],
                temperature=0.1,
                max_tokens=3000,
                response_format={"type": "json_object"},
            )
            decoded = json.loads(_response_content(response))
            raw_items = decoded.get("items") if isinstance(decoded, dict) else None
            if not isinstance(raw_items, list):
                raw_items = []
        except Exception as exc:
            if _is_nonrecoverable(exc):
                logger.error("Content LLM circuit opened: %s", exc)
                self._circuit_open = True
                return [
                    self._fallback_result(event, index, current_attempts[event.event_key])
                    for index, event in enumerate(events, 1)
                ]
            logger.warning("Brief builder request failed: %s", exc)
            raw_items = []

        by_index: dict[int, list[dict]] = {}
        for raw in raw_items:
            if not isinstance(raw, dict):
                continue
            index = raw.get("index")
            if isinstance(index, bool) or not isinstance(index, int):
                continue
            if 1 <= index <= len(events):
                by_index.setdefault(index, []).append(raw)

        results: list[BuildResult] = []
        for index, event in enumerate(events, 1):
            attempt = current_attempts[event.event_key]
            candidates = by_index.get(index, [])
            draft = (
                _strict_item(candidates[0], event=event, input_index=index)
                if len(candidates) == 1
                else None
            )
            if draft is not None:
                results.append(
                    BuildResult(event.event_key, attempt, draft, None, self._circuit_open)
                )
            elif attempt >= 2:
                results.append(self._fallback_result(event, index, attempt))
            else:
                results.append(
                    BuildResult(
                        event.event_key,
                        attempt,
                        None,
                        "invalid_builder_response",
                        self._circuit_open,
                    )
                )
        return results

    def _client_instance(self):
        if self._client is None:
            self._client = self.client_factory(
                api_key=self.llm_config.api_key,
                base_url=self.llm_config.base_url,
                timeout=self.timeout,
            )
        return self._client

    def _fallback_result(
        self,
        event: MergedEvent,
        input_index: int,
        attempt: int,
    ) -> BuildResult:
        draft = _source_fallback(event, input_index)
        if draft is not None:
            return BuildResult(
                event.event_key,
                attempt,
                draft,
                "source_fallback_used",
                self._circuit_open,
            )
        return BuildResult(
            event.event_key,
            attempt,
            None,
            "translation_failed",
            self._circuit_open,
        )

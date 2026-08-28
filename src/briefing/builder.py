"""Evidence-bound Chinese fact brief generation with bounded attempts."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import json
import logging
import re
from typing import Callable, Mapping, Sequence
from urllib.parse import urlparse

from src.briefing.config import BriefingConfig
from src.briefing.display_targets import display_targets, summary_sentences
from src.briefing.models import BuiltBrief, EvidenceBinding, MergedEvent
from src.briefing.publishability import source_anchored_title
from src.llm_config import LLMConfig


logger = logging.getLogger(__name__)


_CONSECUTIVE_TIMEOUT_CIRCUIT_THRESHOLD = 3


_SOURCE_QUOTE_PATTERN = re.compile(
    r".+?(?:[。！？!?；;]+|[A-Za-z0-9]\.(?=\s|$)|\r?\n+|$)",
    re.DOTALL,
)


def _source_quotes(value: str) -> tuple[tuple[str, str], ...]:
    quotes: list[tuple[str, str]] = []
    seen: set[str] = set()
    for match in _SOURCE_QUOTE_PATTERN.finditer(value):
        quote = match.group(0).strip()
        if not quote or quote in seen:
            continue
        seen.add(quote)
        quotes.append((f"q{len(quotes) + 1}", quote))
    return tuple(quotes)


@dataclass(frozen=True, slots=True)
class BuildResult:
    event_key: str
    generation_attempt: int
    draft: BuiltBrief | None
    reason_code: str | None
    circuit_open: bool = False
    source_fallback_used: bool = False


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
    if status_code in {401, 402, 403, 404} or (
        isinstance(status_code, int) and (status_code == 429 or status_code >= 500)
    ):
        return True
    text = str(error).lower()
    markers = (
        "401",
        "402",
        "403",
        "unauthorized",
        "invalid api key",
        "invalid_api_key",
        "invalid model",
        "model not found",
        "insufficient balance",
        "payment required",
        "subscription_not_found",
        "subscription not found",
        "unsupported protocol",
        "invalid url",
        "invalid base url",
        "429",
        "bad gateway",
        "service unavailable",
        "gateway timeout",
    )
    return any(marker in text for marker in markers)


def _is_timeout(error: Exception) -> bool:
    error_name = type(error).__name__.lower()
    text = str(error).lower()
    return (
        isinstance(error, TimeoutError)
        or "timeout" in error_name
        or "timeout" in text
        or "timed out" in text
    )


def _contains_chinese(value: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in value)


def _normalize_brief_value(value: object) -> str | None:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list) and 1 <= len(value) <= 2 and all(
        isinstance(sentence, str) and sentence.strip() for sentence in value
    ):
        return "".join(sentence.strip() for sentence in value)
    return None


def _protected_anchors(event: MergedEvent) -> list[str]:
    source = event.canonical_evidence
    title_text = f"{source.publisher_name} {source.source_title}"
    evidence_text = source.evidence_text
    try:
        parsed_url = urlparse(source.url)
        path_parts = [part for part in parsed_url.path.split("/") if part]
        hostname = parsed_url.hostname
    except ValueError:
        path_parts = []
        hostname = None
    x_handle = (
        f"@{path_parts[0]}"
        if hostname in {"x.com", "www.x.com"}
        and len(path_parts) >= 3
        and path_parts[1] == "status"
        else None
    )
    anchors = [
        *([x_handle] if x_handle else []),
        *re.findall(r"@[A-Za-z0-9_]+", evidence_text),
        *re.findall(r"\d+(?:\.\d+)*", evidence_text),
        *(
            token
            for token in re.findall(r"[A-Za-z][A-Za-z0-9.+-]*", title_text)
            if any(char.isupper() or char.isdigit() for char in token)
        ),
    ]
    return list(dict.fromkeys(anchors))


def _source_fallback(
    event: MergedEvent,
    input_index: int,
    *,
    allow_anchored_english_title: bool = False,
) -> BuiltBrief | None:
    evidence = event.canonical_evidence
    title = evidence.source_title.strip()
    source_title_is_chinese = _contains_chinese(title)
    if not title or not source_title_is_chinese:
        if not allow_anchored_english_title:
            return None
        title = source_anchored_title(evidence) or ""
    if not title:
        return None

    if not source_title_is_chinese:
        return BuiltBrief(
            event_key=event.event_key,
            input_index=input_index,
            chinese_title=title,
            brief="",
            evidence_bindings=(
                EvidenceBinding(
                    claim=title,
                    source_quote=evidence.source_title,
                    source_url=evidence.url,
                ),
            ),
            content_origin="source",
            brief_mode="title_only",
            brief_reason="brief_empty",
            content_type=evidence.content_type,
            opinion_author=evidence.opinion_author,
        )

    body = evidence.evidence_text.strip()
    remainder = body
    if title and title in remainder:
        remainder = remainder.replace(title, "", 1).strip(" \n。；;：:")
    sentences = [
        part
        for part in summary_sentences(remainder)
        if part and _contains_chinese(part)
    ][:2]
    brief = "。".join(sentences) + "。" if sentences else ""
    claims = [title, *summary_sentences(brief)]
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
        brief_mode="expanded" if brief else "title_only",
        brief_reason="" if brief else "brief_empty",
        content_type=evidence.content_type,
        opinion_author=evidence.opinion_author,
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
        "evidence_targets",
    }
    if set(raw) != required:
        return None
    if isinstance(raw["index"], bool) or not isinstance(raw["index"], int):
        return None
    if raw["index"] != input_index or raw["event_key"] != event.event_key:
        return None
    if not isinstance(raw["chinese_title"], str) or not raw["chinese_title"].strip():
        return None
    brief = _normalize_brief_value(raw["brief"])
    if brief is None:
        return None
    target_claims = display_targets(raw["chinese_title"], brief)
    if not 0 <= len(target_claims) - 1 <= 2:
        return None
    raw_bindings = raw["evidence_targets"]
    if not isinstance(raw_bindings, list) or not raw_bindings:
        return None

    quote_by_id = dict(_source_quotes(event.canonical_evidence.evidence_text))
    bindings: list[EvidenceBinding] = []
    bindings_by_target: dict[str, list[EvidenceBinding]] = {
        target: [] for target in target_claims
    }
    provided_targets: set[str] = set()
    unresolved_brief = False
    for binding in raw_bindings:
        if not isinstance(binding, dict) or set(binding) != {
            "target",
            "source_quote_id",
        }:
            return None
        if not isinstance(binding["target"], str) or not binding["target"].strip():
            return None
        target = binding["target"].strip()
        if target not in target_claims:
            return None
        provided_targets.add(target)
        if (
            not isinstance(binding["source_quote_id"], str)
            or not binding["source_quote_id"].strip()
        ):
            return None
        quote = quote_by_id.get(binding["source_quote_id"].strip())
        if quote is None:
            if target == "title":
                return None
            unresolved_brief = True
            continue
        resolved = EvidenceBinding(
            claim=target_claims[target],
            source_quote=quote,
            source_url=event.canonical_evidence.url,
        )
        bindings.append(resolved)
        bindings_by_target[target].append(resolved)
    title_bindings = bindings_by_target["title"]
    if "title" not in provided_targets or not title_bindings:
        return None
    brief_targets = set(target_claims) - {"title"}
    if brief_targets - provided_targets:
        unresolved_brief = True
    if unresolved_brief:
        brief = ""
        bindings = title_bindings
        brief_reason = "brief_quote_unresolved"
    else:
        brief_reason = "" if brief else "brief_empty"

    return BuiltBrief(
        event_key=event.event_key,
        input_index=input_index,
        chinese_title=raw["chinese_title"].strip(),
        brief=brief,
        evidence_bindings=tuple(bindings),
        content_origin="llm",
        brief_mode="expanded" if brief else "title_only",
        brief_reason=brief_reason,
        content_type=event.canonical_evidence.content_type,
        opinion_author=event.canonical_evidence.opinion_author,
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
        self._consecutive_timeouts = 0
        self.diagnostics: Counter[str] = Counter(
            content_llm_success_count=0,
            content_llm_timeout_count=0,
            content_llm_invalid_response_count=0,
            content_llm_unavailable_count=0,
            content_llm_circuit_open_count=0,
        )

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

        if not self.llm_config.api_key:
            self._circuit_open = True
            self.diagnostics["content_llm_unavailable_count"] += 1
            return [
                self._fallback_result(
                    event,
                    index,
                    current_attempts[event.event_key],
                    failure_reason="content_llm_unavailable",
                )
                for index, event in enumerate(events, 1)
            ]
        if self._circuit_open:
            self.diagnostics["content_llm_circuit_open_count"] += 1
            return [
                self._fallback_result(
                    event,
                    index,
                    current_attempts[event.event_key],
                    failure_reason="content_llm_unavailable",
                )
                for index, event in enumerate(events, 1)
            ]

        payload = {
            "events": [
                {
                    "index": index,
                    "event_key": event.event_key,
                    "source_title": event.canonical_evidence.source_title,
                    "evidence_text": event.canonical_evidence.evidence_text,
                    "source_quotes": [
                        {"quote_id": quote_id, "text": quote}
                        for quote_id, quote in _source_quotes(
                            event.canonical_evidence.evidence_text
                        )
                    ],
                    "source_url": event.canonical_evidence.url,
                    "publisher": event.canonical_evidence.publisher_name,
                    "channel": event.canonical_evidence.channel,
                    "is_official": event.canonical_evidence.is_official,
                    "content_type": event.canonical_evidence.content_type,
                    "opinion_author": event.canonical_evidence.opinion_author,
                    "stance_type": event.canonical_evidence.stance_type,
                    "rebuild_reasons": list(rebuild_reasons.get(event.event_key, ())),
                    "protected_anchors": _protected_anchors(event),
                }
                for index, event in enumerate(events, 1)
            ]
        }
        request_failure_reason: str | None = None
        try:
            response = self._client_instance().chat.completions.create(
                model=self.llm_config.model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "你是 AI 圈新闻与观点编辑。只根据每条 canonical source 证据生成中文标题和"
                            "零至两句事实摘要，不写评论、趋势、影响分析或输入外事实。摘要必须提取"
                            "标题之外的原始证据；没有可安全提取的标题外事实时，允许 brief 为空字符串。"
                            "为标题和摘要中的每个完整展示目标返回 target/source_quote_id；target 只能是 "
                            "title、brief_1 或 brief_2，同一 target 可返回多条记录；source_quote_id 必须逐字选择"
                            "该事件 source_quotes 中存在的 quote_id，不得返回、改写或拼接原文 quote，也不得返回 URL；"
                            "跨语言目标的引用必须包含该目标中的产品、模型或机构名称作为核验锚点；"
                            "跨语言标题只能翻译动作和语法词；非实体、非数字细节必须删去或保留原文锚点，"
                            "标题或摘要使用 protected_anchors 中的 @handle、模型/产品名称和数字时，"
                            "必须原样保留，不得翻译、改写或补造；重建时逐项修正 rebuild_reasons，"
                            "content_type=attributed_opinion 时必须保留 opinion_author 的明确归因，"
                            "只能压缩作者原意，不得改写成无主语的客观事实或机构公告；"
                            "content_type=ai_update 时只能概括原始项目、模型、实验或榜单的具体进展，"
                            "不得改写成正式发布，不得改写成确定性行业结论；"
                            "url 必须等于该条 source_url。严格返回 JSON 对象 {\"items\":[...]}，每条必须"
                            "包含且只包含 index、event_key、chinese_title、brief、evidence_targets；brief 必须是字符串。"
                        ),
                    },
                    {
                        "role": "user",
                        "content": json.dumps(payload, ensure_ascii=False),
                    },
                ],
                temperature=0.1,
                max_tokens=5000,
                response_format={"type": "json_object"},
            )
            decoded = json.loads(_response_content(response))
            self._consecutive_timeouts = 0
            raw_items = decoded.get("items") if isinstance(decoded, dict) else None
            if not isinstance(raw_items, list):
                self.diagnostics["content_llm_invalid_response_count"] += 1
                raw_items = []
                request_failure_reason = "invalid_builder_response"
            else:
                self.diagnostics["content_llm_success_count"] += 1
        except Exception as exc:
            if _is_nonrecoverable(exc):
                logger.error("Content LLM circuit opened: %s", exc)
                self._circuit_open = True
                self.diagnostics["content_llm_unavailable_count"] += 1
                return [
                    self._fallback_result(
                        event,
                        index,
                        current_attempts[event.event_key],
                        failure_reason="content_llm_unavailable",
                    )
                    for index, event in enumerate(events, 1)
                ]
            if _is_timeout(exc):
                self._consecutive_timeouts += 1
                if (
                    self._consecutive_timeouts
                    >= _CONSECUTIVE_TIMEOUT_CIRCUIT_THRESHOLD
                ):
                    self._circuit_open = True
                self.diagnostics["content_llm_timeout_count"] += 1
                request_failure_reason = "content_llm_timeout"
            elif isinstance(exc, (json.JSONDecodeError, ValueError, TypeError)):
                self.diagnostics["content_llm_invalid_response_count"] += 1
                request_failure_reason = "invalid_builder_response"
            else:
                self.diagnostics["content_llm_unavailable_count"] += 1
                request_failure_reason = "content_llm_unavailable"
            logger.warning("Brief builder request failed: %s", exc)
            raw_items = []

        by_index: dict[int, list[dict]] = {}
        index_by_event_key = {
            event.event_key: index for index, event in enumerate(events, 1)
        }
        unmappable_item_present = False
        for raw in raw_items:
            if not isinstance(raw, dict):
                unmappable_item_present = True
                continue
            index = raw.get("index")
            if (
                not isinstance(index, bool)
                and isinstance(index, int)
                and 1 <= index <= len(events)
            ):
                by_index.setdefault(index, []).append(raw)
                continue
            raw_event_key = raw.get("event_key")
            event_index = (
                index_by_event_key.get(raw_event_key)
                if isinstance(raw_event_key, str)
                else None
            )
            if event_index is not None:
                by_index.setdefault(event_index, []).append(raw)
            else:
                unmappable_item_present = True

        results: list[BuildResult] = []
        for index, event in enumerate(events, 1):
            attempt = current_attempts[event.event_key]
            candidates = by_index.get(index, [])
            draft = (
                _strict_item(candidates[0], event=event, input_index=index)
                if len(candidates) == 1
                else None
            )
            fallback_reason = next(
                (
                    reason
                    for reason in rebuild_reasons.get(event.event_key, ())
                    if reason in {"title_claim_not_source_bound", "title_missing_event_action"}
                ),
                None,
            )
            safe_fallback = (
                _source_fallback(
                    event,
                    index,
                    allow_anchored_english_title=True,
                )
                if attempt >= 2
                and fallback_reason is not None
                else None
            )
            if safe_fallback is not None:
                results.append(
                    BuildResult(
                        event.event_key,
                        attempt,
                        safe_fallback,
                        fallback_reason,
                        self._circuit_open,
                        True,
                    )
                )
                continue
            untranslated = bool(
                draft is not None
                and (
                    not _contains_chinese(draft.chinese_title)
                    or not all(
                        _contains_chinese(sentence)
                        for sentence in summary_sentences(draft.brief)
                    )
                )
            )
            if untranslated:
                draft = None
            if draft is not None:
                results.append(
                    BuildResult(event.event_key, attempt, draft, None, self._circuit_open)
                )
            else:
                if request_failure_reason is not None:
                    reason_code = request_failure_reason
                elif untranslated:
                    reason_code = "translation_failed"
                elif not candidates:
                    reason_code = (
                        "builder_item_malformed"
                        if len(events) == 1 and unmappable_item_present
                        else "builder_item_missing"
                    )
                elif len(candidates) > 1:
                    reason_code = "builder_item_duplicate"
                else:
                    reason_code = "builder_item_malformed"
                if attempt >= 2:
                    results.append(
                        self._fallback_result(
                            event,
                            index,
                            attempt,
                            failure_reason=reason_code,
                        )
                    )
                else:
                    results.append(
                        BuildResult(
                            event.event_key,
                            attempt,
                            None,
                            reason_code,
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
                max_retries=0,
            )
        return self._client

    def _fallback_result(
        self,
        event: MergedEvent,
        input_index: int,
        attempt: int,
        *,
        failure_reason: str = "translation_failed",
    ) -> BuildResult:
        draft = _source_fallback(event, input_index)
        if draft is not None:
            return BuildResult(
                event.event_key,
                attempt,
                draft,
                failure_reason,
                self._circuit_open,
                True,
            )
        return BuildResult(
            event.event_key,
            attempt,
            None,
            failure_reason,
            self._circuit_open,
        )

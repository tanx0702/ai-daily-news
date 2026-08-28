"""Deterministic eligibility checks for concrete X AI updates."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Mapping

from src.briefing.opinion import x_content_rejection_reason


_TECHNICAL_OBJECT = re.compile(
    r"\b(?:benchmark|leaderboard|evaluation|experiment|framework|training|"
    r"inference|gguf|quant)\b|基准测试|排行榜|评测|实验|框架|训练|推理|量化",
    re.IGNORECASE,
)
_MODEL_VERSION = re.compile(
    r"\b(?:gpt|claude|gemini|llama|qwen|deepseek|mistral)[\w.+-]*\d[\w.+-]*\b|"
    r"\b(?:model|模型)\s*v?\d+(?:\.\d+)*\b",
    re.IGNORECASE,
)
_MECHANICAL_PROGRESS = re.compile(
    r"\b\d+(?:\.\d+)?\s*%|#\s*\d+\b|\b(?:rank|排名)\s*#?\s*\d+|"
    r"\b\d+(?:\.\d+)?\s*(?:x|ms|s|tok/s|tokens/s)\b|"
    r"\b(?:speed|latency|速度|延迟)\s*\d+|第\s*\d+\s*名",
    re.IGNORECASE,
)
_BENCHMARK_RESULT = re.compile(
    r"\b(?:benchmark|leaderboard|evaluation)\b|基准测试|排行榜|评测",
    re.IGNORECASE,
)
_RESULT_RELATION = re.compile(
    r"\b(?:scores?|reaches?|rank(?:s|ed)?|places?|improves?|improved|"
    r"increases?|increased|decreases?|decreased|higher|lower|faster|slower|"
    r"outperforms?|beats?|achieves?|achieved)\b|达到|提升|降低|加快|减少|超过",
    re.IGNORECASE,
)
_NAMED_UPDATE_SUBJECT = re.compile(
    r"\b(?:gpt|chatgpt|claude(?:\s+code)?|gemini|llama|qwen|deepseek|mistral|"
    r"h\d|model\s*v?\d)[\w.+/-]*(?:\s+[A-Z][\w.+/-]*)?\b|"
    r"\b[A-Za-z][A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\b",
    re.IGNORECASE,
)
_CONCRETE_BEHAVIOR = re.compile(
    r"\b(?:demonstrates?|shows?|tests?|tested|supports?|enables?|allows?|"
    r"generates?|creates?|runs?|executes?|handles?|processes?)\b|"
    r"展示|演示|测试|支持|允许|生成|创建|运行|执行|处理",
    re.IGNORECASE,
)
_CONCRETE_CAPABILITY = re.compile(
    r"\b(?:videos?|images?|audio|code|agents?|workflows?|browsers?|"
    r"documents?|files?)\b|视频|图像|图片|音频|代码|智能体|工作流|浏览器|文档|文件",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class UpdateEligibility:
    eligible: bool
    reason_codes: tuple[str, ...]


def evaluate_ai_update_candidate(candidate: Mapping[str, object]) -> UpdateEligibility:
    """Accept only substantive posts with a concrete technical anchor."""
    text = " ".join(str(candidate.get("summary") or candidate.get("title") or "").split())
    rejection_candidate = dict(candidate)
    rejection_candidate["summary"] = text
    if x_content_rejection_reason(rejection_candidate):
        return UpdateEligibility(False, ("update_promotional_or_repost",))
    visible = re.sub(r"https?://\S+", "", text).strip()
    if len(re.findall(r"[A-Za-z0-9]+|[\u4e00-\u9fff]", visible)) < 8:
        return UpdateEligibility(False, ("update_no_substantive_detail",))
    if not _has_update_anchor(visible):
        return UpdateEligibility(False, ("update_missing_concrete_anchor",))
    return UpdateEligibility(True, ())


def _has_update_anchor(text: str) -> bool:
    result_relation = bool(_RESULT_RELATION.search(text))
    model_version = bool(_MODEL_VERSION.search(text))
    technical_object = bool(_TECHNICAL_OBJECT.search(text))
    mechanical_progress = bool(_MECHANICAL_PROGRESS.search(text))
    benchmark_result = bool(_BENCHMARK_RESULT.search(text))
    numeric_result = result_relation and (
        (model_version and (mechanical_progress or benchmark_result))
        or (technical_object and mechanical_progress)
    )
    capability_update = bool(
        _NAMED_UPDATE_SUBJECT.search(text)
        and _CONCRETE_BEHAVIOR.search(text)
        and _CONCRETE_CAPABILITY.search(text)
    )
    return numeric_result or capability_update

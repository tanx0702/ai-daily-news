"""Deterministic editorial sufficiency and compositional fact binding."""

from __future__ import annotations

from dataclasses import dataclass
from html import unescape
import re
import unicodedata

from src.briefing.models import SourceEvidence


EVENT_ACTION_MARKERS = {
    "release": (
        "发布", "推出", "介绍", "上线", "公开", "开放使用", "release", "released", "releases",
        "launch", "launched", "launches", "available", "receiving access", "introduce",
        "introduced", "introduces", "introducing", "roll out", "rollout",
    ),
    "update": (
        "更新", "升级", "新增", "下线", "update", "updated", "updates",
        "upgrade", "upgraded", "upgrades", "add", "added", "adds",
        "deprecate", "deprecated", "暂停", "追踪", "pause", "paused", "pauses",
        "tracks",
    ),
    "result": (
        "达到", "提升", "降低", "减少", "超过", "增长", "achieve", "achieved", "improve",
        "improved", "reduce", "reduced", "reduces", "exceed", "exceeded",
        "jump", "jumps",
    ),
    "research": (
        "研究发现", "论文提出", "实验显示", "发表论文", "发表",
        "study finds", "paper proposes", "publishes a paper", "published a paper",
        "publishes", "published",
    ),
    "funding": (
        "完成融资", "获得融资", "获得投资", "融资完成", "raise", "raises",
        "raised", "funded",
    ),
    "acquisition": (
        "收购", "完成合并", "acquire", "acquired", "acquires", "merges with",
    ),
    "partnership": (
        "达成合作", "宣布合作", "签署合作", "partners with", "partnered with",
        "collaborates with", "collaborated with",
    ),
    "appointment": ("任命", "晋升", "appoint", "appointed", "appoints", "promoted"),
    "departure": (
        "宣布离职", "宣布辞职", "announces departure", "announced departure", "departs", "departed", "离职", "辞职", "离开", "卸任", "leaving", "leaves", "left", "takes off",
        "headed out the door", "steps down", "resigns", "resigned",
    ),
    "organizational_change": ("解散", "disband", "disbanded", "disbands"),
    "joining": ("入职", "加入", "joins", "joined", "hired"),
    "layoff": ("裁员", "layoffs", "laid off", "cuts jobs"),
    "policy": (
        "颁布禁令", "出台禁令", "发布禁令", "监管裁决", "bans", "banned",
        "prohibits", "regulated", "issues a ban",
    ),
    "infrastructure": (
        "建设", "部署", "扩建", "扩大", "builds", "built", "deploys", "deployed",
        "expands",
    ),
    "security": (
        "披露漏洞", "发现漏洞", "修复漏洞", "discloses", "disclosed",
        "discovers", "discovered", "fixes", "fixed",
    ),
    "open_source": ("开源", "open source", "open-source", "open-sources"),
}

_ORGANIZATION_ALIASES = {
    "openai": "openai",
    "anthropic": "anthropic",
    "google": "google",
    "google deepmind": "google deepmind",
    "deepmind": "google deepmind",
    "mistral": "mistral ai",
    "mistral ai": "mistral ai",
    "microsoft": "microsoft",
    "meta": "meta",
    "nvidia": "nvidia",
    "xai": "xai",
    "x.ai": "xai",
    "cohere": "cohere",
    "cerebras": "cerebras",
    "谷歌": "google",
    "微软": "microsoft",
    "英伟达": "nvidia",
}
_GENERIC_SUBJECTS = {
    "a company", "an ai company", "company", "the company", "this company",
    "team", "the team", "organization", "某公司", "一家公司", "多家公司",
    "该公司", "这家公司", "公司", "某机构", "该机构", "机构", "某团队",
    "该团队", "这个团队", "团队", "实验室", "我们", "我", "其", "它",
}
_GENERIC_DETAILS = {
    "ai", "model", "models", "product", "products", "strategy", "update",
    "模型", "产品", "战略", "计划", "进展", "动态", "消息", "更新", "研究",
    "能力", "功能", "新模型", "新产品", "全新模型", "全新产品",
}
_NON_NEWS_PATTERNS = (
    r"\bhow\b.*\bworks?\b",
    r"\bguide\b",
    r"\btutorial\b",
    r"\bmentions?\b",
    r"工作原理",
    r"使用指南",
    r"教程",
    r"提及",
    r"战略$",
    r"趋势$",
)
_METADATA_PATTERNS = (
    re.compile(r"\bPoints:\s*\d+", re.I),
    re.compile(r"#\s*Comments:\s*\d+", re.I),
    re.compile(r"\bComments:\s*\d+", re.I),
)
_NEGATION = re.compile(r"\b(?:not|never|without|would not)\b|未|没有|并未|不会")
_SOURCE_ACTION_TRANSLATIONS = {
    "release": "发布",
    "released": "发布",
    "releases": "发布",
    "launch": "发布",
    "launched": "发布",
    "launches": "发布",
    "update": "更新",
    "updated": "更新",
    "updates": "更新",
    "upgrade": "升级",
    "upgraded": "升级",
    "upgrades": "升级",
    "pause": "暂停",
    "paused": "暂停",
    "pauses": "暂停",
    "track": "追踪",
    "tracks": "追踪",
    "reduce": "减少",
    "reduced": "减少",
    "reduces": "减少",
    "improve": "提升",
    "improved": "提升",
    "exceed": "超过",
    "exceeded": "超过",
    "jump": "增长",
    "jumps": "增长",
    "disband": "解散",
    "disbanded": "解散",
    "disbands": "解散",
    "raise": "融资",
    "raises": "融资",
    "raised": "融资",
    "acquire": "收购",
    "acquired": "收购",
    "acquires": "收购",
    "appoint": "任命",
    "appointed": "任命",
    "appoints": "任命",
    "join": "加入",
    "joins": "加入",
    "joined": "加入",
    "hire": "入职",
    "hired": "入职",
}


@dataclass(frozen=True, slots=True)
class PublishabilityResult:
    accepted: bool
    reason_codes: tuple[str, ...]
    event_type: str = ""
    subject_anchors: tuple[str, ...] = ()
    title_completeness: str = "incomplete"


@dataclass(frozen=True, slots=True)
class _ClaimFrame:
    actions: frozenset[str]
    subjects: frozenset[str]
    details: frozenset[str]


def _normalize(value: str) -> str:
    return re.sub(
        r"\s+", " ", unicodedata.normalize("NFKC", unescape(value))
    ).strip()


def _contains_marker(value: str, marker: str) -> bool:
    if any("\u4e00" <= char <= "\u9fff" for char in marker):
        return marker in value
    return bool(re.search(
        rf"(?<![a-z0-9]){re.escape(marker)}(?![a-z0-9])",
        value,
        flags=re.I,
    ))


def asserted_action_types(value: str) -> frozenset[str]:
    normalized = _normalize(value).casefold()
    if _NEGATION.search(normalized):
        return frozenset()
    return frozenset(
        action
        for action, markers in EVENT_ACTION_MARKERS.items()
        if any(_contains_marker(normalized, marker) for marker in markers)
    )


def _first_action(value: str) -> tuple[int, int, str]:
    normalized = _normalize(value)
    lowered = normalized.casefold()
    matches: list[tuple[int, int, str]] = []
    for action, markers in EVENT_ACTION_MARKERS.items():
        for marker in markers:
            if any("\u4e00" <= char <= "\u9fff" for char in marker):
                index = lowered.find(marker.casefold())
                if index >= 0:
                    matches.append((index, index + len(marker), action))
            else:
                match = re.search(
                    rf"(?<![a-z0-9]){re.escape(marker)}(?![a-z0-9])",
                    lowered,
                    flags=re.I,
                )
                if match:
                    matches.append((match.start(), match.end(), action))
    return min(matches, default=(-1, -1, ""), key=lambda item: item[0])


def _organization_anchors(value: str) -> set[str]:
    lowered = _normalize(value).casefold()
    return {
        f"org:{canonical}"
        for alias, canonical in _ORGANIZATION_ALIASES.items()
        if _contains_marker(lowered, alias)
    }


def _model_anchors(value: str) -> set[str]:
    pattern = re.compile(
        r"(?<![a-z0-9])(?:chatgpt(?![a-z0-9])|(?:gpt|claude|gemini|llama|qwen|deepseek|model|mistral)"
        r"[- ]?[a-z]*\d[\w.+-]*)(?:\s+(?:flash|mini|pro|ultra|ultrafast))?",
        re.I,
    )
    return {
        "model:" + re.sub(
            r"\s+", "-", match.group(0).casefold().rstrip(".,;:!?，。；：！？")
        )
        for match in pattern.finditer(_normalize(value))
    }


def _surface_anchor_matches(value: str) -> tuple[str, ...]:
    normalized = _normalize(value)
    matches: list[tuple[int, str]] = []
    for alias in sorted(_ORGANIZATION_ALIASES, key=len, reverse=True):
        match = re.search(
            rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])",
            normalized,
            flags=re.I,
        )
        if match:
            matches.append((match.start(), match.group(0)))
    model_pattern = re.compile(
        r"(?<![a-z0-9])(?:chatgpt(?![a-z0-9])|(?:gpt|claude|gemini|llama|qwen|deepseek|model|mistral)"
        r"[- ]?[a-z]*\d[\w.+-]*)(?:\s+(?:flash|mini|pro|ultra|ultrafast))?",
        re.I,
    )
    matches.extend((match.start(), match.group(0)) for match in model_pattern.finditer(normalized))
    return tuple(value for _, value in sorted(matches, key=lambda item: item[0]))


def source_anchored_title(source: SourceEvidence) -> str | None:
    """Build a minimal cross-language title solely from known source anchors."""
    title = _normalize(source.source_title)
    if not title or any("\u4e00" <= char <= "\u9fff" for char in title):
        return None
    action_matches = [
        (match.start(), match.end(), translation)
        for marker, translation in _SOURCE_ACTION_TRANSLATIONS.items()
        for match in [
            re.search(
                rf"(?<![a-z0-9]){re.escape(marker)}(?![a-z0-9])",
                title,
                flags=re.I,
            )
        ]
        if match
    ]
    if not action_matches:
        return None
    start, end, action = min(action_matches, key=lambda item: item[0])
    subjects = _surface_anchor_matches(title[:start])
    details = _surface_anchor_matches(title[end:])
    if not subjects or not details:
        return None
    subject = subjects[0]
    detail = next(
        (anchor for anchor in details if anchor.casefold() != subject.casefold()),
        None,
    )
    return f"{subject} {action} {detail}" if detail else None


def _numeric_anchors(value: str) -> set[str]:
    return {
        "number:" + token.replace(",", "").casefold()
        for token in re.findall(r"(?:\$\s*)?\d+(?:[.,]\d+)?(?:\s*(?:%|gw|mw|亿美元|万元))?", value, re.I)
    }


def _literal_subject(value: str) -> str:
    subject = _normalize(value).strip(" ,，:：-—")
    subject = re.sub(r"^(?:在|于|截至)\s*", "", subject)
    subject = re.sub(r"\b(?:has|have|had|is|are|was|were|will)\s*$", "", subject, flags=re.I)
    if not subject or subject.casefold() in _GENERIC_SUBJECTS:
        return ""
    if re.fullmatch(r"\d+(?:[.,]\d+)?", subject):
        return ""
    return f"literal-subject:{subject.casefold()}"


def _detail_anchors(value: str) -> set[str]:
    anchors = _organization_anchors(value) | _model_anchors(value) | _numeric_anchors(value)
    residual = _normalize(value).casefold()
    for markers in EVENT_ACTION_MARKERS.values():
        for marker in markers:
            residual = re.sub(re.escape(marker.casefold()), " ", residual)
    residual = re.sub(r"[^a-z0-9\u4e00-\u9fff.+-]+", " ", residual)
    for raw_token in residual.split():
        token = raw_token.strip(".,;:!?，。；：！？")
        if token not in _GENERIC_DETAILS and len(token) >= 2:
            anchors.add(f"literal:{token}")
    return anchors


def _claim_frame(value: str) -> _ClaimFrame | None:
    normalized = _normalize(value)
    start, end, action = _first_action(normalized)
    if start < 0 or not action:
        return None
    before = normalized[:start]
    after = normalized[end:]
    subjects = _organization_anchors(before) | _model_anchors(before)
    literal = _literal_subject(before)
    if literal and not subjects:
        subjects.add(literal)
    details = _detail_anchors(after)
    return _ClaimFrame(frozenset({action}), frozenset(subjects), frozenset(details))


def _sentences(value: str) -> tuple[str, ...]:
    return tuple(
        part.strip()
        for part in re.split(r"[。！？!?;；\n]+|(?<=[a-z0-9])\.\s+", value, flags=re.I)
        if part.strip()
    )


def _publisher_subject(source: SourceEvidence) -> frozenset[str]:
    if not source.is_official or not source.official_identity_source:
        return frozenset()
    anchors = _organization_anchors(source.publisher_name)
    if anchors:
        return frozenset(anchors)
    literal = _literal_subject(source.publisher_name)
    return frozenset({literal}) if literal else frozenset()


def _frame_supported(
    display: _ClaimFrame,
    source: _ClaimFrame,
    *,
    publisher_subjects: frozenset[str] = frozenset(),
) -> bool:
    if not display.actions <= source.actions:
        return False
    available_subjects = source.subjects | publisher_subjects
    if not display.subjects or not display.subjects <= available_subjects:
        return False
    return bool(display.details and display.details <= source.details)


def claim_supported_by_quote(
    claim: str,
    quote: str,
    *,
    source: SourceEvidence | None = None,
) -> bool:
    display = _claim_frame(claim)
    if display is None:
        return False
    publisher_subjects = _publisher_subject(source) if source else frozenset()
    return any(
        frame is not None
        and _frame_supported(display, frame, publisher_subjects=publisher_subjects)
        for frame in (_claim_frame(sentence) for sentence in _sentences(quote))
    )


def validate_source_publishability(source: SourceEvidence) -> PublishabilityResult:
    evidence = _normalize(source.evidence_text)
    title = _normalize(source.source_title)
    if source.channel == "github":
        lowered = evidence.casefold()
        activity_markers = ("star", "commit", "recent push", "近期活跃")
        publication_markers = ("release", "readme", "announcement", "发布说明")
        if any(marker in lowered for marker in activity_markers) and not any(
            marker in lowered for marker in publication_markers
        ):
            return PublishabilityResult(False, ("github_activity_only",))
    if source.discovered_via == "hacker_news" and any(
        pattern.search(evidence) for pattern in _METADATA_PATTERNS
    ):
        return PublishabilityResult(False, ("metadata_only_evidence",))
    if any(re.search(pattern, title, flags=re.I) for pattern in _NON_NEWS_PATTERNS):
        return PublishabilityResult(False, ("non_news_content",))
    frame = _claim_frame(title)
    if frame is None:
        return PublishabilityResult(False, ("non_news_content",))
    if not frame.subjects:
        return PublishabilityResult(False, ("source_missing_subject",))
    if not frame.details:
        return PublishabilityResult(False, ("source_missing_event_detail",))
    return PublishabilityResult(
        True,
        (),
        next(iter(frame.actions)),
        tuple(sorted(frame.subjects)),
        "complete",
    )


def validate_display_publishability(
    title: str,
    brief: str,
    source: SourceEvidence,
) -> PublishabilityResult:
    normalized = _normalize(title)
    frame = _claim_frame(normalized)
    if frame is None:
        return PublishabilityResult(False, ("title_missing_event_action",))
    if not frame.subjects:
        return PublishabilityResult(False, ("title_missing_subject",))
    if not frame.details:
        return PublishabilityResult(False, ("title_missing_event_detail",))
    if not claim_supported_by_quote(normalized, source.evidence_text, source=source):
        source_actions = asserted_action_types(source.evidence_text)
        reason = (
            "title_action_not_source_bound"
            if not frame.actions <= source_actions
            else "title_claim_not_source_bound"
        )
        return PublishabilityResult(False, (reason,))
    return PublishabilityResult(
        True,
        (),
        next(iter(frame.actions)),
        tuple(sorted(frame.subjects)),
        "complete",
    )

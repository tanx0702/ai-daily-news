"""Deterministic editorial sufficiency and compositional fact binding."""

from __future__ import annotations

from dataclasses import dataclass
from html import unescape
import re
import unicodedata

from src.briefing.models import SourceEvidence
from src.briefing.opinion import x_content_rejection_reason


EVENT_ACTION_MARKERS = {
    "release": (
        "发布", "推出", "介绍", "上线", "公开", "开放使用", "release", "released", "releases",
        "launch", "launched", "launches", "available", "receiving access", "introduce",
        "introduced", "introduces", "introducing", "releasing", "is live",
        "goes live", "went live", "roll out", "rollout",
    ),
    "update": (
        "更新", "升级", "新增", "下线", "update", "updated", "updates",
        "upgrade", "upgraded", "upgrades", "add", "added", "adds",
        "deprecate", "deprecated", "暂停", "追踪", "跟踪", "pause", "paused", "pauses",
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
_SOURCE_LITERAL_DETAIL_STOPWORDS = {
    "a", "an", "and", "at", "before", "by", "can", "for", "from", "in", "it",
    "its", "may", "model", "models", "more", "new", "of", "on", "our", "over",
    "product", "products", "service", "services", "that", "the", "their", "this",
    "to", "with", "will", "your",
}
_UPDATE_RESULT_RELATION = re.compile(
    r"\b(?:scores?|reaches?|rank(?:s|ed)?|places?|improves?|improved|"
    r"increases?|increased|decreases?|decreased|higher|lower|faster|slower|"
    r"outperforms?|beats?|achieves?|achieved)\b|"
    r"得分|达到|提升|降低|高出|低于|加快|减少|超过|排名|位列",
    re.IGNORECASE,
)
_UPDATE_DIRECTION_PATTERNS = {
    "higher": re.compile(
        r"\b(?:improves?|improved|increases?|increased|higher|faster|"
        r"outperforms?|beats?)\b|提升|高出|加快|超过|快于",
        re.IGNORECASE,
    ),
    "lower": re.compile(
        r"\b(?:decreases?|decreased|lower|slower)\b|降低|低于|减少|慢于",
        re.IGNORECASE,
    ),
}
_UPDATE_VALUE_RELATION = re.compile(
    r"\b(?:scores?|reaches?|achieves?|achieved|rank(?:s|ed)?|places?)\b|"
    r"得分|达到|排名|位列",
    re.IGNORECASE,
)
_UPDATE_DIMENSION_PATTERNS = (
    ("score", re.compile(r"\b(?:scores?|scoring)\b|得分|分数", re.IGNORECASE)),
    ("latency", re.compile(r"\blatency\b|延迟", re.IGNORECASE)),
    (
        "speed",
        re.compile(
            r"\b(?:speed|throughput|faster|slower|tok/s|tokens/s)\b|"
            r"速度|吞吐|快于|慢于",
            re.IGNORECASE,
        ),
    ),
    (
        "rank",
        re.compile(
            r"\b(?:rank(?:s|ed)?|places?)\b|排名|位列|第\s*\d+\s*名",
            re.IGNORECASE,
        ),
    ),
)
_UPDATE_MECHANICAL_PROGRESS = re.compile(
    r"\b\d+(?:\.\d+)?\s*%|#\s*\d+\b|\b(?:rank|排名)\s*#?\s*\d+|"
    r"\b\d+(?:\.\d+)?\s*(?:x|ms|s|tok/s|tokens/s)\b|"
    r"\b(?:speed|latency|速度|延迟)\s*\d+|第\s*\d+\s*名",
    re.IGNORECASE,
)
_UPDATE_METRIC_STOPWORDS = {
    "a", "an", "and", "by", "for", "higher", "lower", "more", "on",
    "than", "the", "to", "with", "benchmark", "evaluation", "result",
}
_UPDATE_BEHAVIOR_PATTERNS = (
    (
        "demo",
        re.compile(r"\b(?:demonstrates?|shows?|tests?|tested)\b|展示|演示|测试", re.I),
    ),
    (
        "support",
        re.compile(r"\b(?:supports?|enables?|allows?)\b|支持|允许|可用于", re.I),
    ),
    ("generate", re.compile(r"\b(?:generates?|creates?)\b|生成|创建", re.I)),
    ("run", re.compile(r"\b(?:runs?|executes?)\b|运行|执行", re.I)),
    ("handle", re.compile(r"\b(?:handles?|processes?)\b|处理", re.I)),
)
_UPDATE_CAPABILITY_PATTERNS = (
    ("video", re.compile(r"\bvideos?\b|视频", re.I)),
    ("image", re.compile(r"\bimages?\b|图像|图片", re.I)),
    ("audio", re.compile(r"\baudio\b|音频", re.I)),
    ("code", re.compile(r"\bcode\b|代码", re.I)),
    ("agent", re.compile(r"\bagents?\b|智能体", re.I)),
    ("workflow", re.compile(r"\bworkflows?\b|工作流", re.I)),
    ("browser", re.compile(r"\bbrowsers?\b|浏览器", re.I)),
    ("document", re.compile(r"\b(?:documents?|files?)\b|文档|文件", re.I)),
)
_UPDATE_KNOWN_SUBJECT = re.compile(
    r"\b(?:gpt|chatgpt|claude(?:\s+code)?|gemini|llama|qwen|deepseek|mistral)"
    r"[\w.+/-]*(?:\s+[A-Z][\w.+/-]*)?\b",
    re.I,
)


@dataclass(frozen=True, slots=True)
class PublishabilityResult:
    accepted: bool
    reason_codes: tuple[str, ...]
    event_type: str = ""
    subject_anchors: tuple[str, ...] = ()
    title_completeness: str = "incomplete"
    detail_anchors: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _ClaimFrame:
    actions: frozenset[str]
    subjects: frozenset[str]
    details: frozenset[str]


@dataclass(frozen=True, slots=True)
class _UpdateClaimFrame:
    subjects: frozenset[str]
    details: frozenset[str]
    relations: frozenset[str]


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


def _literal_detail_matches(value: str) -> tuple[str, ...]:
    return tuple(
        match.group(0)
        for match in re.finditer(r"(?<![a-z0-9])[a-z][a-z0-9.+-]*(?![a-z0-9])", value, re.I)
        if len(match.group(0)) >= 3
        and match.group(0).casefold() not in _SOURCE_LITERAL_DETAIL_STOPWORDS
    )


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
    subject = subjects[0] if subjects else _literal_subject_surface(title[:start])
    if not subject:
        return None
    detail = next(
        (anchor for anchor in details if anchor.casefold() != subject.casefold()),
        None,
    )
    if detail is None:
        detail = next(iter(_literal_detail_matches(title[end:])), None)
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


def _literal_subject_surface(value: str) -> str:
    subject = _normalize(value).strip(" ,，:：-—")
    subject = re.sub(r"^(?:在|于|截至)\s*", "", subject)
    subject = re.sub(r"\b(?:has|have|had|is|are|was|were|will)\s*$", "", subject, flags=re.I)
    return subject if _literal_subject(subject) else ""


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


def _is_promotional_or_vague(title: str, evidence_text: str) -> bool:
    combined = _normalize(f"{title} {evidence_text}")
    return bool(
        x_content_rejection_reason({"summary": combined})
        or re.search(r"\b(?:interesting|trend)\b", title, flags=re.I)
        or any(re.search(pattern, title, flags=re.I) for pattern in _NON_NEWS_PATTERNS)
    )


def _first_update_relation(value: str) -> tuple[int, int, str]:
    normalized = _normalize(value)
    matches: list[tuple[int, int, str]] = []
    metric = _UPDATE_RESULT_RELATION.search(normalized)
    if metric:
        matches.append((metric.start(), metric.end(), "metric"))
    for relation, pattern in _UPDATE_BEHAVIOR_PATTERNS:
        match = pattern.search(normalized)
        if match:
            matches.append((match.start(), match.end(), f"behavior:{relation}"))
    return min(matches, default=(-1, -1, ""), key=lambda item: item[0])


def _update_subject_anchors(value: str, *, publisher_name: str = "") -> set[str]:
    normalized = _normalize(value)
    relation_start, _relation_end, _relation_type = _first_update_relation(normalized)
    subject_text = normalized[:relation_start] if relation_start >= 0 else ""
    # In comparison headlines, anchors after `vs`/`than` are comparison
    # objects, not alternate subjects for the displayed claim.
    if re.search(
        r"\b(?:vs\.?|versus|than|compared\s+with)\b|对比|相比|与",
        subject_text,
        flags=re.I,
    ):
        return set()
    if publisher_name:
        subject_text = re.sub(
            rf"^\s*{re.escape(_normalize(publisher_name))}\s*[:：]\s*",
            "",
            subject_text,
            flags=re.I,
        )
    anchors = _organization_anchors(subject_text) | _model_anchors(subject_text)
    anchors.update(
        f"entity:{re.sub(r'\s+', '-', match.group(0).casefold())}"
        for match in _UPDATE_KNOWN_SUBJECT.finditer(subject_text)
    )
    if anchors:
        return anchors
    for token in re.findall(r"(?<![A-Za-z0-9])[A-Za-z][A-Za-z0-9.+/-]*", subject_text):
        letters = "".join(char for char in token if char.isalpha())
        if (
            any(char.isdigit() for char in token)
            or (letters.isupper() and len(letters) >= 2)
            or (any(char.isupper() for char in letters[1:]) and any(char.islower() for char in letters))
        ):
            anchors.add(f"entity:{token.casefold().rstrip('.,;:!?')}")
    return anchors


def _update_named_detail_anchors(value: str) -> set[str]:
    normalized = _normalize(value)
    _relation_start, relation_end, _relation_type = _first_update_relation(normalized)
    detail_text = normalized[relation_end:] if relation_end >= 0 else ""
    anchors: set[str] = set()
    for token in re.findall(r"(?<![A-Za-z0-9])[A-Za-z][A-Za-z0-9.+/-]*", detail_text):
        stripped = token.rstrip(".,;:!?")
        letters = "".join(char for char in stripped if char.isalpha())
        if (
            "/" in stripped
            or (
                "-" in stripped
                and (
                    any(char.isdigit() for char in stripped)
                    or letters.isupper()
                )
            )
            or (letters.isupper() and len(letters) >= 3)
            or (
                any(char.isupper() for char in letters[1:])
                and any(char.islower() for char in letters)
            )
        ):
            anchors.add(f"named:{stripped.casefold()}")
    return anchors


def _update_metric_anchors(value: str) -> set[str]:
    normalized = _normalize(value)
    _relation_start, relation_end, relation_type = _first_update_relation(normalized)
    if relation_type != "metric":
        return set()
    detail_text = normalized[relation_end:]
    return {
        f"metric:{token.casefold()}"
        for token in re.findall(r"(?<![A-Za-z0-9])[A-Za-z][A-Za-z0-9-]*", detail_text)
        if len(token) >= 3 and token.casefold() not in _UPDATE_METRIC_STOPWORDS
    }


def _update_detail_anchors(value: str) -> set[str]:
    normalized = _normalize(value)
    _relation_start, _relation_end, relation_type = _first_update_relation(normalized)
    if not relation_type:
        return set()
    named = _update_named_detail_anchors(normalized)
    metrics = _update_metric_anchors(normalized)
    capabilities = {
        f"capability:{capability}"
        for capability, pattern in _UPDATE_CAPABILITY_PATTERNS
        if pattern.search(normalized)
    }
    if relation_type == "metric":
        if not (_UPDATE_MECHANICAL_PROGRESS.search(normalized) or named or metrics):
            return set()
        anchors = _numeric_anchors(normalized)
        anchors.update(named | metrics)
        return anchors
    return named | capabilities


def _update_relation_types(value: str) -> set[str]:
    normalized = _normalize(value)
    _relation_start, _relation_end, relation_type = _first_update_relation(normalized)
    if relation_type.startswith("behavior:"):
        return {relation_type}
    if relation_type != "metric":
        return set()
    dimensions = {
        dimension
        for dimension, pattern in _UPDATE_DIMENSION_PATTERNS
        if pattern.search(normalized)
    }
    # Only dimensions with an explicit deterministic vocabulary may establish
    # a relation frame. Named evaluation details remain evidence anchors, not
    # metric dimensions (for example, Div-300 is a benchmark, not a metric).
    # Do not collapse unknown metric wording into a generic result dimension:
    # that would let an accepted claim swap accuracy, quality, or another
    # unregistered metric while preserving only the number and direction.
    directions = {
        direction
        for direction, pattern in _UPDATE_DIRECTION_PATTERNS.items()
        if pattern.search(normalized)
    }
    if not directions and _UPDATE_VALUE_RELATION.search(normalized):
        directions = {"value"}
    return {
        f"{dimension}:{direction}"
        for dimension in dimensions
        for direction in directions
    }


def _update_claim_frame(
    value: str,
    *,
    source: SourceEvidence,
) -> _UpdateClaimFrame:
    return _UpdateClaimFrame(
        frozenset(
            _update_subject_anchors(value, publisher_name=source.publisher_name)
        ),
        frozenset(_update_detail_anchors(value)),
        frozenset(_update_relation_types(value)),
    )


def update_claim_supported_by_quote(
    claim: str,
    quote: str,
    *,
    source: SourceEvidence,
) -> bool:
    """Require one bound quote to contain the complete AI-update claim frame."""
    display = _update_claim_frame(claim, source=source)
    if not display.subjects or not display.details or not display.relations:
        return False
    for sentence in _sentences(quote):
        evidence = _update_claim_frame(sentence, source=source)
        # A sentence containing multiple metric/direction pairs is ambiguous
        # under the deterministic frame model. Do not allow its cartesian
        # product to authorize a mismatched display claim.
        if len(evidence.relations) != 1:
            continue
        if (
            display.subjects <= evidence.subjects
            and display.details <= evidence.details
            and display.relations <= evidence.relations
        ):
            return True
    return False


def validate_update_source_publishability(
    source: SourceEvidence,
) -> PublishabilityResult:
    """Validate a concrete AI update without requiring a hard-news action."""
    title = _normalize(source.source_title)
    if _is_promotional_or_vague(title, source.evidence_text):
        return PublishabilityResult(False, ("update_missing_concrete_detail",))
    subjects = _update_subject_anchors(title, publisher_name=source.publisher_name)
    details = _update_detail_anchors(title)
    if not subjects:
        return PublishabilityResult(False, ("update_missing_subject",))
    if not details:
        return PublishabilityResult(False, ("update_missing_concrete_detail",))
    return PublishabilityResult(
        True,
        (),
        "ai_update",
        tuple(sorted(subjects)),
        "complete",
        tuple(sorted(details)),
    )


def validate_update_display_publishability(
    title: str,
    brief: str,
    source: SourceEvidence,
) -> PublishabilityResult:
    """Validate that a displayed AI update keeps source-bound concrete anchors."""
    normalized = _normalize(title)
    if _is_promotional_or_vague(normalized, source.evidence_text):
        return PublishabilityResult(False, ("update_missing_concrete_detail",))
    subjects = _update_subject_anchors(
        normalized,
        publisher_name=source.publisher_name,
    )
    details = _update_detail_anchors(normalized)
    if not subjects:
        return PublishabilityResult(False, ("update_missing_subject",))
    if not details:
        return PublishabilityResult(False, ("update_missing_concrete_detail",))

    source_quotes = (source.source_title, *_sentences(source.evidence_text))
    for claim in (normalized, *_sentences(brief)):
        if not any(
            update_claim_supported_by_quote(claim, quote, source=source)
            for quote in source_quotes
        ):
            return PublishabilityResult(False, ("update_claim_not_source_bound",))
    return PublishabilityResult(
        True,
        (),
        "ai_update",
        tuple(sorted(subjects)),
        "complete",
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


def validate_content_source_publishability(
    source: SourceEvidence,
) -> PublishabilityResult:
    """Dispatch source sufficiency by the immutable content type."""
    if source.content_type == "ai_update":
        return validate_update_source_publishability(source)
    if source.content_type == "attributed_opinion":
        if not (
            source.opinion_eligible
            and source.original_post
            and source.context_complete
            and source.opinion_author.strip()
        ):
            return PublishabilityResult(False, ("opinion_author_not_allowed",))
        return PublishabilityResult(True, (), "attributed_opinion")
    return validate_source_publishability(source)


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

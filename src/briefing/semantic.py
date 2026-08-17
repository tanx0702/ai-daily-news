"""Deterministic semantic features shared by clustering and final deduplication."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
import re
import unicodedata
from typing import Literal
from urllib.parse import urlparse, urlunparse

from src.briefing.models import BriefItem, SourceEvidence
from src.briefing.publishability import asserted_action_types


Relationship = Literal["same_event", "distinct", "review"]

_AUTHORITY_ORDER = {
    "official": 0,
    "research": 1,
    "professional_media": 2,
    "community": 3,
}
_ORGANIZATION_ALIASES = {
    "openai": "openai",
    "anthropic": "anthropic",
    "google deepmind": "google deepmind",
    "deepmind": "google deepmind",
    "google": "google",
    "meta": "meta",
    "microsoft": "microsoft",
    "microsoft research": "microsoft",
    "nvidia": "nvidia",
    "hugging face": "hugging face",
    "mistral ai": "mistral ai",
    "mistral": "mistral ai",
    "deepseek": "deepseek",
    "qwen": "qwen",
    "微软": "microsoft",
    "谷歌": "google",
    "谷歌深度思维": "google deepmind",
    "英伟达": "nvidia",
    "元宇宙公司": "meta",
    "阿里通义": "qwen",
    "通义千问": "qwen",
}
_ACTION_MARKERS = {
    "release": (
        "release", "released", "releases", "launch", "launched", "launches",
        "roll out", "rollout", "available", "receiving access", "发布", "推出",
        "上线",
    ),
    "funding": ("funding", "funded", "raises", "raised", "融资", "投资", "估值"),
    "acquisition": ("acquire", "acquired", "acquisition", "merger", "收购", "合并"),
    "partnership": ("partner", "partnership", "collaborat", "合作"),
    "open_source": ("open source", "open-source", "opensource", "开源"),
    "research": ("paper", "study", "research", "论文", "研究"),
    "departure": (
        "departing", "departure", "leaving", "leaves", "left", "steps down",
        "stepping down", "resigns", "resigned", "takes off", "headed out the door",
        "离职", "辞职", "离开", "卸任",
    ),
    "appointment": ("appointed", "appointment", "named", "promoted", "任命", "晋升"),
    "joining": ("joins", "joined", "joining", "hired", "入职", "加入"),
    "layoff": ("layoff", "layoffs", "laid off", "cuts jobs", "裁员"),
    "office": ("office", "campus", "headquarters", "办公室", "总部"),
}
_PERSON_STOPWORDS = {
    "another openai",
    "openai coo",
    "openai ceo",
    "the rundown",
    "techcrunch ai",
    "the verge",
}
_KNOWN_PERSON_NAMES = {
    "alexandr wang",
    "alice johnson",
    "andrej karpathy",
    "andrew ng",
    "aravind srinivas",
    "arthur mensch",
    "bob smith",
    "brad lightcap",
    "bret taylor",
    "clement delangue",
    "daniela amodei",
    "dario amodei",
    "demis hassabis",
    "elon musk",
    "emad mostaque",
    "fidji simo",
    "geoffrey hinton",
    "greg brockman",
    "ian goodfellow",
    "ilya sutskever",
    "jeff dean",
    "jensen huang",
    "kevin weil",
    "lisa su",
    "mark zuckerberg",
    "mira murati",
    "mustafa suleyman",
    "noam shazeer",
    "sam altman",
    "sarah friar",
    "satya nadella",
    "sundar pichai",
    "thomas wolf",
    "yann lecun",
    "yoshua bengio",
}
_PERSON_ALIASES = {
    "samuel altman": "sam altman",
    "sam a. altman": "sam altman",
    "山姆·奥特曼": "sam altman",
}
_MODEL_VARIANT_SUFFIXES = (
    "deep think",
    "enterprise",
    "flash",
    "haiku",
    "maverick",
    "max",
    "mini",
    "nano",
    "opus",
    "pro",
    "scout",
    "sonnet",
    "thinking",
    "turbo",
    "ultra",
)
_JOB_TITLE_HEADS = {
    "chair",
    "chairman",
    "chairwoman",
    "counsel",
    "director",
    "engineer",
    "executive",
    "founder",
    "member",
    "officer",
    "partner",
    "president",
    "researcher",
    "scientist",
}
_JOB_TITLE_ACRONYMS = {
    "ceo",
    "cfo",
    "cio",
    "cmo",
    "coo",
    "cpo",
    "cto",
    "svp",
    "vp",
}
_ORGANIZATION_DESIGNATORS = {
    "company",
    "corporation",
    "foundation",
    "group",
    "institute",
    "lab",
    "labs",
    "research",
    "systems",
    "team",
    "technologies",
    "technology",
    "university",
}
_TEXT_STOPWORDS = {
    "a", "an", "and", "for", "from", "in", "is", "new", "of", "on", "the",
    "to", "with",
}


@dataclass(frozen=True, slots=True)
class EventFeatures:
    organizations: frozenset[str]
    people: frozenset[str]
    person_candidates: frozenset[str]
    models: frozenset[str]
    actions: frozenset[str]
    asserted_actions: frozenset[str]
    qualifiers: frozenset[str]
    text_tokens: frozenset[str]

    @property
    def strong_subjects(self) -> frozenset[str]:
        return self.people | self.models


@dataclass(frozen=True, slots=True)
class EventDocument:
    title: str
    text: str
    url: str
    published_at: str
    evidence: SourceEvidence
    features: EventFeatures
    event_key: str = ""
    editorial_score: float = 0.0

    @classmethod
    def from_evidence(
        cls,
        evidence: SourceEvidence,
        *,
        editorial_score: float = 0.0,
    ) -> "EventDocument":
        text = f"{evidence.source_title}\n{evidence.evidence_text}"
        return cls(
            title=evidence.source_title,
            text=text,
            url=evidence.url,
            published_at=evidence.published_at,
            evidence=evidence,
            features=_extract_features(text, action_text=evidence.source_title),
            editorial_score=editorial_score,
        )

    @classmethod
    def from_brief(cls, item: BriefItem) -> "EventDocument":
        source = item.canonical_source
        text = f"{source.source_title}\n{source.evidence_text}"
        return cls(
            title=source.source_title,
            text=text,
            url=source.url,
            published_at=item.published_at,
            evidence=source,
            features=_extract_features(text, action_text=source.source_title),
            event_key=item.event_key,
        )


def deterministic_relationship(
    left: EventDocument,
    right: EventDocument,
    *,
    window_hours: int,
) -> Relationship:
    if _normalized_url(left.url) and _normalized_url(left.url) == _normalized_url(right.url):
        return "same_event"
    if _x_status_id(left.url) and _x_status_id(left.url) == _x_status_id(right.url):
        return "same_event"

    time_distance = abs(_timestamp(left.published_at) - _timestamp(right.published_at))
    if time_distance > window_hours * 3600:
        return "distinct"

    left_features = left.features
    right_features = right.features
    shared_actions = left_features.actions & right_features.actions
    shared_asserted_actions = (
        left_features.asserted_actions & right_features.asserted_actions
    )
    shared_people = _shared_people(left, right)
    if left_features.actions and right_features.actions and not shared_actions:
        return "distinct"
    shared_person_candidates = _shared_person_candidates(left, right)
    if left_features.people and right_features.people and not shared_people:
        return "distinct"
    if (
        left_features.person_candidates
        and right_features.person_candidates
        and not shared_person_candidates
    ):
        return "distinct"
    if left_features.models and right_features.models and not (
        left_features.models & right_features.models
    ):
        return "distinct"
    if _conflicting_qualifiers(left_features.qualifiers, right_features.qualifiers):
        return "distinct"

    shared_organizations = left_features.organizations & right_features.organizations
    if (
        left_features.organizations
        and right_features.organizations
        and not shared_organizations
    ):
        return "distinct"

    shared_strong = shared_strong_subjects(left, right)
    if (
        _same_x_thread(left, right)
        and shared_strong
        and (left_features.asserted_actions or right_features.asserted_actions)
    ):
        # A reaction often omits the release verb. Keep it out of automatic merging,
        # but let the bounded reviewer/degradation path remove the weaker duplicate.
        return "review"
    if shared_strong and shared_asserted_actions:
        return "same_event"

    if (
        _normalized_title(left.title) == _normalized_title(right.title)
        and not shared_person_candidates
        and shared_asserted_actions
    ):
        return "same_event"

    similarity = _text_similarity(left, right)
    if similarity >= 0.82 and shared_strong and shared_asserted_actions:
        return "same_event"
    if shared_actions and (shared_organizations or shared_person_candidates):
        return "review"
    return "distinct"


def evidence_priority(
    evidence: SourceEvidence,
    editorial_score: float = 0.0,
) -> tuple[object, ...]:
    return (
        _AUTHORITY_ORDER.get(evidence.authority, 9),
        0 if evidence.is_official else 1,
        0 if evidence.channel != "x" else 1,
        -len(evidence.evidence_text),
        -float(editorial_score),
        -_timestamp(evidence.published_at),
        evidence.url,
    )


def shared_strong_subjects(
    left: EventDocument,
    right: EventDocument,
) -> frozenset[str]:
    return _shared_people(left, right) | (
        left.features.models & right.features.models
    )


def _extract_features(value: str, *, action_text: str | None = None) -> EventFeatures:
    normalized = _normalize(value)
    lowered = normalized.casefold()
    organizations = frozenset(
        canonical
        for alias, canonical in _ORGANIZATION_ALIASES.items()
        if _contains_alias(lowered, alias)
    )
    normalized_evidence = unicodedata.normalize("NFKC", value)
    people, person_candidates = _extract_people(normalized_evidence)
    actions = frozenset(
        action
        for action, markers in _ACTION_MARKERS.items()
        if any(marker in lowered for marker in markers)
    )
    models = _extract_models(normalized)
    qualifiers = _extract_qualifiers(lowered)
    tokens = frozenset(
        token
        for token in re.findall(r"[a-z0-9][a-z0-9.+-]*|[\u4e00-\u9fff]{2,}", lowered)
        if token not in _TEXT_STOPWORDS
    )
    return EventFeatures(
        organizations,
        people,
        person_candidates,
        models,
        actions,
        asserted_action_types(action_text if action_text is not None else value),
        qualifiers,
        tokens,
    )


def _extract_models(value: str) -> frozenset[str]:
    model_text = re.sub(r"[\u2010-\u2015\u2212]", "-", value)
    suffixes = "|".join(
        re.escape(suffix)
        for suffix in sorted(_MODEL_VARIANT_SUFFIXES, key=len, reverse=True)
    )
    pattern = re.compile(
        rf"\b(?P<base>(?:gpt|claude|gemini|llama|qwen|deepseek|model)"
        rf"[- ]?[a-z]*\d[\w.+-]*)"
        rf"(?:\s*(?:\(\s*)?(?P<suffix>{suffixes})\b(?:\s*\))?)?",
        flags=re.IGNORECASE,
    )
    models: set[str] = set()
    for match in pattern.finditer(model_text):
        base = re.sub(r"[\s-]+", "-", match.group("base").casefold())
        suffix = match.group("suffix")
        if suffix:
            base = f"{base}-{re.sub(r'\s+', '-', suffix.casefold())}"
        models.add(base)
    return frozenset(models)


def _extract_people(value: str) -> tuple[frozenset[str], frozenset[str]]:
    people: set[str] = set()
    candidates: set[str] = set()
    lowered = _normalize(value).casefold()
    for alias, canonical in _PERSON_ALIASES.items():
        if _contains_alias(lowered, alias):
            people.add(canonical)
            candidates.add(canonical)
    known_organizations = set(_ORGANIZATION_ALIASES) | set(
        _ORGANIZATION_ALIASES.values()
    )
    pattern = r"\b[A-Z][a-z]+(?:[ \t]+[A-Z][a-z]+)+\b"
    for line in value.splitlines():
        for match in re.findall(pattern, line):
            display_candidate = match
            words = [word.casefold() for word in match.split()]
            title_indexes = [
                index for index, word in enumerate(words) if word in _JOB_TITLE_HEADS
            ]
            has_title_prefix = bool(title_indexes)
            if has_title_prefix:
                words = words[title_indexes[-1] + 1:]
            if len(words) < 2 or any(
                word in _ORGANIZATION_DESIGNATORS for word in words
            ):
                continue
            candidate = " ".join(words)
            if candidate in _PERSON_STOPWORDS or candidate in known_organizations:
                continue
            has_context = has_title_prefix or _has_person_context(
                candidate,
                display_candidate,
                line,
            )
            if not has_context:
                continue
            candidates.add(candidate)
            if candidate in _KNOWN_PERSON_NAMES:
                people.add(candidate)
    return frozenset(people), frozenset(candidates)


def _has_person_context(candidate: str, display_candidate: str, value: str) -> bool:
    escaped = re.escape(display_candidate)
    title_heads = "|".join(sorted(_JOB_TITLE_HEADS))
    title_acronyms = "|".join(sorted(_JOB_TITLE_ACRONYMS))
    title = rf"(?:{title_acronyms}|(?:[A-Z][a-z]+[ \t]+){{0,4}}(?:{title_heads}))"
    explicit_title = rf"\b{title}[ \t]+{escaped}\b"
    title_appositive = rf"\b{re.escape(candidate)},[^.]{{0,80}}\b{title}\b"
    personal_pronoun = (
        rf"\b{re.escape(candidate)}\b[^.]{{0,80}}\b(?:he|her|hers|him|his|she)\b"
    )
    explicit_role = (
        rf"\b{re.escape(candidate)}\b[^.]{{0,80}}\bas\s+(?:an?|the)\s+"
        rf"(?:{title_heads})\b"
    )
    return bool(
        re.search(explicit_title, value, flags=re.IGNORECASE)
        or re.search(title_appositive, value, flags=re.IGNORECASE)
        or re.search(personal_pronoun, value, flags=re.IGNORECASE)
        or re.search(explicit_role, value, flags=re.IGNORECASE)
    )


def _shared_people(
    left: EventDocument,
    right: EventDocument,
) -> frozenset[str]:
    shared = left.features.people & right.features.people
    shared |= frozenset(
        person
        for person in left.features.people
        if _contains_phrase(right.text, person)
    )
    shared |= frozenset(
        person
        for person in right.features.people
        if _contains_phrase(left.text, person)
    )
    return shared


def _shared_person_candidates(
    left: EventDocument,
    right: EventDocument,
) -> frozenset[str]:
    shared = left.features.person_candidates & right.features.person_candidates
    shared |= frozenset(
        person
        for person in left.features.person_candidates
        if _contains_phrase(right.text, person)
    )
    shared |= frozenset(
        person
        for person in right.features.person_candidates
        if _contains_phrase(left.text, person)
    )
    return shared


def _contains_phrase(value: str, phrase: str) -> bool:
    normalized = _normalize(value).casefold()
    return bool(
        re.search(
            rf"(?<![a-z0-9]){re.escape(phrase.casefold())}(?![a-z0-9])",
            normalized,
        )
    )


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value)).strip()


def _normalized_title(value: str) -> str:
    return _normalize(value).casefold()


def _contains_alias(value: str, alias: str) -> bool:
    if any("\u4e00" <= character <= "\u9fff" for character in alias):
        return alias in value
    return bool(re.search(rf"(?<![\w]){re.escape(alias)}(?![\w])", value))


def _normalized_url(value: str) -> str:
    parsed = urlparse(value.strip())
    host = (parsed.hostname or "").lower()
    if not parsed.scheme or not host:
        return ""
    path = re.sub(r"/+", "/", parsed.path).rstrip("/") or "/"
    return urlunparse((parsed.scheme.lower(), host, path, "", "", ""))


def _x_status_id(value: str) -> str:
    parsed = urlparse(value)
    if (parsed.hostname or "").lower() not in {"x.com", "www.x.com"}:
        return ""
    match = re.search(r"/status/(\d+)(?:/|$)", parsed.path)
    return match.group(1) if match else ""


def _same_x_thread(left: EventDocument, right: EventDocument) -> bool:
    left_thread = left.evidence.thread_id
    right_thread = right.evidence.thread_id
    return bool(
        left.evidence.channel == "x"
        and right.evidence.channel == "x"
        and left_thread
        and left_thread == right_thread
        and left.evidence.source_item_id != right.evidence.source_item_id
    )


def _timestamp(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def _conflicting_qualifiers(left: frozenset[str], right: frozenset[str]) -> bool:
    left_by_kind = _qualifiers_by_kind(left)
    right_by_kind = _qualifiers_by_kind(right)
    return any(
        left_by_kind[kind].isdisjoint(right_by_kind[kind])
        for kind in left_by_kind.keys() & right_by_kind.keys()
    )


def _extract_qualifiers(value: str) -> frozenset[str]:
    values: set[str] = set()
    patterns = {
        "amount": r"(?:\$|usd\s*)\d+(?:[.,]\d+)?(?:\s*(?:million|billion|m|b))?",
        "percent": r"\d+(?:[.,]\d+)?%",
        "year": r"\b(?:19|20)\d{2}\b",
        "duration": r"\b\d+(?:[.,]\d+)?\s*(?:years?|months?|days?|hours?)\b",
    }
    for kind, pattern in patterns.items():
        values.update(
            f"{kind}:{match.group(0).strip()}"
            for match in re.finditer(pattern, value)
        )
    return frozenset(values)


def _qualifiers_by_kind(values: frozenset[str]) -> dict[str, set[str]]:
    grouped: dict[str, set[str]] = {}
    for value in values:
        kind, _, detail = value.partition(":")
        grouped.setdefault(kind, set()).add(detail)
    return grouped


def _text_similarity(left: EventDocument, right: EventDocument) -> float:
    left_tokens = left.features.text_tokens
    right_tokens = right.features.text_tokens
    if not left_tokens or not right_tokens:
        return 0.0
    jaccard = len(left_tokens & right_tokens) / len(left_tokens | right_tokens)
    left_value = " ".join(sorted(left_tokens))
    right_value = " ".join(sorted(right_tokens))
    return max(jaccard, SequenceMatcher(None, left_value, right_value).ratio())

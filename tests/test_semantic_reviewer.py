import json

from src.briefing.models import SourceEvidence
from src.briefing.semantic import EventDocument
from src.briefing.semantic_reviewer import SemanticDuplicateReviewer
from src.llm_config import LLMConfig


class FakeResponse:
    def __init__(self, payload):
        content = payload if isinstance(payload, str) else json.dumps(payload)
        message = type("Message", (), {"content": content})()
        self.choices = [type("Choice", (), {"message": message})()]


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        completions = type("Completions", (), {})()
        completions.create = self.create
        chat = type("Chat", (), {})()
        chat.completions = completions
        self.chat = chat

    def create(self, **kwargs):
        self.calls.append(kwargs)
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return FakeResponse(value)


def document(title: str, text: str, url: str) -> EventDocument:
    return EventDocument.from_evidence(
        SourceEvidence(
            publisher_id="media",
            publisher_name="Media",
            channel="rss",
            authority="professional_media",
            is_official=False,
            official_identity_source="",
            source_title=title,
            evidence_text=text,
            url=url,
            published_at="2026-08-11T17:00:00+00:00",
        )
    )


LEFT = document(
    "OpenAI executive takes off",
    "OpenAI COO Brad Lightcap announced his departure.",
    "https://left.example/story",
)
RIGHT = document(
    "Brad Lightcap leaves OpenAI",
    "Brad Lightcap is leaving OpenAI after eight years.",
    "https://right.example/story",
)


def reviewer(responses, *, max_calls=20, model="quality-model"):
    client = FakeClient(responses)
    instance = SemanticDuplicateReviewer(
        LLMConfig("quality-key", model, "https://quality.example/v1"),
        client_factory=lambda **_kwargs: client,
        timeout=12,
        max_calls=max_calls,
    )
    return instance, client


def response(relationship="same_event", **overrides):
    value = {
        "relationship": relationship,
        "shared_subjects": ["Brad Lightcap", "OpenAI"],
        "shared_action": "departure",
    }
    value.update(overrides)
    return value


def test_reviewer_accepts_strict_same_event_response():
    instance, client = reviewer([response()])

    result = instance.review(LEFT, RIGHT)

    assert result.relationship == "same_event"
    assert result.comparison_mode == "rules_and_llm"
    assert result.reason_code is None
    assert instance.diagnostics["semantic_llm_success_count"] == 1
    assert client.calls[0]["response_format"] == {"type": "json_object"}
    assert client.calls[0]["max_tokens"] <= 300


def test_semantic_reviewer_uses_low_reasoning_effort_for_glm_5_3_flash():
    instance, client = reviewer([response()], model="glm-5.3-flash")

    instance.review(LEFT, RIGHT)

    assert client.calls[0]["extra_body"] == {"reasoning_effort": "low"}


def test_reviewer_accepts_distinct_and_uncertain_enum_values():
    instance, _ = reviewer([response("distinct"), response("uncertain")])

    assert instance.review(LEFT, RIGHT).relationship == "distinct"
    assert instance.review(LEFT, RIGHT).relationship == "uncertain"


def test_reviewer_disables_sdk_retries_to_keep_timeout_budget_bounded():
    client = FakeClient([response()])
    captured = {}
    instance = SemanticDuplicateReviewer(
        LLMConfig("quality-key", "quality-model", "https://quality.example/v1"),
        client_factory=lambda **kwargs: (captured.update(kwargs) or client),
        timeout=12,
    )

    instance.review(LEFT, RIGHT)

    assert captured["max_retries"] == 0


def test_reviewer_accepts_distinct_without_shared_anchors():
    instance, _ = reviewer(
        [response("distinct", shared_subjects=[], shared_action="")]
    )

    result = instance.review(LEFT, RIGHT)

    assert result.relationship == "distinct"
    assert result.comparison_mode == "rules_and_llm"
    assert instance.diagnostics["semantic_llm_success_count"] == 1


def test_reviewer_rejects_extra_fields_and_unanchored_subjects():
    extra, _ = reviewer([response(fixed_title="invented")])
    unanchored, _ = reviewer(
        [response(shared_subjects=["Sam Altman", "OpenAI"])]
    )

    assert extra.review(LEFT, RIGHT).relationship == "uncertain"
    assert unanchored.review(LEFT, RIGHT).relationship == "uncertain"
    assert extra.diagnostics["semantic_llm_invalid_response_count"] == 1
    assert unanchored.diagnostics["semantic_llm_invalid_response_count"] == 1


def test_reviewer_rejects_same_event_based_only_on_broad_organization():
    instance, _ = reviewer(
        [response(shared_subjects=["OpenAI"])]
    )

    result = instance.review(LEFT, RIGHT)

    assert result.relationship == "uncertain"
    assert result.reason_code == "semantic_llm_invalid_response"
    assert instance.diagnostics["semantic_llm_invalid_response_count"] == 1


def test_reviewer_rejects_broad_organization_plus_generic_role():
    instance, _ = reviewer(
        [response(shared_subjects=["OpenAI", "executive"])]
    )

    result = instance.review(LEFT, RIGHT)

    assert result.relationship == "uncertain"
    assert result.reason_code == "semantic_llm_invalid_response"


def test_reviewer_does_not_accept_substring_only_subject_anchor():
    instance, _ = reviewer(
        [response(shared_subjects=["AI"], shared_action="departure")]
    )

    result = instance.review(LEFT, RIGHT)

    assert result.relationship == "uncertain"
    assert result.reason_code == "semantic_llm_invalid_response"


def test_reviewer_requires_an_exact_shared_strong_subject():
    instance, _ = reviewer(
        [response(shared_subjects=["Brad", "OpenAI"], shared_action="departure")]
    )

    result = instance.review(LEFT, RIGHT)

    assert result.relationship == "uncertain"
    assert result.reason_code == "semantic_llm_invalid_response"


def test_reviewer_rejects_title_or_organization_phrase_as_shared_person():
    examples = (
        "Chief Executive",
        "General Counsel",
        "Managing Partner",
        "Board Member",
        "Machine Learning Engineer",
        "Hugging Face",
        "Microsoft Research",
        "Blue Horizon",
    )

    for index, phrase in enumerate(examples):
        left = document(
            f"{phrase} leaves OpenAI",
            f"{phrase} announced a departure from OpenAI.",
            f"https://left.example/non-person-{index}",
        )
        right = document(
            f"{phrase} departure at OpenAI",
            f"OpenAI confirmed the departure of {phrase}.",
            f"https://right.example/non-person-{index}",
        )
        instance, _ = reviewer(
            [response(shared_subjects=[phrase], shared_action="departure")]
        )

        result = instance.review(left, right)

        assert result.relationship == "uncertain"
        assert result.reason_code == "semantic_llm_invalid_response"


def test_reviewer_accepts_person_after_job_title_prefix():
    left = document(
        "Chief Technology Officer Mira Murati leaves OpenAI",
        "Mira Murati announced her departure from OpenAI.",
        "https://left.example/mira",
    )
    right = document(
        "Mira Murati departure at OpenAI",
        "OpenAI confirmed that Mira Murati is leaving.",
        "https://right.example/mira",
    )
    instance, _ = reviewer(
        [response(shared_subjects=["Mira Murati"], shared_action="departure")]
    )

    result = instance.review(left, right)

    assert result.relationship == "same_event"
    assert result.reason_code is None


def test_reviewer_rejects_unknown_phrase_with_person_like_verb():
    left = document(
        "Blue Horizon is leaving the model market",
        "Blue Horizon is leaving the model market.",
        "https://left.example/blue-horizon",
    )
    right = document(
        "Blue Horizon is leaving the benchmark market",
        "Blue Horizon is leaving the benchmark market.",
        "https://right.example/blue-horizon",
    )
    instance, _ = reviewer(
        [response(shared_subjects=["Blue Horizon"], shared_action="departure")]
    )

    result = instance.review(left, right)

    assert result.relationship == "uncertain"
    assert result.reason_code == "semantic_llm_invalid_response"


def test_reviewer_rejects_unknown_phrase_in_person_like_contexts():
    examples = (
        "CEO Blue Horizon leaves OpenAI",
        "Blue Horizon, the founder, leaves OpenAI",
        "Blue Horizon said he is leaving OpenAI",
    )

    for index, text in enumerate(examples):
        left = document(
            text,
            text,
            f"https://left.example/unknown-person-{index}",
        )
        right = document(
            text.replace("leaves", "is departing"),
            text,
            f"https://right.example/unknown-person-{index}",
        )
        instance, _ = reviewer(
            [response(shared_subjects=["Blue Horizon"], shared_action="departure")]
        )

        result = instance.review(left, right)

        assert result.relationship == "uncertain"
        assert result.reason_code == "semantic_llm_invalid_response"


def test_reviewer_does_not_upgrade_unlisted_person_to_strong_subject():
    left = document(
        "Cohere CEO Aidan Gomez leaves the company",
        "Aidan Gomez announced his departure from Cohere.",
        "https://left.example/aidan-gomez",
    )
    right = document(
        "Aidan Gomez departs Cohere",
        "Cohere confirmed that Aidan Gomez is leaving.",
        "https://right.example/aidan-gomez",
    )
    instance, _ = reviewer(
        [response(shared_subjects=["Aidan Gomez"], shared_action="departure")]
    )

    result = instance.review(left, right)

    assert result.relationship == "uncertain"
    assert result.reason_code == "semantic_llm_invalid_response"


def test_reviewer_treats_empty_content_as_invalid_without_opening_circuit():
    instance, client = reviewer(["", response()])

    invalid = instance.review(LEFT, RIGHT)
    recovered = instance.review(LEFT, RIGHT)

    assert invalid.relationship == "uncertain"
    assert invalid.reason_code == "semantic_llm_invalid_response"
    assert recovered.relationship == "same_event"
    assert len(client.calls) == 2
    assert instance.diagnostics["semantic_llm_invalid_response_count"] == 1
    assert instance.diagnostics["semantic_llm_circuit_open_count"] == 0


def test_reviewer_treats_malformed_json_as_invalid_without_opening_circuit():
    instance, client = reviewer(['{"relationship":', response()])

    invalid = instance.review(LEFT, RIGHT)
    recovered = instance.review(LEFT, RIGHT)

    assert invalid.relationship == "uncertain"
    assert invalid.reason_code == "semantic_llm_invalid_response"
    assert recovered.relationship == "same_event"
    assert len(client.calls) == 2
    assert instance.diagnostics["semantic_llm_invalid_response_count"] == 1
    assert instance.diagnostics["semantic_llm_circuit_open_count"] == 0


def test_reviewer_treats_client_value_error_as_unavailable_and_opens_circuit():
    instance = SemanticDuplicateReviewer(
        LLMConfig("quality-key", "quality-model", "invalid-base-url"),
        client_factory=lambda **_kwargs: (_ for _ in ()).throw(
            ValueError("invalid base URL")
        ),
        timeout=12,
        max_calls=20,
    )

    first = instance.review(LEFT, RIGHT)
    second = instance.review(LEFT, RIGHT)

    assert first.reason_code == "semantic_llm_unavailable"
    assert second.reason_code == "semantic_llm_circuit_open"
    assert instance.diagnostics["semantic_llm_unavailable_count"] == 1
    assert instance.diagnostics["semantic_llm_invalid_response_count"] == 0


def test_reviewer_timeout_opens_circuit_without_second_call():
    instance, client = reviewer([TimeoutError("semantic timeout")])

    first = instance.review(LEFT, RIGHT)
    second = instance.review(LEFT, RIGHT)

    assert first.relationship == second.relationship == "uncertain"
    assert first.reason_code == "semantic_llm_unavailable"
    assert second.reason_code == "semantic_llm_circuit_open"
    assert len(client.calls) == 1
    assert instance.diagnostics["semantic_llm_timeout_count"] == 1
    assert instance.diagnostics["semantic_llm_circuit_open_count"] == 1


def test_reviewer_stops_after_configured_budget():
    instance, client = reviewer([response()], max_calls=1)

    assert instance.review(LEFT, RIGHT).relationship == "same_event"
    exhausted = instance.review(LEFT, RIGHT)

    assert exhausted.relationship == "uncertain"
    assert exhausted.reason_code == "semantic_dedup_budget_exhausted"
    assert len(client.calls) == 1
    assert instance.diagnostics["semantic_dedup_budget_exhausted_count"] == 1


def test_reviewer_without_credentials_degrades_without_creating_client():
    created = False

    def client_factory(**_kwargs):
        nonlocal created
        created = True

    instance = SemanticDuplicateReviewer(
        LLMConfig("", "quality-model", "https://quality.example/v1"),
        client_factory=client_factory,
        timeout=12,
        max_calls=20,
    )

    result = instance.review(LEFT, RIGHT)

    assert result.relationship == "uncertain"
    assert result.reason_code == "semantic_llm_unavailable"
    assert created is False
    assert instance.diagnostics["semantic_llm_unavailable_count"] == 1

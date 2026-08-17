import json
from pathlib import Path

from src.briefing.models import BriefItem, EvidenceBinding, SourceEvidence
from src.briefing.semantic import EventDocument, deterministic_relationship


FIXTURE_PATH = (
    Path(__file__).parent
    / "fixtures"
    / "briefing"
    / "2026-08-12-brad-lightcap-duplicates.json"
)


def evidence(**overrides) -> SourceEvidence:
    values = {
        "publisher_id": "media",
        "publisher_name": "Media",
        "channel": "rss",
        "authority": "professional_media",
        "is_official": False,
        "official_identity_source": "",
        "source_title": "OpenAI releases Model 5",
        "evidence_text": "OpenAI releases Model 5 through its API.",
        "url": "https://media.example/news",
        "published_at": "2026-08-11T17:00:00+00:00",
    }
    values.update(overrides)
    return SourceEvidence(**values)


def brad_lightcap_evidence() -> list[SourceEvidence]:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    return [SourceEvidence(**value) for value in payload]


def test_brad_lightcap_reports_share_person_and_departure_action():
    documents = [EventDocument.from_evidence(value) for value in brad_lightcap_evidence()]

    assert all("brad lightcap" in value.features.people for value in documents)
    assert all("departure" in value.features.actions for value in documents)
    assert deterministic_relationship(documents[0], documents[2], window_hours=48) == (
        "same_event"
    )


def test_same_company_different_actions_are_distinct():
    release = EventDocument.from_evidence(evidence())
    departure = EventDocument.from_evidence(
        evidence(
            source_title="Brad Lightcap leaves OpenAI",
            evidence_text="OpenAI COO Brad Lightcap announced his departure.",
            url="https://media.example/departure",
        )
    )

    assert deterministic_relationship(release, departure, window_hours=48) == "distinct"


def test_same_action_different_people_are_distinct():
    brad = EventDocument.from_evidence(
        evidence(
            source_title="Brad Lightcap leaves OpenAI",
            evidence_text="OpenAI COO Brad Lightcap announced his departure.",
            url="https://media.example/brad",
        )
    )
    sam = EventDocument.from_evidence(
        evidence(
            source_title="Sam Altman leaves OpenAI",
            evidence_text="OpenAI CEO Sam Altman announced his departure.",
            url="https://media.example/sam",
        )
    )

    assert deterministic_relationship(brad, sam, window_hours=48) == "distinct"


def test_titles_and_organizations_are_not_extracted_as_people():
    examples = (
        "Chief Executive leaves OpenAI",
        "General Counsel leaves OpenAI",
        "Managing Partner leaves OpenAI",
        "Board Member leaves OpenAI",
        "Machine Learning Engineer leaves OpenAI",
        "Hugging Face launches a dataset",
        "Microsoft Research publishes a benchmark",
        "Blue Horizon launches a model",
        "Blue Horizon is leaving the market",
    )

    for index, text in enumerate(examples):
        document = EventDocument.from_evidence(
            evidence(
                source_title=text,
                evidence_text=text,
                url=f"https://media.example/non-person-{index}",
            )
        )
        assert not document.features.people


def test_job_title_prefix_preserves_only_the_trailing_person_name():
    examples = (
        ("Chief Technology Officer Mira Murati leaves OpenAI", "mira murati"),
        ("Research Scientist Alice Johnson leaves OpenAI", "alice johnson"),
    )

    for index, (text, expected_person) in enumerate(examples):
        document = EventDocument.from_evidence(
            evidence(
                source_title=text,
                evidence_text=f"{text} and announced the departure.",
                url=f"https://media.example/titled-person-{index}",
            )
        )

        assert document.features.people == {expected_person}


def test_person_extraction_does_not_cross_title_and_evidence_boundary():
    titled = EventDocument.from_evidence(
        evidence(
            source_title="Chief Technology Officer Mira Murati",
            evidence_text="Mira Murati announced her departure from OpenAI.",
            url="https://media.example/mira-titled",
        )
    )
    bare = EventDocument.from_evidence(
        evidence(
            source_title="Mira Murati departure at OpenAI",
            evidence_text="OpenAI confirmed that Mira Murati is leaving.",
            url="https://media.example/mira-bare",
        )
    )

    assert titled.features.people == {"mira murati"}
    assert deterministic_relationship(titled, bare, window_hours=48) == "review"


def test_unknown_title_case_phrase_stays_non_person_in_person_like_contexts():
    examples = (
        "CEO Blue Horizon leaves OpenAI",
        "Blue Horizon, the founder, leaves OpenAI",
        "Blue Horizon said he is leaving OpenAI",
    )

    for index, text in enumerate(examples):
        document = EventDocument.from_evidence(
            evidence(
                source_title=text,
                evidence_text=text,
                url=f"https://media.example/unknown-person-{index}",
            )
        )

        assert not document.features.people
        assert document.features.person_candidates == {"blue horizon"}


def test_unknown_person_like_phrase_cannot_bypass_review_via_identical_title():
    examples = (
        "CEO Blue Horizon leaves OpenAI",
        "Blue Horizon, the founder, leaves OpenAI",
        "Blue Horizon said he is leaving OpenAI",
    )

    for index, text in enumerate(examples):
        left = EventDocument.from_evidence(
            evidence(
                source_title=text,
                evidence_text=text,
                url=f"https://media.example/unknown-left-{index}",
            )
        )
        right = EventDocument.from_evidence(
            evidence(
                source_title=text,
                evidence_text=text,
                url=f"https://media.example/unknown-right-{index}",
            )
        )

        assert deterministic_relationship(left, right, window_hours=48) == "review"


def test_unlisted_person_with_independent_context_enters_review():
    examples = (
        (
            "Cohere CEO Aidan Gomez leaves the company",
            "Aidan Gomez announced his departure from Cohere.",
            "Aidan Gomez departs Cohere",
            "Cohere confirmed that Aidan Gomez is leaving.",
            "aidan gomez",
        ),
        (
            "Scale AI CEO Jason Droege leaves the company",
            "Jason Droege announced his departure from Scale AI.",
            "Jason Droege departs Scale AI",
            "Scale AI confirmed that Jason Droege is leaving.",
            "jason droege",
        ),
        (
            "Thinking Machines Lab Barret Zoph leaves",
            "Barret Zoph announced his departure from Thinking Machines Lab.",
            "Barret Zoph departs Thinking Machines Lab",
            "Thinking Machines Lab confirmed that Barret Zoph is leaving.",
            "barret zoph",
        ),
    )

    for index, (left_title, left_text, right_title, right_text, person) in enumerate(
        examples
    ):
        left = EventDocument.from_evidence(
            evidence(
                source_title=left_title,
                evidence_text=left_text,
                url=f"https://media.example/unlisted-left-{index}",
            )
        )
        right = EventDocument.from_evidence(
            evidence(
                source_title=right_title,
                evidence_text=right_text,
                url=f"https://media.example/unlisted-right-{index}",
            )
        )

        assert not left.features.people
        assert left.features.person_candidates == {person}
        assert not right.features.person_candidates
        assert deterministic_relationship(left, right, window_hours=48) == "review"


def test_different_unlisted_people_are_distinct():
    aidan = EventDocument.from_evidence(
        evidence(
            source_title="Cohere CEO Aidan Gomez leaves OpenAI",
            evidence_text="Aidan Gomez announced his departure from OpenAI.",
            url="https://media.example/aidan",
        )
    )
    nick = EventDocument.from_evidence(
        evidence(
            source_title="Cohere CEO Nick Frosst leaves OpenAI",
            evidence_text="Nick Frosst announced his departure from OpenAI.",
            url="https://media.example/nick",
        )
    )

    assert aidan.features.person_candidates == {"aidan gomez"}
    assert nick.features.person_candidates == {"nick frosst"}
    assert deterministic_relationship(aidan, nick, window_hours=48) == "distinct"


def test_model_names_normalize_space_and_hyphen_variants():
    examples = (
        ("GPT-5", "GPT 5"),
        ("Llama-4", "Llama 4"),
        ("Claude-4", "Claude 4"),
    )

    for index, (hyphenated, spaced) in enumerate(examples):
        first = EventDocument.from_evidence(
            evidence(
                source_title=f"OpenAI releases {hyphenated}",
                evidence_text=f"OpenAI released {hyphenated} through its API.",
                url=f"https://media.example/model-hyphen-{index}",
            )
        )
        second = EventDocument.from_evidence(
            evidence(
                source_title=f"OpenAI releases {spaced}",
                evidence_text=f"OpenAI released {spaced} through its API.",
                url=f"https://media.example/model-space-{index}",
            )
        )

        assert first.features.models == second.features.models
        assert deterministic_relationship(first, second, window_hours=48) == (
            "same_event"
        )


def test_model_variant_suffixes_remain_distinct():
    examples = (
        ("GPT-5", "GPT-5 mini"),
        ("Claude-4 Opus", "Claude-4 Sonnet"),
        ("Llama 4 Maverick", "Llama 4 Scout"),
        ("Claude-4.5 (Opus)", "Claude-4.5 (Sonnet)"),
        ("Gemini 3 Deep Think", "Gemini 3 Enterprise"),
    )

    for index, (left_name, right_name) in enumerate(examples):
        left = EventDocument.from_evidence(
            evidence(
                source_title=f"OpenAI releases {left_name}",
                evidence_text=f"OpenAI released {left_name} through its API.",
                url=f"https://media.example/model-variant-left-{index}",
            )
        )
        right = EventDocument.from_evidence(
            evidence(
                source_title=f"OpenAI releases {right_name}",
                evidence_text=f"OpenAI released {right_name} through its API.",
                url=f"https://media.example/model-variant-right-{index}",
            )
        )

        assert left.features.models != right.features.models
        assert deterministic_relationship(left, right, window_hours=48) == "distinct"


def test_model_names_normalize_unicode_hyphens():
    for index, unicode_hyphen in enumerate(("\u2011", "\u2013", "\u2014")):
        ascii_name = "GPT-5"
        unicode_name = f"GPT{unicode_hyphen}5"
        left = EventDocument.from_evidence(
            evidence(
                source_title=f"OpenAI releases {ascii_name}",
                evidence_text=f"OpenAI released {ascii_name} through its API.",
                url=f"https://media.example/model-ascii-{index}",
            )
        )
        right = EventDocument.from_evidence(
            evidence(
                source_title=f"OpenAI releases {unicode_name}",
                evidence_text=f"OpenAI released {unicode_name} through its API.",
                url=f"https://media.example/model-unicode-{index}",
            )
        )

        assert left.features.models == right.features.models
        assert deterministic_relationship(left, right, window_hours=48) == (
            "same_event"
        )


def test_explicit_person_aliases_share_a_canonical_identity():
    aliases = ("Samuel Altman", "Sam A. Altman", "山姆·奥特曼")
    canonical = EventDocument.from_evidence(
        evidence(
            source_title="OpenAI CEO Sam Altman leaves OpenAI",
            evidence_text="Sam Altman announced his departure from OpenAI.",
            url="https://media.example/sam-canonical",
        )
    )

    for index, alias in enumerate(aliases):
        alternate = EventDocument.from_evidence(
            evidence(
                source_title=f"OpenAI CEO {alias} leaves OpenAI",
                evidence_text=f"{alias} announced a departure from OpenAI.",
                url=f"https://media.example/sam-alias-{index}",
            )
        )

        assert alternate.features.people == {"sam altman"}
        assert deterministic_relationship(canonical, alternate, window_hours=48) == (
            "same_event"
        )


def test_same_title_organization_and_action_with_different_people_are_distinct():
    mira = EventDocument.from_evidence(
        evidence(
            source_title="Chief Technology Officer Mira Murati leaves OpenAI",
            evidence_text="Mira Murati announced her departure from OpenAI.",
            url="https://media.example/mira",
        )
    )
    bob = EventDocument.from_evidence(
        evidence(
            source_title="Chief Technology Officer Bob Smith leaves OpenAI",
            evidence_text="Bob Smith announced his departure from OpenAI.",
            url="https://media.example/bob",
        )
    )

    assert deterministic_relationship(mira, bob, window_hours=48) == "distinct"


def test_confirmed_person_can_anchor_a_bare_name_in_another_source():
    titled = EventDocument.from_evidence(
        evidence(
            source_title="OpenAI COO Brad Lightcap announces departure",
            evidence_text="OpenAI COO Brad Lightcap announced his departure.",
            url="https://media.example/brad-titled",
        )
    )
    bare = EventDocument.from_evidence(
        evidence(
            source_title="Brad Lightcap is leaving OpenAI",
            evidence_text="Brad Lightcap is leaving OpenAI after eight years.",
            url="https://media.example/brad-bare",
        )
    )

    assert titled.features.people == {"brad lightcap"}
    assert not bare.features.people
    assert deterministic_relationship(titled, bare, window_hours=48) == "same_event"


def test_shared_title_or_organization_phrase_does_not_prove_same_event():
    examples = (
        (
            "Chief Executive leaves OpenAI",
            "Chief Executive leaves OpenAI after a restructuring",
        ),
        (
            "Hugging Face launches a dataset",
            "Hugging Face launches a developer tool",
        ),
        (
            "Microsoft Research publishes a model",
            "Microsoft Research publishes a benchmark",
        ),
        (
            "Blue Horizon launches a model",
            "Blue Horizon launches a benchmark",
        ),
        (
            "Blue Horizon is leaving the model market",
            "Blue Horizon is leaving the benchmark market",
        ),
    )

    for index, (left_text, right_text) in enumerate(examples):
        left = EventDocument.from_evidence(
            evidence(
                source_title=left_text,
                evidence_text=left_text,
                url=f"https://media.example/phrase-left-{index}",
            )
        )
        right = EventDocument.from_evidence(
            evidence(
                source_title=right_text,
                evidence_text=right_text,
                url=f"https://media.example/phrase-right-{index}",
            )
        )

        assert deterministic_relationship(left, right, window_hours=48) != "same_event"


def test_shared_organization_and_action_without_strong_subject_needs_review():
    first = EventDocument.from_evidence(
        evidence(
            source_title="OpenAI executive takes off",
            evidence_text="An OpenAI executive announced a departure.",
            url="https://media.example/first",
        )
    )
    second = EventDocument.from_evidence(
        evidence(
            source_title="OpenAI leader leaves company",
            evidence_text="An OpenAI leader is leaving the company.",
            url="https://media.example/second",
        )
    )

    assert deterministic_relationship(first, second, window_hours=48) == "review"


def test_nominal_topic_cannot_auto_merge_a_distinct_model_paper():
    topic = EventDocument.from_evidence(
        evidence(
            source_title="OpenAI GPT-5.6 research strategy",
            evidence_text="OpenAI GPT-5.6 research strategy.",
            authority="official",
            is_official=True,
            url="https://openai.example/research-strategy",
        )
    )
    paper = EventDocument.from_evidence(
        evidence(
            source_title="OpenAI publishes a GPT-5.6 safety paper",
            evidence_text="OpenAI publishes a GPT-5.6 safety paper.",
            url="https://media.example/gpt-safety-paper",
        )
    )

    assert "research" in topic.features.actions
    assert not topic.features.asserted_actions
    assert "research" in paper.features.asserted_actions
    assert deterministic_relationship(topic, paper, window_hours=48) == "review"


def test_same_x_thread_reaction_with_a_shared_model_needs_review():
    release = EventDocument.from_evidence(
        evidence(
            channel="x",
            source_item_id="100",
            thread_id="100",
            source_title="OpenAI releases GPT-5.6",
            evidence_text="OpenAI releases GPT-5.6 to developers.",
            url="https://x.com/openai/status/100",
        )
    )
    reaction = EventDocument.from_evidence(
        evidence(
            channel="x",
            source_item_id="101",
            thread_id="100",
            reply_to_item_id="100",
            source_title="GPT-5.6 is extremely fast",
            evidence_text="GPT-5.6 is extremely fast.",
            url="https://x.com/openai/status/101",
        )
    )

    assert deterministic_relationship(release, reaction, window_hours=48) == "review"


def test_same_subject_outside_time_window_is_distinct():
    early, late = brad_lightcap_evidence()[:2]
    late = SourceEvidence(
        **{
            **late.to_dict(),
            "published_at": "2026-08-15T17:41:34+00:00",
        }
    )

    assert deterministic_relationship(
        EventDocument.from_evidence(early),
        EventDocument.from_evidence(late),
        window_hours=48,
    ) == "distinct"


def test_receiving_model_access_is_normalized_as_a_release_action():
    announced = EventDocument.from_evidence(
        evidence(
            source_title="OpenAI makes Model 5 available to selected API developers",
            evidence_text="OpenAI makes Model 5 available in a limited preview.",
            url="https://media.example/announced",
        )
    )
    receiving = EventDocument.from_evidence(
        evidence(
            source_title="Selected developers begin receiving access to OpenAI Model 5",
            evidence_text="Selected developers begin receiving access to OpenAI Model 5.",
            url="https://media.example/receiving",
        )
    )

    assert "release" in receiving.features.actions
    assert deterministic_relationship(announced, receiving, window_hours=48) == (
        "same_event"
    )


def test_same_person_and_action_with_conflicting_organizations_are_distinct():
    microsoft = EventDocument.from_evidence(
        evidence(
            source_title="Sam Altman joins Microsoft",
            evidence_text="Sam Altman joins Microsoft as an executive.",
            url="https://media.example/microsoft",
        )
    )
    openai = EventDocument.from_evidence(
        evidence(
            source_title="Sam Altman joins OpenAI",
            evidence_text="Sam Altman joins OpenAI as an executive.",
            url="https://media.example/openai",
        )
    )

    assert deterministic_relationship(microsoft, openai, window_hours=48) == (
        "distinct"
    )


def test_different_numeric_categories_do_not_hide_same_event():
    tenure = EventDocument.from_evidence(
        evidence(
            source_title="Brad Lightcap leaves OpenAI after 8 years",
            evidence_text="Brad Lightcap announced his departure after 8 years.",
            url="https://media.example/tenure",
        )
    )
    year = EventDocument.from_evidence(
        evidence(
            source_title="Brad Lightcap leaves OpenAI in 2026",
            evidence_text="Brad Lightcap announced his departure in 2026.",
            url="https://media.example/year",
        )
    )

    assert deterministic_relationship(tenure, year, window_hours=48) == "same_event"


def test_english_and_chinese_reports_with_exact_entity_anchor_are_same_event():
    english = EventDocument.from_evidence(
        evidence(
            source_title="Sam Altman joins Microsoft",
            evidence_text="Sam Altman joins Microsoft as an executive.",
            url="https://media.example/sam-microsoft-en",
        )
    )
    chinese = EventDocument.from_evidence(
        evidence(
            source_title="Sam Altman 加入微软",
            evidence_text="Sam Altman 宣布加入微软并担任高管。",
            url="https://media.example/sam-microsoft-zh",
        )
    )

    assert "microsoft" in chinese.features.organizations
    assert deterministic_relationship(english, chinese, window_hours=48) == (
        "same_event"
    )


def test_single_sided_cross_language_person_needs_review():
    examples = (
        (
            "Nvidia CEO Jensen Huang releases a platform",
            "Jensen Huang announced the Nvidia platform release.",
            "英伟达 CEO 黄仁勋发布一个平台",
            "黄仁勋宣布英伟达发布一个平台。",
        ),
        (
            "OpenAI CTO Mira Murati releases a platform",
            "Mira Murati announced the OpenAI platform release.",
            "OpenAI CTO 米拉·穆拉蒂发布一个平台",
            "米拉·穆拉蒂宣布 OpenAI 发布一个平台。",
        ),
    )

    for index, (left_title, left_text, right_title, right_text) in enumerate(examples):
        english = EventDocument.from_evidence(
            evidence(
                source_title=left_title,
                evidence_text=left_text,
                url=f"https://media.example/person-en-{index}",
            )
        )
        chinese = EventDocument.from_evidence(
            evidence(
                source_title=right_title,
                evidence_text=right_text,
                url=f"https://media.example/person-zh-{index}",
            )
        )

        assert english.features.people
        assert not chinese.features.people
        assert deterministic_relationship(english, chinese, window_hours=48) == (
            "review"
        )


def test_high_text_similarity_without_shared_entity_is_distinct():
    first = EventDocument.from_evidence(
        evidence(
            source_title="Source 1",
            evidence_text="Source 1 update.",
            url="https://media.example/source-1",
        )
    )
    second = EventDocument.from_evidence(
        evidence(
            source_title="Source 2",
            evidence_text="Source 2 update.",
            url="https://media.example/source-2",
        )
    )

    assert deterministic_relationship(first, second, window_hours=48) == "distinct"


def test_identical_unknown_titles_are_same_event():
    first = EventDocument.from_evidence(
        evidence(
            source_title="Project Aurora update",
            evidence_text="Project Aurora update is now available.",
            url="https://media.example/aurora-1",
        )
    )
    second = EventDocument.from_evidence(
        evidence(
            source_title="Project Aurora update",
            evidence_text="More details about the Project Aurora update.",
            url="https://media.example/aurora-2",
        )
    )

    assert deterministic_relationship(first, second, window_hours=48) == "same_event"


def test_generated_brief_titles_do_not_override_distinct_source_evidence():
    first_source = evidence(
        source_title="OpenAI launches Model 5",
        evidence_text="OpenAI launches Model 5 for developers.",
        url="https://media.example/model-5",
    )
    second_source = evidence(
        source_title="OpenAI opens a London office",
        evidence_text="OpenAI opens a London office for research.",
        url="https://media.example/london-office",
    )
    first = EventDocument.from_brief(
        BriefItem(
            event_key="event-model",
            chinese_title="OpenAI 发布新功能",
            brief="OpenAI 发布新功能。",
            canonical_source=first_source,
            related_sources=(),
            published_at=first_source.published_at,
            evidence_bindings=(EvidenceBinding("OpenAI 发布新功能", first_source.source_title, first_source.url),),
            content_origin="llm",
            validation_mode="rules_only",
        )
    )
    second = EventDocument.from_brief(
        BriefItem(
            event_key="event-office",
            chinese_title="OpenAI 发布新功能",
            brief="OpenAI 发布新功能。",
            canonical_source=second_source,
            related_sources=(),
            published_at=second_source.published_at,
            evidence_bindings=(EvidenceBinding("OpenAI 发布新功能", second_source.source_title, second_source.url),),
            content_origin="llm",
            validation_mode="rules_only",
        )
    )

    assert deterministic_relationship(first, second, window_hours=48) == "distinct"

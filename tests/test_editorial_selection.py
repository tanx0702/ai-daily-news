import unittest

from src.editorial_selection import assign_source_tier, select_editorial_candidates


def _item(title, source, score, tier="media", topic=""):
    return {
        "title": title,
        "source": source,
        "source_tier": tier,
        "topic_key": topic or title,
        "_score": score,
    }


class EditorialSelectionTests(unittest.TestCase):
    def test_selection_caps_a_publisher_at_two_items(self):
        items = [
            _item(f"A story {index}", "Publisher A", 100 - index)
            for index in range(4)
        ] + [
            _item("B story", "Publisher B", 90),
            _item("C story", "Publisher C", 89),
        ]

        selected, reserves, report = select_editorial_candidates(
            items,
            target_count=4,
            max_items_per_source=2,
            max_items_per_topic=2,
            min_primary_or_research=0,
        )

        self.assertEqual(len(selected), 4)
        self.assertLessEqual(
            sum(item["source"] == "Publisher A" for item in selected),
            2,
        )
        self.assertEqual(report["selected_count"], 4)
        self.assertEqual(len(reserves), 2)

    def test_selection_caps_a_topic_at_two_items(self):
        items = [
            _item(f"Agent story {index}", f"Publisher {index}", 100 - index, topic="agents")
            for index in range(3)
        ] + [
            _item("Vision story", "Vision Publisher", 80, topic="vision"),
            _item("Research story", "Research Publisher", 79, topic="research"),
        ]

        selected, _, _ = select_editorial_candidates(
            items,
            target_count=4,
            max_items_per_source=2,
            max_items_per_topic=2,
            min_primary_or_research=0,
        )

        self.assertLessEqual(
            sum(item["topic_key"] == "agents" for item in selected),
            2,
        )

    def test_selection_keeps_primary_or_research_when_available(self):
        items = [
            _item("Media one", "Media A", 100),
            _item("Media two", "Media B", 99),
            _item("Media three", "Media C", 98),
            _item("Primary one", "Official A", 80, tier="primary"),
            _item("Research one", "Lab A", 79, tier="research"),
        ]

        selected, _, report = select_editorial_candidates(
            items,
            target_count=4,
            max_items_per_source=2,
            max_items_per_topic=2,
            min_primary_or_research=2,
        )

        self.assertGreaterEqual(
            sum(item["source_tier"] in {"primary", "research"} for item in selected),
            2,
        )
        self.assertEqual(report["primary_or_research_count"], 2)

    def test_assign_source_tier_keeps_existing_tier_and_defaults_by_source_type(self):
        self.assertEqual(
            assign_source_tier({"source_tier": "primary"})["source_tier"],
            "primary",
        )
        self.assertEqual(
            assign_source_tier({"source_type": "arxiv"})["source_tier"],
            "research",
        )
        self.assertEqual(
            assign_source_tier({"source_type": "hn"})["source_tier"],
            "community",
        )

    def test_selection_reports_relaxed_caps_when_no_alternative_exists(self):
        items = [
            _item(f"Only publisher {index}", "Only Publisher", 100 - index)
            for index in range(3)
        ]

        selected, _, report = select_editorial_candidates(
            items,
            target_count=3,
            max_items_per_source=2,
            min_primary_or_research=0,
        )

        self.assertEqual(len(selected), 3)
        self.assertTrue(report["cap_relaxed"])
        self.assertEqual(report["source_counts"], {"only publisher": 3})


if __name__ == "__main__":
    unittest.main()

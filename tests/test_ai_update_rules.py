import pytest

from src.briefing.update import evaluate_ai_update_candidate


def test_concrete_benchmark_progress_is_ai_update():
    result = evaluate_ai_update_candidate({
        "title": "Qwen3.8-27B GGUF scores 10% higher on Div-300",
        "summary": "Qwen3.8-27B GGUF scores 10% higher on Div-300 benchmark.",
    })

    assert result.eligible is True


def test_title_only_promotional_post_is_not_ai_update():
    result = evaluate_ai_update_candidate({
        "title": "Register for our AI workshop to learn Model 2.0 benchmark techniques today",
    })

    assert result.eligible is False
    assert result.reason_codes == ("update_promotional_or_repost",)


def test_bare_rank_without_concrete_progress_is_not_an_update():
    result = evaluate_ai_update_candidate({
        "title": "The community keeps discussing #6 on the leaderboard this week",
    })

    assert result.eligible is False


@pytest.mark.parametrize("text", [
    "Register for our AI workshop",
    "https://t.co/example",
    "Great work!",
])
def test_promotional_link_only_and_vague_posts_are_not_updates(text):
    result = evaluate_ai_update_candidate({"title": text, "summary": text})

    assert result.eligible is False

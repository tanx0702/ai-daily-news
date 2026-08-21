import pytest

from src.briefing.update import evaluate_ai_update_candidate


def test_concrete_benchmark_progress_is_ai_update():
    result = evaluate_ai_update_candidate({
        "title": "Qwen3.8-27B GGUF scores 10% higher on Div-300",
        "summary": "Qwen3.8-27B GGUF scores 10% higher on Div-300 benchmark.",
    })

    assert result.eligible is True


@pytest.mark.parametrize("text", [
    "Register for our AI workshop",
    "https://t.co/example",
    "Great work!",
])
def test_promotional_link_only_and_vague_posts_are_not_updates(text):
    result = evaluate_ai_update_candidate({"title": text, "summary": text})

    assert result.eligible is False

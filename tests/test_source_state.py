from pathlib import Path

from src.source_state import SourceStateStore


def test_source_state_records_success_and_resets_failure_streak(tmp_path):
    store = SourceStateStore(str(tmp_path / "state.db"))

    store.record(
        "OpenAI Blog",
        "https://example.test/feed",
        status="error",
        item_count=0,
        latency_ms=20,
        error="timeout",
    )
    store.record(
        "OpenAI Blog",
        "https://example.test/feed",
        status="success",
        item_count=3,
        latency_ms=40,
        content_hash="abc",
    )

    row = store.snapshot()["OpenAI Blog"]

    assert row["status"] == "success"
    assert row["consecutive_failures"] == 0
    assert row["last_item_count"] == 3
    assert row["last_content_hash"] == "abc"


def test_source_state_preserves_failure_streak_and_is_serializable(tmp_path):
    store = SourceStateStore(str(tmp_path / "state.db"))

    for _ in range(2):
        store.record(
            "Feed",
            "https://example.test/feed",
            status="timeout",
            item_count=0,
            latency_ms=100,
            error="timeout",
        )

    row = store.snapshot()["Feed"]

    assert row["consecutive_failures"] == 2
    assert isinstance(row["last_attempt_at"], str)


def test_source_state_from_environment_uses_runtime_default(monkeypatch):
    monkeypatch.delenv("SOURCE_STATE_DB_PATH", raising=False)

    store = SourceStateStore.from_environment()

    assert Path(store.path).parts[-2:] == ("runtime", "source-state.db")
    store.close()

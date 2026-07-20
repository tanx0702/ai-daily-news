"""Record one human validation label for a saved shadow run."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.domain.models import FeedbackLabel
from src.services.shadow_history import record_feedback


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Record feedback for one shadow candidate.")
    parser.add_argument(
        "--history-dir",
        default=str(PROJECT_ROOT / "docs" / "debug" / "shadow"),
    )
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--candidate-id", required=True)
    parser.add_argument("--label", required=True, choices=[label.value for label in FeedbackLabel])
    parser.add_argument("--note", default="")
    args = parser.parse_args(argv)

    try:
        event, feedback_path = record_feedback(
            history_dir=Path(args.history_dir),
            run_id=args.run_id,
            candidate_id=args.candidate_id,
            label=args.label,
            note=args.note,
        )
    except (FileNotFoundError, ValueError) as exc:
        parser.error(str(exc))

    print(
        json.dumps(
            {"event": event, "feedback_path": str(feedback_path.resolve())},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

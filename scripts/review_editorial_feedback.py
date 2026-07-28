"""Generate a local calibration retrospective from shadow history and human feedback."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.services.editorial_retrospective import (
    build_editorial_retrospective,
    save_editorial_retrospective,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Generate a local editorial calibration report from shadow-run history. "
            "This command never runs the production news pipeline."
        ),
    )
    parser.add_argument(
        "--history-dir",
        default=str(PROJECT_ROOT / "docs" / "debug" / "shadow"),
    )
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--days", type=int, default=30)
    args = parser.parse_args(argv)
    if args.days <= 0:
        parser.error("--days must be a positive integer")

    history_dir = Path(args.history_dir)
    output_dir = Path(args.output_dir) if args.output_dir else history_dir / "reviews"
    try:
        report = build_editorial_retrospective(history_dir, days=args.days)
        json_path, markdown_path = save_editorial_retrospective(
            report,
            output_dir=output_dir,
        )
    except (OSError, ValueError, FileExistsError) as exc:
        parser.error(str(exc))

    print(
        json.dumps(
            {
                "report": report,
                "json_path": str(json_path.resolve()),
                "markdown_path": str(markdown_path.resolve()),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

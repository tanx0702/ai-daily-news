"""Run the v2 Collector -> Analyst -> Editorial shadow workflow only."""

from __future__ import annotations

import argparse
import json
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

# Direct script execution puts ``scripts/`` on sys.path instead of the project root.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.domain.models import WorkflowResult
from src.services.editorial_report import build_editorial_report
from src.services.production_snapshot import load_production_snapshot
from src.services.shadow_history import save_shadow_report
from src.time_utils import report_date_str
from src.workflows.daily_edition import DailyEditionWorkflow


DEFAULT_HISTORY_DIR = PROJECT_ROOT / "docs" / "debug" / "shadow"


def _default_snapshot_path() -> Path:
    return DEFAULT_HISTORY_DIR / f"production-candidates-{report_date_str()}.json"


def report_from_result(result: WorkflowResult) -> dict[str, Any]:
    """Return a JSON-safe report with no render or publishing capability."""
    plan = result.editorial_plan
    return {
        "shadow_only": True,
        "publishing_enabled": False,
        "state": result.state.value,
        "state_history": [state.value for state in result.state_history],
        "candidate_count": len(result.candidates),
        "analysis_count": len(result.analyses),
        "selection_report": dict(plan.selection_report) if plan else {},
        "decisions": [
            {
                "candidate_id": decision.candidate_id,
                "action": decision.action.value,
                "rank": decision.rank,
                "audience": decision.brief.audience,
                "angle": decision.brief.angle,
                "title_direction": decision.brief.title_direction,
                "reason": decision.reason,
            }
            for decision in (plan.decisions if plan else ())
        ],
        "error": result.error,
    }


def save_shadow_result(
    result: WorkflowResult,
    *,
    history_dir: Path,
    run_id: str | None = None,
    generated_at: datetime | None = None,
) -> tuple[dict[str, Any], Path]:
    """Build and atomically persist the complete report for one shadow run."""
    generated_at = generated_at or datetime.now(timezone.utc)
    run_id = run_id or _new_run_id(generated_at)
    report = build_editorial_report(result, run_id=run_id, generated_at=generated_at)
    path = save_shadow_report(report, history_dir=history_dir)
    return report, path


def _new_run_id(generated_at: datetime) -> str:
    timestamp = generated_at.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"shadow-{timestamp}-{secrets.token_hex(3)}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the v2 editorial MVP without rendering or publishing.",
    )
    parser.add_argument("--top-n", type=int, default=10)
    parser.add_argument("--rss-timeout", type=int, default=30, help=argparse.SUPPRESS)
    parser.add_argument("--history-dir", default=str(DEFAULT_HISTORY_DIR))
    parser.add_argument(
        "--snapshot",
        default=str(_default_snapshot_path()),
        help="Production candidate snapshot to analyze.",
    )
    args = parser.parse_args(argv)

    load_dotenv()
    try:
        items, collection_diagnostics = load_production_snapshot(args.snapshot)
    except ValueError as exc:
        print(f"Cannot load production snapshot: {exc}", file=sys.stderr)
        return 1

    result = DailyEditionWorkflow().run_existing(
        items,
        top_n=args.top_n,
        collection_diagnostics=collection_diagnostics,
    )
    report, path = save_shadow_result(result, history_dir=Path(args.history_dir))
    report["history_path"] = str(path.resolve())
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not result.error else 1


if __name__ == "__main__":
    raise SystemExit(main())

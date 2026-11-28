"""Argument parser definitions and type validators for the CLI."""

import argparse
from collections.abc import Callable
from datetime import datetime
from typing import Any

from opencode_go_model_optimizer.engine.tiers import STANDARD_TIERS
from opencode_go_model_optimizer.ingest.http import package_version
from opencode_go_model_optimizer.ingest.models import PLANS, ensure_utc

PROG = "opencode-go-model-optimizer"
TIERS = tuple(STANDARD_TIERS)
EXIT_CODES = """\
exit codes:
  0    success
  1    runtime error
  2    usage error, diff --fail-on-change detected changes, or report --fail-on-warnings
       found warnings
  3    select --strict found an empty tier
  130  interrupted
"""


def _parse_csv_numbers(
    value: str,
    count: int,
    converter: Callable[[str], Any],
    error_msg: str,
    *,
    non_negative: bool = False,
) -> tuple[Any, ...]:
    """Parse a fixed-length comma-separated numeric argument."""
    try:
        parts = tuple(converter(part) for part in value.split(","))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(error_msg) from exc
    if len(parts) != count or (non_negative and any(part < 0 for part in parts)):
        raise argparse.ArgumentTypeError(error_msg)
    return parts


def _parse_token_mix(value: str) -> tuple[int, int, int]:
    """Parse an IN,CACHE,OUT token mix argument."""
    a, b, c = _parse_csv_numbers(
        value,
        3,
        int,
        "--tokens must be three non-negative comma-separated integers: IN,CACHE,OUT",
        non_negative=True,
    )
    return (a, b, c)


def _parse_weights(value: str) -> tuple[float, float]:
    """Parse an IQ,CODING benchmark weight argument."""
    a, b = _parse_csv_numbers(
        value,
        2,
        float,
        "--weights must be two comma-separated numbers: IQ,CODING",
    )
    return (a, b)


def _split_prefer_item(value: str) -> tuple[str, str, bool]:
    """Split a TIER=MODEL_ID string into (tier, model_id, has_separator)."""
    tier, sep, model_id = value.partition("=")
    return tier.strip(), model_id.strip(), bool(sep)


def _prefer_arg(value: str) -> str:
    """Validate a TIER=MODEL_ID --prefer argument."""
    tier, model_id, has_sep = _split_prefer_item(value)
    if not has_sep:
        raise argparse.ArgumentTypeError(
            f"--prefer must be TIER=MODEL_ID (e.g. heavy=zeta-pro), got {value!r}"
        )
    if tier not in TIERS:
        raise argparse.ArgumentTypeError(
            f"--prefer unknown tier {tier!r}; choose from {', '.join(TIERS)}"
        )
    if not model_id:
        raise argparse.ArgumentTypeError(f"--prefer is missing a model id in {value!r}")
    return value


def _parse_at(value: str) -> datetime:
    """Parse an ISO 8601 timestamp (UTC when no timezone is given)."""
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"--at must be an ISO 8601 time, got {value!r}") from exc
    return ensure_utc(dt)


def _build_parent_parsers() -> tuple[
    argparse.ArgumentParser,
    argparse.ArgumentParser,
    argparse.ArgumentParser,
    argparse.ArgumentParser,
]:
    """Build shared help-less parent parsers for CLI subcommands."""
    plan = argparse.ArgumentParser(add_help=False)
    plan.add_argument(
        "--plan",
        choices=list(PLANS),
        default="go",
        help="OpenCode plan tier (default: go)",
    )
    plan.add_argument(
        "--aa-cache-ttl",
        type=int,
        default=None,
        help=(
            "Cache TTL in seconds for Artificial Analysis benchmarks "
            "(default: 0, or OPENCODE_GO_AA_CACHE_TTL)"
        ),
    )

    # Shared by select, rank, and diff.
    ranking = argparse.ArgumentParser(add_help=False)
    ranking.add_argument(
        "--mode",
        choices=["auto", "pareto", "allowance"],
        default="auto",
        help="Ranking mode (default: auto)",
    )
    ranking.add_argument(
        "--allow-fallback",
        action="store_true",
        help=(
            "Allow falling back from pareto mode to allowance mode when Artificial Analysis "
            "benchmarks are unavailable (disabled by default)"
        ),
    )
    ranking.add_argument(
        "--profile",
        choices=["general", "coding", "reasoning", "fast"],
        default="general",
        help="Workload profile for token mix and benchmark weighting (default: general)",
    )
    ranking.add_argument(
        "--cache-ttl",
        type=int,
        default=None,
        help="Cache TTL in seconds for live snapshot fetches (default: 0)",
    )
    ranking.add_argument("-o", "--output", help="Output file path (default: stdout)")

    # Shared by select and rank.
    price = argparse.ArgumentParser(add_help=False)
    price.add_argument(
        "--price-basis",
        choices=["now", "blended", "worst", "list"],
        default="blended",
        help=(
            "Price multiplier basis: now (at --at), blended (7-day time-weighted average), "
            "worst (7-day maximum), list (ignore multipliers) (default: blended)"
        ),
    )
    price.add_argument(
        "--at",
        type=_parse_at,
        default=None,
        help="Reference ISO 8601 time for multipliers (UTC if no timezone; default: now)",
    )

    # Shared by select and rank.
    filt = argparse.ArgumentParser(add_help=False)
    filt.add_argument("--snapshot", help="Path to offline snapshot JSON file")
    filt.add_argument(
        "--skip",
        action="append",
        default=[],
        help="Model ID to exclude (can be specified multiple times)",
    )
    filt.add_argument(
        "--min-context",
        type=int,
        default=0,
        help="Minimum context window in tokens (default: 0)",
    )
    filt.add_argument(
        "--min-output",
        type=int,
        default=0,
        help="Minimum output token limit (default: 0)",
    )
    filt.add_argument(
        "--max-output-cost",
        type=float,
        default=None,
        help="Maximum output token cost in USD per 1M tokens",
    )
    filt.add_argument(
        "--max-burn",
        type=float,
        default=None,
        help="Maximum monthly quota burn percentage per 1,000 turns",
    )
    filt.add_argument(
        "--tokens",
        type=_parse_token_mix,
        default=None,
        help="Custom turn token mix as IN,CACHE,OUT (e.g. --tokens 2000,120000,1000)",
    )
    filt.add_argument(
        "--burn-basis",
        choices=["auto", "tokens", "estimate"],
        default="auto",
        help=(
            "Quota burn basis: tokens (synthetic token mix x price), estimate (computed "
            "requests per plan, scaled by price multipliers), auto (estimate unless "
            "--tokens or a non-general --profile is given) (default: auto)"
        ),
    )
    filt.add_argument(
        "--weights",
        type=_parse_weights,
        default=None,
        help="Custom benchmark weights as IQ,CODING (e.g. --weights 0.5,0.5)",
    )
    filt.add_argument(
        "--allow-global-region",
        action="store_true",
        help="Allow models that require Global regions in workspace privacy settings "
        "(blocked by default)",
    )
    filt.add_argument(
        "--allow-paid-training",
        action="store_true",
        help="Allow paid endpoints that train on request data (blocked by default)",
    )
    filt.add_argument(
        "--allow-free-training",
        action="store_true",
        help="Allow free endpoints that train on request data (blocked by default; "
        "turning this off disables those free models)",
    )
    filt.add_argument(
        "--max-retention-days",
        type=int,
        metavar="N",
        default=None,
        help="Exclude models retaining data longer than N days (unknown retention fails)",
    )
    filt.add_argument(
        "--require-zdr",
        action="store_true",
        help="Only allow zero-data-retention models (unknown fails)",
    )

    return plan, ranking, price, filt


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="Dynamic model tiering and Pareto efficiency optimizer for OpenCode Go",
        epilog=EXIT_CODES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"{PROG} {package_version()}")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    plan_parent, ranking_parent, price_parent, filter_parent = _build_parent_parsers()

    select_parser = subparsers.add_parser(
        "select",
        help="Select optimal models across capability tiers",
        parents=[plan_parent, ranking_parent, price_parent, filter_parent],
    )
    select_parser.add_argument(
        "--format",
        choices=["json", "table", "compact", "env", "github-output"],
        default="json",
        help="Output format (default: json)",
    )
    select_parser.add_argument(
        "--prefer",
        action="append",
        type=_prefer_arg,
        default=[],
        help="Pin a model to a tier, e.g. --prefer heavy=zeta-pro (can be specified multiple times)",
    )
    select_parser.add_argument(
        "--top-n",
        type=int,
        default=1,
        help="Number of ordered candidate models per tier for fallback chains (default: 1)",
    )
    select_parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit with code 3 (after writing output) when any tier has no model",
    )
    select_parser.add_argument(
        "--explain",
        action="store_true",
        help="Include detailed per-tier metrics and selection rationale",
    )

    rank_parser = subparsers.add_parser(
        "rank",
        help="Rank all active models in the catalog",
        parents=[plan_parent, ranking_parent, price_parent, filter_parent],
    )
    rank_parser.add_argument(
        "--format",
        choices=["table", "json"],
        default="table",
        help="Output format (default: table)",
    )

    diff_parser = subparsers.add_parser(
        "diff",
        help="Diff catalog, pricing, and tier assignments between snapshots",
        parents=[plan_parent, ranking_parent],
    )
    diff_parser.add_argument(
        "--old",
        required=True,
        help="Path to the old snapshot (e.g. one saved earlier with 'fetch')",
    )
    diff_parser.add_argument(
        "--new",
        default=None,
        help="Path to the new snapshot (default: live fetch, else your local cache)",
    )
    diff_parser.add_argument(
        "--format",
        choices=["table", "json"],
        default="table",
        help="Output format (default: table)",
    )
    diff_parser.add_argument(
        "--fail-on-change",
        action="store_true",
        dest="fail_on_change",
        help="Exit with code 2 when catalog, pricing, or tier assignments changed",
    )

    fetch_parser = subparsers.add_parser(
        "fetch", help="Fetch and save snapshot data", parents=[plan_parent]
    )
    fetch_parser.add_argument(
        "--out",
        default="snapshot.json",
        help="Destination path for snapshot (default: snapshot.json)",
    )

    report_parser = subparsers.add_parser(
        "report",
        help="Fetch live and publish per-plan results with how and why they were decided",
    )
    report_parser.add_argument(
        "--out-dir", required=True, help="Directory to write results.json and README.md into"
    )
    report_parser.add_argument(
        "--tests-junit",
        action="append",
        default=[],
        metavar="PATH",
        help="JUnit XML from a test run to summarise in the report (can be repeated)",
    )
    report_parser.add_argument(
        "--fail-on-warnings",
        action="store_true",
        help="Exit with code 2 (after writing) when the report contains warnings",
    )

    return parser.parse_args(argv)

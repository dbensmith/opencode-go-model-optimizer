"""Command line interface for opencode-go-model-optimizer."""

import argparse
import math
import os
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NamedTuple

import httpx

from opencode_go_model_optimizer.engine.criteria import SelectionCriteria, passes_privacy
from opencode_go_model_optimizer.engine.diff import diff_snapshots
from opencode_go_model_optimizer.engine.tiers import (
    STANDARD_TIERS,
    TierSelection,
    rank_catalog,
    resolve_mode,
    select_tiers,
)
from opencode_go_model_optimizer.formatters.output import (
    explain_tiers,
    format_diff_json,
    format_diff_table,
    format_rank_json,
    format_rank_table,
    format_select_compact,
    format_select_env,
    format_select_github_output,
    format_select_json,
    format_select_table,
    settings_payload,
    tier_display_names,
)
from opencode_go_model_optimizer.ingest.http import atomic_write_text, package_version
from opencode_go_model_optimizer.ingest.models import (
    PLANS,
    NormalizedModel,
    Snapshot,
    normalize_plan,
)
from opencode_go_model_optimizer.ingest.snapshot import (
    apply_plan,
    fetch_and_build_snapshot,
    load_cached_snapshot,
    load_snapshot,
    save_cached_snapshot,
    save_snapshot,
)
from opencode_go_model_optimizer.report import build_report, write_report

_PROG = "opencode-go-model-optimizer"
_TIERS = tuple(STANDARD_TIERS)
_EXIT_CODES = """\
exit codes:
  0    success
  1    runtime error
  2    usage error, diff --fail-on-change detected changes, or report --fail-on-warnings
       found warnings
  3    select --strict found an empty tier
  130  interrupted
"""


def _ensure_utc(dt: datetime) -> datetime:
    """Normalize a naive or timezone-aware datetime to UTC."""
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


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


def _env_cache_ttl() -> int:
    """Read OPENCODE_GO_CACHE_TTL, returning 0 (with a warning) on invalid values."""
    raw = os.environ.get("OPENCODE_GO_CACHE_TTL", "0")
    try:
        value = int(raw)
    except ValueError:
        value = -1
    if value < 0:
        print(
            f"{_PROG}: warning: invalid OPENCODE_GO_CACHE_TTL={raw!r}; using 0",
            file=sys.stderr,
        )
        return 0
    return value


def _cache_ttl(args: argparse.Namespace) -> int:
    """Return --cache-ttl, falling back to OPENCODE_GO_CACHE_TTL when the flag was omitted."""
    return _env_cache_ttl() if args.cache_ttl is None else args.cache_ttl


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
    if tier not in _TIERS:
        raise argparse.ArgumentTypeError(
            f"--prefer unknown tier {tier!r}; choose from {', '.join(_TIERS)}"
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
    return _ensure_utc(dt)


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

    # Shared by select, rank, and diff.
    ranking = argparse.ArgumentParser(add_help=False)
    ranking.add_argument(
        "--mode",
        choices=["auto", "pareto", "allowance"],
        default="auto",
        help="Ranking mode (default: auto)",
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
        prog=_PROG,
        description="Dynamic model tiering and Pareto efficiency optimizer for OpenCode Go",
        epilog=_EXIT_CODES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"{_PROG} {package_version()}")
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


def _effective_plan(snap_plan: str, requested_plan: str, models: list[NormalizedModel]) -> str:
    """Return requested plan if available in allowances, else retain snapshot plan."""
    norm_plan = normalize_plan(requested_plan)
    if any(norm_plan in m.plan_allowances or requested_plan in m.plan_allowances for m in models):
        return norm_plan
    return snap_plan


class LoadedSnapshot(NamedTuple):
    """A snapshot plus where it came from: file, cache, stale-cache, or live."""

    snapshot: Snapshot
    source: str


def _print_snapshot_warnings(snap: Snapshot) -> None:
    """Print each ingest warning to stderr without failing the command."""
    for warning in snap.warnings:
        print(f"{_PROG}: warning: {warning}", file=sys.stderr)


class SnapshotUnavailableError(ValueError):
    """Live fetch failed and there is no local cache to fall back to."""


def _age(fetched_at: str) -> str:
    """Human-readable age of an ISO timestamp (e.g. '3d 4h'), or 'unknown'."""
    try:
        then = _ensure_utc(datetime.fromisoformat(fetched_at))
    except ValueError:
        return "unknown"
    seconds = int((datetime.now(UTC) - then).total_seconds())
    days, rest = divmod(max(seconds, 0), 86400)
    hours, rest = divmod(rest, 3600)
    return f"{days}d {hours}h" if days else f"{hours}h {rest // 60}m"


def _load_or_fetch_snapshot(
    snapshot_path: str | None = None,
    plan: str = "go",
    cache_ttl: int = 0,
) -> LoadedSnapshot:
    """Retrieve a snapshot from a file if provided, otherwise fetch/cache live."""
    if snapshot_path:
        return LoadedSnapshot(load_snapshot(snapshot_path), "file")

    if cache_ttl > 0 and (cached := load_cached_snapshot(plan=plan, ttl_seconds=cache_ttl)):
        return LoadedSnapshot(cached, "cache")

    try:
        snap = fetch_and_build_snapshot(plan=plan)
    except (httpx.HTTPError, OSError, ValueError) as exc:
        stale = load_cached_snapshot(plan=plan, ttl_seconds=math.inf)
        if stale is None:
            raise SnapshotUnavailableError(
                f"live fetch failed ({type(exc).__name__}: {exc}) and no local cache exists; "
                "pass --snapshot FILE (see the 'fetch' command) or retry when online"
            ) from exc
        print(
            f"{_PROG}: warning: live fetch failed ({type(exc).__name__}: {exc}); "
            f"using local cache fetched at {stale.fetched_at} ({_age(stale.fetched_at)} old)",
            file=sys.stderr,
        )
        return LoadedSnapshot(stale, "stale-cache")
    _print_snapshot_warnings(snap)
    save_cached_snapshot(snap)
    return LoadedSnapshot(snap, "live")


def _load_or_fetch_models(
    snapshot_path: str | None = None,
    plan: str = "go",
    cache_ttl: int = 0,
) -> tuple[list[NormalizedModel], str]:
    """Retrieve models from snapshot file if provided, otherwise fetch live."""
    snap = _load_or_fetch_snapshot(snapshot_path, plan=plan, cache_ttl=cache_ttl).snapshot
    effective_plan = _effective_plan(snap.plan, plan, snap.models)
    return apply_plan(snap.models, effective_plan), effective_plan


def _write_output(content: str, out_path: str | None = None) -> None:
    """Write text to file if specified, otherwise stdout."""
    if out_path:
        p = Path(out_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(p, content + "\n")
    else:
        print(content)


def _parse_preferred_tiers(prefer_list: list[str] | None) -> dict[str, str]:
    """Parse --prefer key=value arguments into a dictionary."""
    preferred: dict[str, str] = {}
    for item in prefer_list or ():
        tier, model_id, has_sep = _split_prefer_item(item)
        if has_sep and tier and model_id:
            preferred[tier] = model_id
    return preferred


def _criteria_from_args(args: argparse.Namespace, mode: str) -> SelectionCriteria:
    """Build selection criteria from parsed select/rank arguments and the resolved mode."""
    return SelectionCriteria(
        mode=mode,
        top_n=getattr(args, "top_n", None) or 1,
        preferred_tiers=_parse_preferred_tiers(getattr(args, "prefer", None)),
        skip_models=frozenset(args.skip),
        allow_global_region=args.allow_global_region,
        allow_paid_training=args.allow_paid_training,
        allow_free_training=args.allow_free_training,
        max_retention_days=args.max_retention_days,
        require_zdr=args.require_zdr,
        min_context=args.min_context,
        min_output=args.min_output,
        max_output_cost=args.max_output_cost,
        max_burn=args.max_burn,
        profile=args.profile,
        token_mix=args.tokens,
        weights=args.weights,
        price_basis=args.price_basis,
        burn_basis=args.burn_basis,
        at=args.at or datetime.now(UTC),
    )


def _warn_unmatched_preferred(
    models: list[NormalizedModel],
    selection: TierSelection,
    criteria: SelectionCriteria,
) -> None:
    """Emit stderr warnings for --prefer overrides that did not match an eligible model."""
    if not criteria.preferred_tiers:
        return
    candidate_ids = {
        ref for m in selection.models if m.status == "active" for ref in (m.id, m.full_id)
    }
    privacy_excluded = {
        ref for m in models if not passes_privacy(m, criteria) for ref in (m.id, m.full_id)
    }
    for tier, model_id in criteria.preferred_tiers.items():
        if model_id not in candidate_ids:
            reason = "excluded by privacy filters" if model_id in privacy_excluded else "not found"
            print(
                f"{_PROG}: warning: preferred model '{model_id}' for tier '{tier}' "
                f"{reason}; ignoring",
                file=sys.stderr,
            )


def _format_select_output(
    fmt: str,
    loaded: LoadedSnapshot,
    selection: TierSelection,
    criteria: SelectionCriteria,
    plan: str,
    mode: str,
    explain: bool,
) -> str:
    """Format tier selection results according to --format."""
    tiers = selection.primary
    fallbacks = selection.candidates if criteria.top_n > 1 else None
    names = tier_display_names(tiers, selection.models)
    snap = loaded.snapshot

    formatters: dict[str, Callable[[], str]] = {
        "json": lambda: format_select_json(
            tiers,
            mode=mode,
            plan=plan,
            explanations=explain_tiers(tiers, selection.models, criteria) if explain else None,
            profile=criteria.profile,
            fallbacks=fallbacks,
            names=names,
            snapshot_info={
                "source": loaded.source,
                "fetched_at": snap.fetched_at,
                "benchmark_source": snap.metadata.benchmark_source,
            },
            settings=settings_payload(criteria),
        ),
        "table": lambda: format_select_table(
            tiers, selection.models, plan=plan, explain=explain, criteria=criteria
        ),
        "env": lambda: format_select_env(tiers, fallbacks=fallbacks, names=names),
        "github-output": lambda: format_select_github_output(
            tiers, fallbacks=fallbacks, names=names
        ),
        "compact": lambda: format_select_compact(tiers, names=names),
    }
    return formatters[fmt]()


def handle_select(args: argparse.Namespace) -> int:
    """Handle the 'select' command."""
    loaded = _load_or_fetch_snapshot(args.snapshot, plan=args.plan, cache_ttl=_cache_ttl(args))
    snap = loaded.snapshot
    plan = _effective_plan(snap.plan, args.plan, snap.models)
    models = apply_plan(snap.models, plan)
    mode = resolve_mode(args.mode, models)

    criteria = _criteria_from_args(args, mode)
    selection = select_tiers(models, criteria)
    _warn_unmatched_preferred(models, selection, criteria)

    out = _format_select_output(
        args.format,
        loaded=loaded,
        selection=selection,
        criteria=criteria,
        plan=plan,
        mode=mode,
        explain=args.explain,
    )
    _write_output(out, args.output)

    empty = [t for t in _TIERS if not selection.primary.get(t)]
    if args.strict and empty:
        print(
            f"{_PROG}: error: no model available for tier(s): {', '.join(empty)}",
            file=sys.stderr,
        )
        return 3
    return 0


def handle_rank(args: argparse.Namespace) -> int:
    """Handle the 'rank' command."""
    models, _ = _load_or_fetch_models(args.snapshot, plan=args.plan, cache_ttl=_cache_ttl(args))
    mode = resolve_mode(args.mode, models)
    ranked = rank_catalog(models, _criteria_from_args(args, mode))
    out = (
        format_rank_json(ranked) if args.format == "json" else format_rank_table(ranked, mode=mode)
    )
    _write_output(out, args.output)
    return 0


def handle_diff(args: argparse.Namespace) -> int:
    """Handle the 'diff' command."""
    target_plan = args.plan
    old_snap = load_snapshot(args.old)
    new_snap = _load_or_fetch_snapshot(
        args.new, plan=target_plan, cache_ttl=_cache_ttl(args)
    ).snapshot
    diff = diff_snapshots(
        old_snap,
        new_snap,
        mode=args.mode,
        plan=target_plan,
        profile=args.profile,
    )
    out = format_diff_json(diff) if args.format == "json" else format_diff_table(diff)
    _write_output(out, args.output)
    return 2 if args.fail_on_change and diff.has_changes() else 0


def handle_fetch(args: argparse.Namespace) -> int:
    """Handle the 'fetch' command."""
    target_plan = args.plan
    snap = fetch_and_build_snapshot(plan=target_plan)
    _print_snapshot_warnings(snap)
    save_snapshot(snap, args.out)
    print(
        f"{_PROG}: saved snapshot with {len(snap.models)} models for plan '{target_plan}' to {args.out}",
        file=sys.stderr,
    )
    return 0


def handle_report(args: argparse.Namespace) -> int:
    """Handle the 'report' command: fetch live (no cache) and publish results."""
    report = build_report(junit_paths=args.tests_junit)
    write_report(report, args.out_dir)
    print(f"{_PROG}: wrote results.json and README.md to {args.out_dir}", file=sys.stderr)
    if args.fail_on_warnings and report["warnings"]:
        print(f"{_PROG}: error: report contains warnings", file=sys.stderr)
        return 2
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint."""
    args = parse_args(argv)
    if not args.command:
        print(f"{_PROG}: use --help for usage information", file=sys.stderr)
        return 0

    handlers: dict[str, Callable[[argparse.Namespace], int]] = {
        "select": handle_select,
        "rank": handle_rank,
        "diff": handle_diff,
        "fetch": handle_fetch,
        "report": handle_report,
    }
    handler = handlers.get(args.command)
    if handler is None:
        return 0

    try:
        return handler(args)
    except KeyboardInterrupt:
        return 130
    except (OSError, ValueError, httpx.HTTPError) as exc:
        # ValueError covers pydantic ValidationError and JSON decode errors.
        message = " ".join(f"{type(exc).__name__}: {exc}".split())
        print(f"{_PROG}: error: {message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

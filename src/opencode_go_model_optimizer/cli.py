"""Command line interface for opencode-go-model-optimizer."""

import argparse
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NamedTuple

import httpx

from opencode_go_model_optimizer.engine.criteria import SelectionCriteria
from opencode_go_model_optimizer.engine.diff import diff_snapshots
from opencode_go_model_optimizer.engine.tiers import rank_catalog, resolve_mode, select_tiers
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
)
from opencode_go_model_optimizer.ingest.http import atomic_write_text, package_version
from opencode_go_model_optimizer.ingest.models import NormalizedModel, Snapshot
from opencode_go_model_optimizer.ingest.snapshot import (
    apply_plan,
    fetch_and_build_snapshot,
    load_cached_snapshot,
    load_reference_snapshot,
    load_snapshot,
    save_cached_snapshot,
    save_snapshot,
    update_reference_snapshot,
)

_PROG = "opencode-go-model-optimizer"
_TIERS = ("heavy", "balanced", "light", "free")
_EXIT_CODES = """\
exit codes:
  0    success
  1    runtime error
  2    usage error, or diff --fail-on-change detected changes
  3    select --strict found an empty tier
  130  interrupted
"""


def _parse_token_mix(value: str) -> tuple[int, int, int]:
    """Parse an IN,CACHE,OUT token mix argument."""
    try:
        parts = [int(part) for part in value.split(",")]
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "--tokens must be three comma-separated integers: IN,CACHE,OUT"
        ) from exc
    if len(parts) != 3 or any(part < 0 for part in parts):
        raise argparse.ArgumentTypeError(
            "--tokens must be three non-negative comma-separated integers: IN,CACHE,OUT"
        )
    return (parts[0], parts[1], parts[2])


def _parse_weights(value: str) -> tuple[float, float]:
    """Parse an IQ,CODING benchmark weight argument."""
    try:
        parts = [float(part) for part in value.split(",")]
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "--weights must be two comma-separated numbers: IQ,CODING"
        ) from exc
    if len(parts) != 2:
        raise argparse.ArgumentTypeError("--weights must be two comma-separated numbers: IQ,CODING")
    return (parts[0], parts[1])


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


def _prefer_arg(value: str) -> str:
    """Validate a TIER=MODEL_ID --prefer argument."""
    tier, sep, model_id = value.partition("=")
    if not sep:
        raise argparse.ArgumentTypeError(
            f"--prefer must be TIER=MODEL_ID (e.g. heavy=kimi-k3.1-pro), got {value!r}"
        )
    if tier.strip() not in _TIERS:
        raise argparse.ArgumentTypeError(
            f"--prefer unknown tier {tier.strip()!r}; choose from {', '.join(_TIERS)}"
        )
    if not model_id.strip():
        raise argparse.ArgumentTypeError(f"--prefer is missing a model id in {value!r}")
    return value


def _parse_at(value: str) -> datetime:
    """Parse an ISO 8601 timestamp (UTC when no timezone is given)."""
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"--at must be an ISO 8601 time, got {value!r}") from exc
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def _parent(*specs: tuple[tuple[str, ...], dict[str, Any]]) -> argparse.ArgumentParser:
    """Build a help-less parent parser from (flags, kwargs) argument specs."""
    parent = argparse.ArgumentParser(add_help=False)
    for flags, kwargs in specs:
        parent.add_argument(*flags, **kwargs)
    return parent


_PLAN_PARENT = _parent(
    (
        ("--plan",),
        {
            "choices": ["go", "go-plus", "go-teams"],
            "default": "go",
            "help": "OpenCode plan tier (default: go)",
        },
    ),
)

# Shared by select, rank, and diff.
_RANKING_PARENT = _parent(
    (
        ("--mode",),
        {
            "choices": ["auto", "pareto", "allowance"],
            "default": "auto",
            "help": "Ranking mode (default: auto)",
        },
    ),
    (
        ("--profile",),
        {
            "choices": ["general", "coding", "reasoning", "fast"],
            "default": "general",
            "help": "Workload profile for token mix and benchmark weighting (default: general)",
        },
    ),
    (
        ("--cache-ttl",),
        {
            "type": int,
            "default": None,
            "help": "Cache TTL in seconds for live snapshot fetches (default: 0)",
        },
    ),
    (("-o", "--output"), {"help": "Output file path (default: stdout)"}),
)

# Shared by select and rank.
_PRICE_PARENT = _parent(
    (
        ("--price-basis",),
        {
            "choices": ["now", "blended", "worst", "list"],
            "default": "blended",
            "help": (
                "Price multiplier basis: now (at --at), blended (7-day time-weighted average), "
                "worst (7-day maximum), list (ignore multipliers) (default: blended)"
            ),
        },
    ),
    (
        ("--at",),
        {
            "type": _parse_at,
            "default": None,
            "help": "Reference ISO 8601 time for multipliers (UTC if no timezone; default: now)",
        },
    ),
)

# Shared by select and rank.
_FILTER_PARENT = _parent(
    (("--snapshot",), {"help": "Path to offline snapshot JSON file"}),
    (
        ("--skip",),
        {
            "action": "append",
            "default": [],
            "help": "Model ID to exclude (can be specified multiple times)",
        },
    ),
    (
        ("--min-context",),
        {"type": int, "default": 0, "help": "Minimum context window in tokens (default: 0)"},
    ),
    (
        ("--min-output",),
        {"type": int, "default": 0, "help": "Minimum output token limit (default: 0)"},
    ),
    (
        ("--max-output-cost",),
        {
            "type": float,
            "default": None,
            "help": "Maximum output token cost in USD per 1M tokens",
        },
    ),
    (
        ("--max-burn",),
        {
            "type": float,
            "default": None,
            "help": "Maximum monthly quota burn percentage per 1,000 turns",
        },
    ),
    (
        ("--tokens",),
        {
            "type": _parse_token_mix,
            "default": None,
            "help": "Custom turn token mix as IN,CACHE,OUT (e.g. --tokens 2000,120000,1000)",
        },
    ),
    (
        ("--weights",),
        {
            "type": _parse_weights,
            "default": None,
            "help": "Custom benchmark weights as IQ,CODING (e.g. --weights 0.5,0.5)",
        },
    ),
    (
        ("--no-contributor",),
        {
            "action": "store_true",
            "help": "Exclude contributor-tier models from free tier selection",
        },
    ),
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        prog="opencode-go-model-optimizer",
        description="Dynamic model tiering and Pareto efficiency optimizer for OpenCode Go",
        epilog=_EXIT_CODES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"{_PROG} {package_version()}")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    select_parser = subparsers.add_parser(
        "select",
        help="Select optimal models across capability tiers",
        parents=[_PLAN_PARENT, _RANKING_PARENT, _PRICE_PARENT, _FILTER_PARENT],
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
        help="Pin a model to a tier, e.g. --prefer heavy=kimi-k3.1-pro (can be specified multiple times)",
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
        parents=[_PLAN_PARENT, _RANKING_PARENT, _PRICE_PARENT, _FILTER_PARENT],
    )
    rank_parser.add_argument(
        "--table",
        action="store_true",
        help="Output results as a human-readable table",
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
        parents=[_PLAN_PARENT, _RANKING_PARENT],
    )
    diff_parser.add_argument(
        "--old",
        default=None,
        help="Path to the old snapshot (default: bundled reference snapshot)",
    )
    diff_parser.add_argument(
        "--new",
        default=None,
        help="Path to the new snapshot (default: live fetch or cached snapshot)",
    )
    diff_parser.add_argument(
        "--format",
        choices=["table", "json"],
        default="table",
        help="Output format (default: table)",
    )
    diff_parser.add_argument(
        "--fail-on-change",
        "--exit-code",
        action="store_true",
        dest="fail_on_change",
        help="Exit with code 2 when catalog, pricing, or tier assignments changed",
    )

    fetch_parser = subparsers.add_parser(
        "fetch", help="Fetch and save snapshot data", parents=[_PLAN_PARENT]
    )
    fetch_parser.add_argument(
        "--out",
        default="snapshot.json",
        help="Destination path for snapshot (default: snapshot.json)",
    )
    fetch_parser.add_argument(
        "--update-reference",
        action="store_true",
        help="Fetch live data and update repository reference snapshot",
    )

    return parser.parse_args(argv)


def _effective_plan(snap_plan: str, requested_plan: str, models: list[NormalizedModel]) -> str:
    """Return requested plan if available in allowances, else retain snapshot plan."""
    norm_plan = requested_plan if requested_plan.startswith("go") else f"go-{requested_plan}"
    if any(norm_plan in m.plan_allowances or requested_plan in m.plan_allowances for m in models):
        return norm_plan
    return snap_plan


class LoadedSnapshot(NamedTuple):
    """A snapshot plus where it came from: file, cache, live, or reference."""

    snapshot: Snapshot
    source: str


def _print_snapshot_warnings(snap: Snapshot) -> None:
    """Print each ingest warning to stderr without failing the command."""
    for warning in snap.warnings:
        print(f"{_PROG}: warning: {warning}", file=sys.stderr)


def _load_or_fetch_snapshot(
    snapshot_path: str | None = None,
    plan: str = "go",
    cache_ttl: int = 0,
) -> LoadedSnapshot:
    """Retrieve a snapshot from a file if provided, otherwise fetch/cache live."""
    if snapshot_path:
        return LoadedSnapshot(load_snapshot(snapshot_path), "file")

    if cache_ttl > 0:
        cached = load_cached_snapshot(plan=plan, ttl_seconds=cache_ttl)
        if cached:
            return LoadedSnapshot(cached, "cache")

    try:
        snap = fetch_and_build_snapshot(plan=plan)
        _print_snapshot_warnings(snap)
        if cache_ttl > 0:
            save_cached_snapshot(snap)
        return LoadedSnapshot(snap, "live")
    except (httpx.HTTPError, OSError, ValueError) as exc:
        ref_snap = load_reference_snapshot()
        if ref_snap:
            print(
                f"{_PROG}: warning: live fetch failed ({type(exc).__name__}: {exc}); "
                f"using bundled reference snapshot from {ref_snap.fetched_at}",
                file=sys.stderr,
            )
            return LoadedSnapshot(ref_snap, "reference")
        raise


def _load_or_fetch_models(
    snapshot_path: str | None = None,
    plan: str = "go",
    cache_ttl: int = 0,
) -> tuple[list[NormalizedModel], str]:
    """Retrieve models from snapshot file if provided, otherwise fetch live."""
    snap = _load_or_fetch_snapshot(snapshot_path, plan=plan, cache_ttl=cache_ttl).snapshot
    return apply_plan(snap.models, plan), _effective_plan(snap.plan, plan, snap.models)


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
    if not prefer_list:
        return preferred
    for item in prefer_list:
        if "=" in item:
            tier, model_id = item.split("=", 1)
            tier = tier.strip()
            model_id = model_id.strip()
            if tier and model_id:
                preferred[tier] = model_id
    return preferred


def _criteria_from_args(args: argparse.Namespace, mode: str) -> SelectionCriteria:
    """Build selection criteria from parsed select/rank arguments and the resolved mode."""
    return SelectionCriteria(
        mode=mode,
        top_n=getattr(args, "top_n", None) or 1,
        preferred_tiers=_parse_preferred_tiers(getattr(args, "prefer", None)),
        skip_models=frozenset(args.skip),
        allow_contributor=not args.no_contributor,
        min_context=args.min_context,
        min_output=args.min_output,
        max_output_cost=args.max_output_cost,
        max_burn=args.max_burn,
        profile=args.profile,
        token_mix=args.tokens,
        weights=args.weights,
        price_basis=args.price_basis,
        at=args.at or datetime.now(UTC),
    )


def handle_select(args: argparse.Namespace) -> int:
    """Handle the 'select' command."""
    loaded = _load_or_fetch_snapshot(args.snapshot, plan=args.plan, cache_ttl=_cache_ttl(args))
    snap = loaded.snapshot
    models = apply_plan(snap.models, args.plan)
    plan = _effective_plan(snap.plan, args.plan, snap.models)
    mode = resolve_mode(args.mode, models)

    criteria = _criteria_from_args(args, mode)
    candidate_ids = {
        ref
        for m in models
        if m.id not in criteria.skip_models
        and m.full_id not in criteria.skip_models
        and m.limit.context >= args.min_context
        and m.limit.output >= args.min_output
        for ref in (m.id, m.full_id)
    }
    for tier, model_id in criteria.preferred_tiers.items():
        if model_id not in candidate_ids:
            print(
                f"{_PROG}: warning: preferred model '{model_id}' for tier '{tier}' "
                "not found; ignoring",
                file=sys.stderr,
            )
    profile = criteria.profile
    explain = args.explain

    selection = select_tiers(models, criteria)
    tiers = selection.primary
    fallbacks = selection.candidates if criteria.top_n > 1 else None

    if args.format == "json":
        explanations = explain_tiers(tiers, selection.models, criteria) if explain else None
        out = format_select_json(
            tiers,
            mode=mode,
            plan=plan,
            explanations=explanations,
            profile=profile,
            fallbacks=fallbacks,
            snapshot_info={
                "source": loaded.source,
                "fetched_at": snap.fetched_at,
                "benchmark_source": snap.metadata.benchmark_source,
            },
        )
    elif args.format == "table":
        out = format_select_table(
            tiers, selection.models, plan=plan, explain=explain, criteria=criteria
        )
    elif args.format == "env":
        out = format_select_env(tiers, fallbacks=fallbacks)
    elif args.format == "github-output":
        out = format_select_github_output(tiers, fallbacks=fallbacks)
    else:
        out = format_select_compact(tiers)

    _write_output(out, args.output)
    empty = [t for t in _TIERS if not tiers.get(t)]
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

    if args.format == "json" and not args.table:
        out = format_rank_json(ranked)
    else:
        out = format_rank_table(ranked, mode=mode)

    _write_output(out, args.output)
    return 0


def handle_diff(args: argparse.Namespace) -> int:
    """Handle the 'diff' command."""
    target_plan = args.plan

    if args.old:
        old_snap = load_snapshot(args.old)
    else:
        old_snap = load_reference_snapshot()
        if old_snap is None:
            print(
                "opencode-go-model-optimizer: no reference snapshot available; pass --old",
                file=sys.stderr,
            )
            return 1

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
    if args.fail_on_change and diff.has_changes():
        return 2
    return 0


def handle_fetch(args: argparse.Namespace) -> int:
    """Handle the 'fetch' command."""
    target_plan = args.plan
    if args.update_reference:
        path = update_reference_snapshot(plan=target_plan)
        print(
            f"opencode-go-model-optimizer: updated reference snapshot at {path}",
            file=sys.stderr,
        )
        return 0

    snap = fetch_and_build_snapshot(plan=target_plan)
    _print_snapshot_warnings(snap)
    save_snapshot(snap, args.out)
    print(
        f"opencode-go-model-optimizer: saved snapshot with {len(snap.models)} models for plan '{target_plan}' to {args.out}",
        file=sys.stderr,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint."""
    args = parse_args(argv)
    if not args.command:
        print("opencode-go-model-optimizer: use --help for usage information", file=sys.stderr)
        return 0

    try:
        if args.command == "select":
            return handle_select(args)
        if args.command == "rank":
            return handle_rank(args)
        if args.command == "diff":
            return handle_diff(args)
        if args.command == "fetch":
            return handle_fetch(args)
    except KeyboardInterrupt:
        return 130
    except (OSError, ValueError, httpx.HTTPError) as exc:
        # ValueError covers pydantic ValidationError and JSON decode errors.
        message = " ".join(f"{type(exc).__name__}: {exc}".split())
        print(f"{_PROG}: error: {message}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Command line interface for opencode-go-model-optimizer."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import httpx

from opencode_go_model_optimizer.engine.diff import diff_snapshots
from opencode_go_model_optimizer.engine.tiers import (
    allocate_tier_fallbacks,
    allocate_tiers,
    rank_catalog,
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
)
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


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        prog="opencode-go-model-optimizer",
        description="Dynamic model tiering and Pareto efficiency optimizer for OpenCode Go",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # select subcommand
    select_parser = subparsers.add_parser(
        "select", help="Select optimal models across capability tiers"
    )
    select_parser.add_argument(
        "--format",
        choices=["json", "table", "compact", "env", "github-output"],
        default="json",
        help="Output format (default: json)",
    )
    select_parser.add_argument(
        "--mode",
        choices=["auto", "pareto", "allowance"],
        default="auto",
        help="Ranking mode (default: auto)",
    )
    select_parser.add_argument(
        "--plan",
        choices=["go", "go-plus", "go-teams"],
        default="go",
        help="OpenCode plan tier (default: go)",
    )
    select_parser.add_argument(
        "-o",
        "--output",
        help="Output file path (default: stdout)",
    )
    select_parser.add_argument(
        "--snapshot",
        help="Path to offline snapshot JSON file",
    )
    select_parser.add_argument(
        "--cache-ttl",
        type=int,
        default=int(os.environ.get("OPENCODE_GO_CACHE_TTL", "0")),
        help="Cache TTL in seconds for live snapshot fetches (default: 0)",
    )
    select_parser.add_argument(
        "--skip",
        action="append",
        default=[],
        help="Model ID to exclude (can be specified multiple times)",
    )
    select_parser.add_argument(
        "--prefer",
        action="append",
        default=[],
        help="Pin a model to a tier, e.g. --prefer heavy=kimi-k3.1-pro (can be specified multiple times)",
    )
    select_parser.add_argument(
        "--min-context",
        type=int,
        default=0,
        help="Minimum context window in tokens (default: 0)",
    )
    select_parser.add_argument(
        "--min-output",
        type=int,
        default=0,
        help="Minimum output token limit (default: 0)",
    )
    select_parser.add_argument(
        "--max-output-cost",
        type=float,
        default=None,
        help="Maximum output token cost in USD per 1M tokens",
    )
    select_parser.add_argument(
        "--max-burn",
        type=float,
        default=None,
        help="Maximum monthly quota burn percentage per 1,000 turns",
    )
    select_parser.add_argument(
        "--tokens",
        type=_parse_token_mix,
        default=None,
        help="Custom turn token mix as IN,CACHE,OUT (e.g. --tokens 2000,120000,1000)",
    )
    select_parser.add_argument(
        "--weights",
        type=_parse_weights,
        default=None,
        help="Custom benchmark weights as IQ,CODING (e.g. --weights 0.5,0.5)",
    )
    select_parser.add_argument(
        "--top-n",
        type=int,
        default=1,
        help="Number of ordered candidate models per tier for fallback chains (default: 1)",
    )
    select_parser.add_argument(
        "--no-contributor",
        action="store_true",
        help="Exclude contributor-tier models from free tier selection",
    )
    select_parser.add_argument(
        "--profile",
        choices=["general", "coding", "reasoning", "fast"],
        default="general",
        help="Workload profile for token mix and benchmark weighting (default: general)",
    )
    select_parser.add_argument(
        "--explain",
        action="store_true",
        help="Include detailed per-tier metrics and selection rationale",
    )

    # rank subcommand
    rank_parser = subparsers.add_parser("rank", help="Rank all active models in the catalog")
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
    rank_parser.add_argument(
        "--mode",
        choices=["auto", "pareto", "allowance"],
        default="auto",
        help="Ranking mode (default: auto)",
    )
    rank_parser.add_argument(
        "--profile",
        choices=["general", "coding", "reasoning", "fast"],
        default="general",
        help="Workload profile for token mix and benchmark weighting (default: general)",
    )
    rank_parser.add_argument(
        "--plan",
        choices=["go", "go-plus", "go-teams"],
        default="go",
        help="OpenCode plan tier (default: go)",
    )
    rank_parser.add_argument(
        "--snapshot",
        help="Path to offline snapshot JSON file",
    )
    rank_parser.add_argument(
        "--cache-ttl",
        type=int,
        default=int(os.environ.get("OPENCODE_GO_CACHE_TTL", "0")),
        help="Cache TTL in seconds for live snapshot fetches (default: 0)",
    )
    rank_parser.add_argument(
        "--skip",
        action="append",
        default=[],
        help="Model ID to exclude (can be specified multiple times)",
    )
    rank_parser.add_argument(
        "--min-context",
        type=int,
        default=0,
        help="Minimum context window in tokens (default: 0)",
    )
    rank_parser.add_argument(
        "--min-output",
        type=int,
        default=0,
        help="Minimum output token limit (default: 0)",
    )
    rank_parser.add_argument(
        "--max-output-cost",
        type=float,
        default=None,
        help="Maximum output token cost in USD per 1M tokens",
    )
    rank_parser.add_argument(
        "--max-burn",
        type=float,
        default=None,
        help="Maximum monthly quota burn percentage per 1,000 turns",
    )
    rank_parser.add_argument(
        "--tokens",
        type=_parse_token_mix,
        default=None,
        help="Custom turn token mix as IN,CACHE,OUT (e.g. --tokens 2000,120000,1000)",
    )
    rank_parser.add_argument(
        "--weights",
        type=_parse_weights,
        default=None,
        help="Custom benchmark weights as IQ,CODING (e.g. --weights 0.5,0.5)",
    )
    rank_parser.add_argument(
        "--no-contributor",
        action="store_true",
        help="Exclude contributor-tier models from free tier selection",
    )
    rank_parser.add_argument(
        "-o",
        "--output",
        help="Output file path (default: stdout)",
    )

    # diff subcommand
    diff_parser = subparsers.add_parser(
        "diff", help="Diff catalog, pricing, and tier assignments between snapshots"
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
        "--plan",
        choices=["go", "go-plus", "go-teams"],
        default="go",
        help="OpenCode plan tier (default: go)",
    )
    diff_parser.add_argument(
        "--mode",
        choices=["auto", "pareto", "allowance"],
        default="auto",
        help="Tier allocation mode (default: auto)",
    )
    diff_parser.add_argument(
        "--profile",
        choices=["general", "coding", "reasoning", "fast"],
        default="general",
        help="Workload profile for token mix and benchmark weighting (default: general)",
    )
    diff_parser.add_argument(
        "--format",
        choices=["table", "json"],
        default="table",
        help="Output format (default: table)",
    )
    diff_parser.add_argument(
        "--cache-ttl",
        type=int,
        default=int(os.environ.get("OPENCODE_GO_CACHE_TTL", "0")),
        help="Cache TTL in seconds for live snapshot fetches (default: 0)",
    )
    diff_parser.add_argument(
        "--fail-on-change",
        "--exit-code",
        action="store_true",
        dest="fail_on_change",
        help="Exit with code 2 when catalog, pricing, or tier assignments changed",
    )
    diff_parser.add_argument(
        "-o",
        "--output",
        help="Output file path (default: stdout)",
    )

    # fetch subcommand
    fetch_parser = subparsers.add_parser("fetch", help="Fetch and save snapshot data")
    fetch_parser.add_argument(
        "--plan",
        choices=["go", "go-plus", "go-teams"],
        default="go",
        help="OpenCode plan tier (default: go)",
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


def _resolve_mode(mode: str, models: list[NormalizedModel]) -> str:
    """Resolve auto mode to either pareto or allowance based on benchmark data."""
    if mode != "auto":
        return mode
    if any(
        (m.aa_iq is not None or m.aa_coding is not None) for m in models if m.status == "active"
    ):
        return "pareto"
    return "allowance"


def _effective_plan(snap_plan: str, requested_plan: str, models: list[NormalizedModel]) -> str:
    """Return requested plan if available in allowances, else retain snapshot plan."""
    norm_plan = requested_plan if requested_plan.startswith("go") else f"go-{requested_plan}"
    if any(norm_plan in m.plan_allowances or requested_plan in m.plan_allowances for m in models):
        return norm_plan
    return snap_plan


def _load_or_fetch_snapshot(
    snapshot_path: str | None = None,
    plan: str = "go",
    cache_ttl: int = 0,
) -> Snapshot:
    """Retrieve a snapshot from a file if provided, otherwise fetch/cache live."""
    if snapshot_path:
        return load_snapshot(snapshot_path)

    if cache_ttl > 0:
        cached = load_cached_snapshot(plan=plan, ttl_seconds=cache_ttl)
        if cached:
            return cached

    try:
        snap = fetch_and_build_snapshot(plan=plan)
        if cache_ttl > 0:
            save_cached_snapshot(snap)
        return snap
    except (httpx.HTTPError, OSError, ValueError):
        ref_snap = load_reference_snapshot()
        if ref_snap:
            return ref_snap
        raise


def _load_or_fetch_models(
    snapshot_path: str | None = None,
    plan: str = "go",
    cache_ttl: int = 0,
) -> tuple[list[NormalizedModel], str]:
    """Retrieve models from snapshot file if provided, otherwise fetch live."""
    snap = _load_or_fetch_snapshot(snapshot_path, plan=plan, cache_ttl=cache_ttl)
    return apply_plan(snap.models, plan), _effective_plan(snap.plan, plan, snap.models)


def _write_output(content: str, out_path: str | None = None) -> None:
    """Write text to file if specified, otherwise stdout."""
    if out_path:
        p = Path(out_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(content + "\n")
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


def handle_select(args: argparse.Namespace) -> int:
    """Handle the 'select' command."""
    target_plan = getattr(args, "plan", "go") or "go"
    cache_ttl = getattr(args, "cache_ttl", 0) or 0
    models, plan = _load_or_fetch_models(args.snapshot, plan=target_plan, cache_ttl=cache_ttl)
    mode = _resolve_mode(args.mode, models)

    skip_models = set(getattr(args, "skip", []) or [])
    preferred_tiers = _parse_preferred_tiers(getattr(args, "prefer", []))
    min_context = getattr(args, "min_context", 0) or 0
    min_output = getattr(args, "min_output", 0) or 0
    max_output_cost = getattr(args, "max_output_cost", None)
    max_burn = getattr(args, "max_burn", None)
    token_mix = getattr(args, "tokens", None)
    weights = getattr(args, "weights", None)
    top_n = getattr(args, "top_n", 1) or 1
    allow_contributor = not getattr(args, "no_contributor", False)
    profile = getattr(args, "profile", "general") or "general"
    explain = bool(getattr(args, "explain", False))

    tiers = allocate_tiers(
        models,
        mode=mode,
        preferred_tiers=preferred_tiers,
        skip_models=skip_models,
        allow_contributor=allow_contributor,
        min_context=min_context,
        min_output=min_output,
        max_output_cost=max_output_cost,
        profile=profile,
        max_burn=max_burn,
        token_mix=token_mix,
        weights=weights,
    )

    fallbacks = (
        allocate_tier_fallbacks(
            models,
            top_n=top_n,
            mode=mode,
            preferred_tiers=preferred_tiers,
            skip_models=skip_models,
            allow_contributor=allow_contributor,
            min_context=min_context,
            min_output=min_output,
            max_output_cost=max_output_cost,
            profile=profile,
            max_burn=max_burn,
            token_mix=token_mix,
            weights=weights,
        )
        if top_n > 1
        else None
    )

    if args.format == "json":
        explanations = (
            explain_tiers(
                tiers,
                models,
                mode=mode,
                profile=profile,
                token_mix=token_mix,
                weights=weights,
            )
            if explain
            else None
        )
        out = format_select_json(
            tiers,
            mode=mode,
            plan=plan,
            explanations=explanations,
            profile=profile,
            fallbacks=fallbacks,
        )
    elif args.format == "table":
        out = format_select_table(
            tiers,
            models,
            plan=plan,
            explain=explain,
            profile=profile,
            token_mix=token_mix,
            weights=weights,
        )
    elif args.format == "env":
        out = format_select_env(tiers, fallbacks=fallbacks)
    elif args.format == "github-output":
        out = format_select_github_output(tiers, fallbacks=fallbacks)
    else:
        out = format_select_compact(tiers)

    _write_output(out, args.output)
    return 0


def handle_rank(args: argparse.Namespace) -> int:
    """Handle the 'rank' command."""
    target_plan = getattr(args, "plan", "go") or "go"
    cache_ttl = getattr(args, "cache_ttl", 0) or 0
    models, _ = _load_or_fetch_models(args.snapshot, plan=target_plan, cache_ttl=cache_ttl)
    mode = _resolve_mode(args.mode, models)

    skip_models = set(getattr(args, "skip", []) or [])
    min_context = getattr(args, "min_context", 0) or 0
    min_output = getattr(args, "min_output", 0) or 0
    max_output_cost = getattr(args, "max_output_cost", None)
    max_burn = getattr(args, "max_burn", None)
    token_mix = getattr(args, "tokens", None)
    weights = getattr(args, "weights", None)
    allow_contributor = not getattr(args, "no_contributor", False)
    profile = getattr(args, "profile", "general") or "general"

    ranked = rank_catalog(
        models,
        mode=mode,
        skip_models=skip_models,
        allow_contributor=allow_contributor,
        min_context=min_context,
        min_output=min_output,
        max_output_cost=max_output_cost,
        profile=profile,
        max_burn=max_burn,
        token_mix=token_mix,
        weights=weights,
    )

    if args.format == "json" and not args.table:
        out = format_rank_json(ranked)
    else:
        out = format_rank_table(ranked, mode=mode)

    _write_output(out, args.output)
    return 0


def handle_diff(args: argparse.Namespace) -> int:
    """Handle the 'diff' command."""
    target_plan = getattr(args, "plan", "go") or "go"
    cache_ttl = getattr(args, "cache_ttl", 0) or 0

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

    new_snap = _load_or_fetch_snapshot(args.new, plan=target_plan, cache_ttl=cache_ttl)
    diff = diff_snapshots(
        old_snap,
        new_snap,
        mode=args.mode,
        plan=target_plan,
        profile=args.profile,
    )

    out = format_diff_json(diff) if args.format == "json" else format_diff_table(diff)
    _write_output(out, args.output)
    if getattr(args, "fail_on_change", False) and diff.has_changes():
        return 2
    return 0


def handle_fetch(args: argparse.Namespace) -> int:
    """Handle the 'fetch' command."""
    target_plan = getattr(args, "plan", "go") or "go"
    if getattr(args, "update_reference", False):
        path = update_reference_snapshot(plan=target_plan)
        print(
            f"opencode-go-model-optimizer: updated reference snapshot at {path}",
            file=sys.stderr,
        )
        return 0

    snap = fetch_and_build_snapshot(plan=target_plan)
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

    if args.command == "select":
        return handle_select(args)
    if args.command == "rank":
        return handle_rank(args)
    if args.command == "diff":
        return handle_diff(args)
    if args.command == "fetch":
        return handle_fetch(args)

    return 0


if __name__ == "__main__":
    sys.exit(main())

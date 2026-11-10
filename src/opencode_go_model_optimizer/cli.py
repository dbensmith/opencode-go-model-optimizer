"""Command line interface for opencode-go-model-optimizer."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import httpx

from opencode_go_model_optimizer.engine.tiers import allocate_tiers, rank_catalog
from opencode_go_model_optimizer.formatters.output import (
    format_rank_json,
    format_rank_table,
    format_select_compact,
    format_select_json,
    format_select_table,
)
from opencode_go_model_optimizer.ingest.models import NormalizedModel
from opencode_go_model_optimizer.ingest.snapshot import (
    fetch_and_build_snapshot,
    load_reference_snapshot,
    load_snapshot,
    save_snapshot,
    update_reference_snapshot,
)


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
        choices=["json", "table", "compact"],
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
    """Resolve auto mode to either pareto or allowance based on API key and data."""
    if mode != "auto":
        return mode
    if os.environ.get("AA_API_KEY") and any(m.aa_iq is not None for m in models):
        return "pareto"
    return "allowance"


def _load_or_fetch_models(
    snapshot_path: str | None = None,
    plan: str = "go",
) -> tuple[list[NormalizedModel], str]:
    """Retrieve models from snapshot file if provided, otherwise fetch live."""
    if snapshot_path:
        snap = load_snapshot(snapshot_path)
        return snap.models, snap.plan
    try:
        snap = fetch_and_build_snapshot(plan=plan)
        return snap.models, snap.plan
    except (httpx.HTTPError, OSError, ValueError):
        ref_snap = load_reference_snapshot()
        if ref_snap:
            return ref_snap.models, ref_snap.plan
        raise


def _write_output(content: str, out_path: str | None = None) -> None:
    """Write text to file if specified, otherwise stdout."""
    if out_path:
        p = Path(out_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(content + "\n")
    else:
        print(content)


def handle_select(args: argparse.Namespace) -> int:
    """Handle the 'select' command."""
    target_plan = getattr(args, "plan", "go") or "go"
    models, plan = _load_or_fetch_models(args.snapshot, plan=target_plan)
    mode = _resolve_mode(args.mode, models)

    tiers = allocate_tiers(models, mode=mode)

    if args.format == "json":
        out = format_select_json(tiers, mode=mode, plan=plan)
    elif args.format == "table":
        out = format_select_table(tiers, models, plan=plan)
    else:
        out = format_select_compact(tiers)

    _write_output(out, args.output)
    return 0


def handle_rank(args: argparse.Namespace) -> int:
    """Handle the 'rank' command."""
    target_plan = getattr(args, "plan", "go") or "go"
    models, _ = _load_or_fetch_models(args.snapshot, plan=target_plan)
    mode = _resolve_mode(args.mode, models)

    ranked = rank_catalog(models, mode=mode)

    if args.format == "json" and not args.table:
        out = format_rank_json(ranked)
    else:
        out = format_rank_table(ranked, mode=mode)

    _write_output(out, args.output)
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
    if args.command == "fetch":
        return handle_fetch(args)

    return 0


if __name__ == "__main__":
    sys.exit(main())

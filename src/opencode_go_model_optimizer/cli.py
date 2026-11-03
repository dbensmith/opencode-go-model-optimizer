"""Command line interface for opencode-go-model-optimizer."""

from __future__ import annotations
import argparse
import sys


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="opencode-go-model-optimizer",
        description="Dynamic model tiering, Pareto efficiency ranker, and GSD selector for OpenCode Go",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # select subcommand
    select_parser = subparsers.add_parser("select", help="Select models for tier mapping")
    select_parser.add_argument(
        "--format",
        choices=["gsd", "json", "compact"],
        default="gsd",
        help="Output format (default: gsd)",
    )
    select_parser.add_argument(
        "--mode",
        choices=["auto", "pareto", "allowance"],
        default="auto",
        help="Ranking mode (default: auto)",
    )
    select_parser.add_argument(
        "-o",
        "--output",
        help="Output file path (default: stdout)",
    )

    # rank subcommand
    rank_parser = subparsers.add_parser("rank", help="Rank all active models")
    rank_parser.add_argument(
        "--table",
        action="store_true",
        help="Output results as a human-readable table",
    )

    # fetch subcommand
    fetch_parser = subparsers.add_parser("fetch", help="Fetch and save snapshot data")
    fetch_parser.add_argument(
        "--out",
        default="snapshot.json",
        help="Destination path for snapshot (default: snapshot.json)",
    )

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.command:
        print("opencode-go-model-optimizer: use --help for usage information", file=sys.stderr)
        return 0

    print(f"opencode-go-model-optimizer: command '{args.command}' called.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

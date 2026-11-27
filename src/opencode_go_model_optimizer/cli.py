"""Command line interface for opencode-go-model-optimizer."""

import argparse
import math
import os
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple

import httpx

from opencode_go_model_optimizer.cli_parser import (
    PROG as _PROG,
)
from opencode_go_model_optimizer.cli_parser import (
    TIERS as _TIERS,
)
from opencode_go_model_optimizer.cli_parser import (
    _parse_token_mix,  # noqa: F401
    _parse_weights,  # noqa: F401
    _split_prefer_item,
    parse_args,
)
from opencode_go_model_optimizer.engine.criteria import SelectionCriteria, passes_privacy
from opencode_go_model_optimizer.engine.diff import diff_snapshots
from opencode_go_model_optimizer.engine.tiers import (
    TierSelection,
    rank_catalog,
    resolve_mode,
    select_tiers,
)
from opencode_go_model_optimizer.formatters import (
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
from opencode_go_model_optimizer.ingest.http import atomic_write_text
from opencode_go_model_optimizer.ingest.models import (
    NormalizedModel,
    Snapshot,
    effective_plan,
    ensure_utc,
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
        then = ensure_utc(datetime.fromisoformat(fetched_at))
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
    plan_name = effective_plan(snap.plan, plan, snap.models)
    return apply_plan(snap.models, plan_name), plan_name


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


def _prepare_selection_context(
    args: argparse.Namespace,
) -> tuple[LoadedSnapshot, list[NormalizedModel], str, str, SelectionCriteria]:
    """Load snapshot, apply effective plan, resolve mode, and build SelectionCriteria."""
    loaded = _load_or_fetch_snapshot(args.snapshot, plan=args.plan, cache_ttl=_cache_ttl(args))
    snap = loaded.snapshot
    plan = effective_plan(snap.plan, args.plan, snap.models)
    models = apply_plan(snap.models, plan)
    mode = resolve_mode(args.mode, models)
    return loaded, models, plan, mode, _criteria_from_args(args, mode)


def handle_select(args: argparse.Namespace) -> int:
    """Handle the 'select' command."""
    loaded, models, plan, mode, criteria = _prepare_selection_context(args)
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
    _, models, _, mode, criteria = _prepare_selection_context(args)
    ranked = rank_catalog(models, criteria)
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

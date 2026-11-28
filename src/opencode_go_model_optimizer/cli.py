"""Command line interface for opencode-go-model-optimizer."""

import argparse
import math
import os
import sys
from collections.abc import Callable
from dataclasses import replace
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
    BenchmarksUnavailableError,
    TierSelection,
    rank_catalog_with_method,
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


def _env_non_negative_int(env_var: str) -> int:
    """Read a non-negative integer env var, returning 0 (with a warning) on invalid values."""
    raw = os.environ.get(env_var, "0")
    try:
        value = int(raw)
    except ValueError:
        value = -1
    if value < 0:
        print(
            f"{_PROG}: warning: invalid {env_var}={raw!r}; using 0",
            file=sys.stderr,
        )
        return 0
    return value


def _env_cache_ttl() -> int:
    """Read OPENCODE_GO_CACHE_TTL, returning 0 (with a warning) on invalid values."""
    return _env_non_negative_int("OPENCODE_GO_CACHE_TTL")


def _cache_ttl(args: argparse.Namespace) -> int:
    """Return --cache-ttl, falling back to OPENCODE_GO_CACHE_TTL when the flag was omitted."""
    return _env_cache_ttl() if args.cache_ttl is None else args.cache_ttl


def _aa_cache_ttl(args: argparse.Namespace) -> int:
    """Return --aa-cache-ttl, falling back to OPENCODE_GO_AA_CACHE_TTL when omitted."""
    val = getattr(args, "aa_cache_ttl", None)
    return _env_non_negative_int("OPENCODE_GO_AA_CACHE_TTL") if val is None else val


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
    aa_cache_ttl: int = 0,
    use_aa: bool = True,
) -> LoadedSnapshot:
    """Retrieve a snapshot from a file if provided, otherwise fetch/cache live."""
    if snapshot_path:
        return LoadedSnapshot(load_snapshot(snapshot_path), "file")

    if cache_ttl > 0 and (cached := load_cached_snapshot(plan=plan, ttl_seconds=cache_ttl)):
        return LoadedSnapshot(cached, "cache")

    try:
        fetch_kwargs: dict[str, object] = {"plan": plan}
        if not use_aa:
            fetch_kwargs["use_aa"] = False
        if aa_cache_ttl > 0:
            fetch_kwargs["aa_cache_ttl"] = aa_cache_ttl
        snap = fetch_and_build_snapshot(**fetch_kwargs)  # type: ignore[arg-type]
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
        allow_fallback=getattr(args, "allow_fallback", False),
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


def _announce_method(
    *,
    method: str,
    requested_mode: str,
    fell_back: bool,
    benchmark_source: str = "none",
    benchmark_error: str | None = None,
) -> None:
    """Loudly announce on stderr which calculation method actually produced the output."""
    if fell_back:
        detail = f" ({benchmark_error})" if benchmark_error else ""
        print(
            f"{_PROG}: WARNING: FALLBACK ACTIVE — requested mode '{requested_mode}', "
            f"calculated with METHOD={method.upper()} "
            f"(Artificial Analysis benchmarks unavailable{detail})",
            file=sys.stderr,
        )
    elif method == "pareto":
        print(
            f"{_PROG}: calculated with METHOD=PARETO "
            f"(Artificial Analysis benchmarks; source={benchmark_source})",
            file=sys.stderr,
        )
    else:
        print(
            f"{_PROG}: calculated with METHOD=ALLOWANCE "
            "(plan allowance & pricing heuristic; no benchmarks)",
            file=sys.stderr,
        )


def _enrich_benchmark_error(
    exc: BenchmarksUnavailableError, benchmark_error: str | None
) -> BenchmarksUnavailableError:
    if not benchmark_error or benchmark_error in str(exc):
        return exc
    return BenchmarksUnavailableError(
        "Pareto mode requires Artificial Analysis benchmarks, but no eligible paid models "
        f"have benchmark data ({benchmark_error}); pass --allow-fallback to fall back to "
        "allowance mode, or use --mode allowance"
    )


def _format_select_output(
    fmt: str,
    loaded: LoadedSnapshot,
    selection: TierSelection,
    criteria: SelectionCriteria,
    plan: str,
    mode: str,
    explain: bool,
    *,
    requested_mode: str | None = None,
    fell_back: bool = False,
) -> str:
    """Format tier selection results according to --format."""
    tiers = selection.primary
    fallbacks = selection.candidates if criteria.top_n > 1 else None
    names = tier_display_names(tiers, selection.models)
    snap = loaded.snapshot
    snapshot_info: dict[str, object] = {
        "source": loaded.source,
        "fetched_at": snap.fetched_at,
        "benchmark_source": snap.metadata.benchmark_source,
    }
    if snap.metadata.benchmark_error:
        snapshot_info["benchmark_error"] = snap.metadata.benchmark_error

    formatters: dict[str, Callable[[], str]] = {
        "json": lambda: format_select_json(
            tiers,
            mode=mode,
            plan=plan,
            explanations=explain_tiers(tiers, selection.models, criteria) if explain else None,
            profile=criteria.profile,
            fallbacks=fallbacks,
            names=names,
            snapshot_info=snapshot_info,
            settings=settings_payload(criteria),
            requested_mode=requested_mode,
            fell_back=fell_back,
        ),
        "table": lambda: format_select_table(
            tiers,
            selection.models,
            plan=plan,
            explain=explain,
            criteria=criteria,
            mode=mode,
            fell_back=fell_back,
        ),
        "env": lambda: format_select_env(
            tiers, fallbacks=fallbacks, names=names, mode=mode, fell_back=fell_back
        ),
        "github-output": lambda: format_select_github_output(
            tiers, fallbacks=fallbacks, names=names, mode=mode, fell_back=fell_back
        ),
        "compact": lambda: format_select_compact(tiers, names=names),
    }
    return formatters[fmt]()


def _prepare_selection_context(
    args: argparse.Namespace,
) -> tuple[LoadedSnapshot, list[NormalizedModel], str, str, SelectionCriteria]:
    """Load snapshot, apply effective plan, resolve mode, and build SelectionCriteria."""
    loaded = _load_or_fetch_snapshot(
        args.snapshot,
        plan=args.plan,
        cache_ttl=_cache_ttl(args),
        aa_cache_ttl=_aa_cache_ttl(args),
        use_aa=(args.mode != "allowance"),
    )
    snap = loaded.snapshot
    plan = effective_plan(snap.plan, args.plan, snap.models)
    models = apply_plan(snap.models, plan)
    if args.mode == "auto" and snap.metadata.benchmark_error:
        mode = "pareto"
    else:
        mode = resolve_mode(args.mode, models)
    return loaded, models, plan, mode, _criteria_from_args(args, mode)


def handle_select(args: argparse.Namespace) -> int:
    """Handle the 'select' command."""
    loaded, models, plan, _, criteria = _prepare_selection_context(args)
    snap = loaded.snapshot
    try:
        selection = select_tiers(models, criteria)
    except BenchmarksUnavailableError as exc:
        raise _enrich_benchmark_error(exc, snap.metadata.benchmark_error) from exc
    actual_method = selection.method
    fell_back = selection.fell_back
    effective_criteria = replace(criteria, mode=actual_method)
    _warn_unmatched_preferred(models, selection, effective_criteria)

    out = _format_select_output(
        args.format,
        loaded=loaded,
        selection=selection,
        criteria=effective_criteria,
        plan=plan,
        mode=actual_method,
        explain=args.explain,
        requested_mode=args.mode,
        fell_back=fell_back,
    )
    _write_output(out, args.output)
    _announce_method(
        method=actual_method,
        requested_mode=args.mode,
        fell_back=fell_back,
        benchmark_source=snap.metadata.benchmark_source,
        benchmark_error=snap.metadata.benchmark_error,
    )

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
    loaded, models, _, _, criteria = _prepare_selection_context(args)
    snap = loaded.snapshot
    try:
        ranked, actual_method, fell_back = rank_catalog_with_method(models, criteria)
    except BenchmarksUnavailableError as exc:
        raise _enrich_benchmark_error(exc, snap.metadata.benchmark_error) from exc
    out = (
        format_rank_json(ranked)
        if args.format == "json"
        else format_rank_table(ranked, mode=actual_method, fell_back=fell_back)
    )
    _write_output(out, args.output)
    _announce_method(
        method=actual_method,
        requested_mode=args.mode,
        fell_back=fell_back,
        benchmark_source=snap.metadata.benchmark_source,
        benchmark_error=snap.metadata.benchmark_error,
    )
    return 0


def handle_diff(args: argparse.Namespace) -> int:
    """Handle the 'diff' command."""
    target_plan = args.plan
    old_snap = load_snapshot(args.old)
    new_snap = _load_or_fetch_snapshot(
        args.new,
        plan=target_plan,
        cache_ttl=_cache_ttl(args),
        aa_cache_ttl=_aa_cache_ttl(args),
        use_aa=(args.mode != "allowance"),
    ).snapshot
    diff_mode = "pareto" if args.mode == "auto" and new_snap.metadata.benchmark_error else args.mode
    try:
        diff = diff_snapshots(
            old_snap,
            new_snap,
            mode=diff_mode,
            plan=target_plan,
            profile=args.profile,
            allow_fallback=args.allow_fallback,
        )
    except BenchmarksUnavailableError as exc:
        raise _enrich_benchmark_error(exc, new_snap.metadata.benchmark_error) from exc
    out = format_diff_json(diff) if args.format == "json" else format_diff_table(diff)
    _write_output(out, args.output)
    _announce_method(
        method=diff.mode,
        requested_mode=args.mode,
        fell_back=diff.fallback,
        benchmark_source=new_snap.metadata.benchmark_source,
        benchmark_error=new_snap.metadata.benchmark_error,
    )
    return 2 if args.fail_on_change and diff.has_changes() else 0


def handle_fetch(args: argparse.Namespace) -> int:
    """Handle the 'fetch' command."""
    target_plan = args.plan
    aa_ttl = _aa_cache_ttl(args)
    snap = fetch_and_build_snapshot(
        plan=target_plan,
        **({"aa_cache_ttl": aa_ttl} if aa_ttl > 0 else {}),  # type: ignore[arg-type]
    )
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

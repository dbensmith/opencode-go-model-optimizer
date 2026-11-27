"""Snapshot generator and persistence for OpenCode Go models."""

import concurrent.futures
import hashlib
import json
import os
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from opencode_go_model_optimizer.ingest.artificial_analysis import (
    AA_MAX_PAGES,
    AA_MODELS_URL,
    _model_aa_lookup_keys,
    _to_float,
    fetch_aa_benchmarks,
    lookup_aa_benchmark,
)
from opencode_go_model_optimizer.ingest.catalogue import (
    DEFAULT_MODELS_HTTP_URL,
    apply_request_estimates,
    fetch_catalogue,
)
from opencode_go_model_optimizer.ingest.console import CONSOLE_PAGE_URL, fetch_console_profiles
from opencode_go_model_optimizer.ingest.docs import (
    DOCS_URLS,
    fetch_docs_mdx,
    norm_name,
    parse_privacy,
)
from opencode_go_model_optimizer.ingest.http import atomic_write_text, client_scope
from opencode_go_model_optimizer.ingest.models import (
    PLANS,
    NormalizedModel,
    Privacy,
    Snapshot,
    SnapshotMetadata,
    is_incomplete_free_model,
    normalize_plan,
    plan_allowance_for,
)
from opencode_go_model_optimizer.ingest.models_dev import MODELS_DEV_URL, fetch_models_dev

__all__ = [
    "AA_MAX_PAGES",
    "AA_MODELS_URL",
    "_model_aa_lookup_keys",
    "_to_float",
    "apply_plan",
    "create_snapshot",
    "fetch_aa_benchmarks",
    "fetch_and_build_snapshot",
    "get_cache_snapshot_path",
    "load_cached_snapshot",
    "load_snapshot",
    "lookup_aa_benchmark",
    "merge_catalogue_metadata",
    "merge_privacy",
    "save_cached_snapshot",
    "save_snapshot",
]


def apply_plan(models: list[NormalizedModel], plan: str) -> list[NormalizedModel]:
    """Apply plan-specific monthly allowances and request estimates to models."""
    norm_plan = normalize_plan(plan)
    for m in models:
        allowance = plan_allowance_for(m, plan)
        if allowance is not None:
            m.monthly_allowance = allowance
        elif m.plan_allowances:
            m.monthly_allowance = None
        m.request_estimate = m.request_estimates.get(norm_plan) or m.request_estimates.get(plan)
    return models


def _lookup_named_row(
    m: NormalizedModel, by_norm: dict[str, dict[str, Any]]
) -> tuple[str, dict[str, Any]] | None:
    """Find a docs row by display name or model ID (normalized first)."""
    for key in (norm_name(m.name), norm_name(m.id), m.id, m.name):
        row = by_norm.get(key)
        if row:
            return key, row
    return None


def merge_catalogue_metadata(
    models: list[NormalizedModel],
    aa_benchmarks: dict[str, dict[str, Any]] | None = None,
    models_dev_metadata: dict[str, dict[str, Any]] | None = None,
) -> list[NormalizedModel]:
    """Attach AA benchmarks and backfill missing metadata from models.dev."""
    benchmarks = aa_benchmarks or {}
    external_metadata = models_dev_metadata or {}
    for m in models:
        key_name = norm_name(m.name)
        key_id = norm_name(m.id)
        metadata = (
            external_metadata.get(m.id)
            or external_metadata.get(key_id)
            or external_metadata.get(key_name)
        )
        if metadata:
            if not m.release_date and metadata.get("release_date"):
                m.release_date = str(metadata["release_date"])
            if m.limit.context == 0 and metadata.get("context"):
                m.limit.context = int(metadata["context"])
            if m.limit.output == 0 and metadata.get("output"):
                m.limit.output = int(metadata["output"])

        bench = lookup_aa_benchmark(m, benchmarks)
        if bench:
            if m.aa_iq is None:
                m.aa_iq = bench.get("iq")
            if m.aa_coding is None:
                m.aa_coding = bench.get("coding")
            if m.speed_tps is None:
                m.speed_tps = bench.get("tps")
    return models


def _merge_privacy(existing: Privacy | None, docs_row: dict[str, Any]) -> Privacy:
    """Combine catalogue Go flags with docs retention/notes, keeping catalogue flags.

    The Go models API is authoritative for training/ZDR/region flags; the docs privacy
    table contributes retention days, notes and agreement dates that the API does not
    publish.
    """
    docs = Privacy(**docs_row)
    if existing is None:
        return docs
    data = existing.model_dump()
    for field in ("trains_on_data", "retention_days", "zdr"):
        if getattr(existing, field) is None:
            value = getattr(docs, field)
            if value is not None:
                data[field] = value
    if docs.requires_global_region:
        data["requires_global_region"] = True
    if docs.note:
        data["note"] = f"{existing.note} {docs.note}" if existing.note else docs.note
    if docs.agreement_valid_until:
        data["agreement_valid_until"] = docs.agreement_valid_until
    return Privacy(**data)


def merge_privacy(
    models: list[NormalizedModel],
    privacy: dict[str, dict[str, Any]],
    warnings: list[str] | None = None,
    now: datetime | None = None,
) -> None:
    """Attach docs privacy rows to models by name or model ID.

    Catalogue-provided Go flags are preserved; docs retention and notes are merged in.
    `warnings`, if given, receives messages about active models without a privacy row and
    expired ZDR agreement dates (relative to `now`).
    """
    by_norm = {norm_name(n): row for n, row in privacy.items() if norm_name(n)}
    missing: list[str] = []
    expired: set[str] = set()
    today = (now or datetime.now(UTC)).date().isoformat()
    for m in models:
        found = _lookup_named_row(m, by_norm)
        if not found:
            if m.status == "active" and m.model_type == "chat" and m.privacy is None:
                missing.append(m.id)
            continue
        m.privacy = _merge_privacy(m.privacy, found[1])
        until = m.privacy.agreement_valid_until
        if until and until < today:
            expired.add(until)
    if warnings is None:
        return
    if missing:
        warnings.append(
            f"{len(missing)} active model(s) have no docs privacy row: "
            + ", ".join(missing)
            + " (and no Go privacy flags)"
        )
    for until in sorted(expired):
        warnings.append(f"privacy agreement date {until} is in the past; docs may be stale")


def _collect_model_warnings(models: list[NormalizedModel], plan: str) -> list[str]:
    """Collect catalogue allowance, anomaly, and multiplier warnings for ``models``."""
    from opencode_go_model_optimizer.engine.pricing import validate_multiplier

    warnings: list[str] = []
    if not any(m.plan_allowances for m in models):
        warnings.append("no per-plan usage from the Go models API; no allowance was applied")
    else:
        norm_plan = normalize_plan(plan)
        no_usage = sorted(
            m.id
            for m in models
            if m.status == "active"
            and m.model_type == "chat"
            and not m.is_zero_cost
            and plan_allowance_for(m, plan) is None
        )
        if no_usage:
            warnings.append(
                f"{len(no_usage)} active paid model(s) have no go.monthly_usage for plan "
                f"{norm_plan}: " + ", ".join(no_usage)
            )
        anomalies = sorted(
            m.id
            for m in models
            if m.status == "active" and m.model_type == "chat" and is_incomplete_free_model(m)
        )
        if anomalies:
            warnings.append(
                f"catalogue anomaly: {len(anomalies)} model(s) publish a monthly allowance "
                "but no token prices: " + ", ".join(anomalies)
            )

    for m in models:
        for mult in m.multipliers:
            reason = validate_multiplier(mult)
            if reason:
                warnings.append(
                    f"ignoring unsupported price multiplier on {m.id} ({reason}); "
                    "prices for it may be understated"
                )
    return warnings


def create_snapshot(
    models: list[NormalizedModel],
    plan: str = "go",
    aa_benchmarks: dict[str, dict[str, Any]] | None = None,
    source_catalogue: str = "live",
    source_docs: str = "live",
    models_dev_metadata: dict[str, dict[str, Any]] | None = None,
    benchmark_source: str = "none",
    warnings: list[str] | None = None,
    privacy: dict[str, dict[str, Any]] | None = None,
    sources: dict[str, dict[str, Any]] | None = None,
) -> Snapshot:
    """Create an immutable normalized Snapshot instance."""
    all_warnings = [*list(warnings or []), *_collect_model_warnings(models, plan)]
    merged_models = merge_catalogue_metadata(
        models,
        aa_benchmarks=aa_benchmarks,
        models_dev_metadata=models_dev_metadata,
    )
    now = datetime.now(UTC)
    now_utc = now.isoformat(timespec="seconds")
    if privacy is not None:
        if not privacy:
            all_warnings.append(
                "docs Privacy table is missing or empty; privacy/region filters may be incomplete"
            )
        merge_privacy(merged_models, privacy, all_warnings, now)

    apply_plan(merged_models, plan)

    return Snapshot(
        schema_version=1,
        fetched_at=now_utc,
        plan=plan,
        models=merged_models,
        metadata=SnapshotMetadata(
            source_catalogue=source_catalogue,
            source_docs=source_docs,
            catalogue_count=len(models),
            benchmark_source=benchmark_source,
            sources=dict(sources or {}),
        ),
        warnings=all_warnings,
    )


def save_snapshot(snapshot: Snapshot, path: str | Path) -> None:
    """Write snapshot to a JSON file."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(p, snapshot.model_dump_json(indent=2))


def load_snapshot(path: str | Path) -> Snapshot:
    """Load and validate snapshot from a JSON file."""
    p = Path(path)
    return Snapshot.model_validate_json(p.read_bytes())


def get_cache_snapshot_path(plan: str = "go") -> Path:
    """Return filesystem path for the local cached snapshot."""
    base = Path(os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache"))
    return base / "opencode-go-model-optimizer" / f"snapshot-{plan}.json"


def _try_load_fresh_snapshot(path: Path, ttl_seconds: float, now: float) -> Snapshot | None:
    """Load a snapshot from ``path`` if it exists and is within ``ttl_seconds``."""
    if not path.exists():
        return None
    try:
        if now - path.stat().st_mtime <= ttl_seconds:
            return load_snapshot(path)
    except OSError, ValueError, json.JSONDecodeError:
        return None
    return None


def load_cached_snapshot(plan: str = "go", ttl_seconds: float = 3600) -> Snapshot | None:
    """Load cached snapshot if within TTL, else return None.

    Pass ``math.inf`` as the TTL to accept a cache file of any age.
    """
    if ttl_seconds <= 0:
        return None

    norm_plan = normalize_plan(plan)
    now = time.time()
    candidates = [plan, *(p for p in PLANS if p != plan and p != norm_plan)]
    for candidate in candidates:
        snap = _try_load_fresh_snapshot(get_cache_snapshot_path(plan=candidate), ttl_seconds, now)
        if snap is None:
            continue
        if candidate == plan:
            apply_plan(snap.models, plan)
            return snap
        if any(plan_allowance_for(m, plan) is not None for m in snap.models):
            apply_plan(snap.models, norm_plan)
            snap.plan = norm_plan
            return snap

    return None


def save_cached_snapshot(snapshot: Snapshot) -> Path | None:
    """Write snapshot to local cache directory and return the path."""
    target_path = get_cache_snapshot_path(plan=snapshot.plan)
    try:
        save_snapshot(snapshot, target_path)
        return target_path
    except OSError:
        return None


_SOURCE_LABELS: dict[str, str] = {
    DEFAULT_MODELS_HTTP_URL: "catalogue",
    **dict.fromkeys(DOCS_URLS, "docs"),
    MODELS_DEV_URL: "models_dev",
    CONSOLE_PAGE_URL: "console",
}


class _SourceRecorder:
    """httpx response hook recording URL, status, time, sha256 and size per source.

    Only the OpenCode/models.dev sources are recorded (never Artificial Analysis), and
    only digests and sizes are kept, never bodies.
    """

    def __init__(self) -> None:
        self.sources: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def __call__(self, response: httpx.Response) -> None:
        label = _SOURCE_LABELS.get(str(response.request.url))
        if label is None:
            return
        body = response.read()
        entry = {
            "url": str(response.request.url),
            "status": response.status_code,
            "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "sha256": hashlib.sha256(body).hexdigest(),
            "bytes": len(body),
        }
        with self._lock:
            previous = self.sources.get(label)
            # Keep a successful fetch over a later failed mirror attempt.
            if previous is None or previous["status"] != 200 or response.status_code == 200:
                self.sources[label] = entry


def fetch_and_build_snapshot(
    plan: str = "go",
    client: httpx.Client | None = None,
    use_aa: bool = True,
) -> Snapshot:
    """Fetch live data from catalogue and docs and build a Snapshot.

    Benchmarks come only from a live Artificial Analysis fetch (needs ``AA_API_KEY`` and
    ``use_aa``); otherwise ``benchmark_source`` is ``"none"``.
    """
    recorder = _SourceRecorder()
    with client_scope(client) as active_client:
        hooks = active_client.event_hooks
        original_hooks = list(hooks.get("response", []))
        hooks["response"] = [*original_hooks, recorder]
        active_client.event_hooks = hooks
        try:
            return _fetch_and_build(plan, active_client, use_aa, recorder)
        finally:
            hooks = active_client.event_hooks
            hooks["response"] = original_hooks
            active_client.event_hooks = hooks


def _format_error_detail(errors: list[str]) -> str:
    """Format a parenthesized error detail suffix, or empty string if no errors."""
    return f" ({'; '.join(errors)})" if errors else ""


def _fetch_and_build(
    plan: str, active_client: httpx.Client, use_aa: bool, recorder: _SourceRecorder
) -> Snapshot:
    aa_key = os.environ.get("AA_API_KEY") if use_aa else None
    aa_errors: list[str] = []
    console_errors: list[str] = []
    with concurrent.futures.ThreadPoolExecutor() as executor:
        fut_cat = executor.submit(fetch_catalogue, client=active_client)
        fut_docs = executor.submit(fetch_docs_mdx, client=active_client)
        fut_models_dev = executor.submit(fetch_models_dev, client=active_client)
        fut_console = executor.submit(
            fetch_console_profiles, client=active_client, errors=console_errors
        )
        fut_aa = (
            executor.submit(fetch_aa_benchmarks, aa_key, client=active_client, errors=aa_errors)
            if aa_key
            else None
        )

        models = fut_cat.result()
        docs_text = fut_docs.result()
        models_dev_metadata = fut_models_dev.result()
        console_profiles = fut_console.result()
        aa_benchmarks = fut_aa.result() if fut_aa is not None else None

    apply_request_estimates(models, console_profiles)

    warnings: list[str] = []
    if console_profiles is None:
        detail = _format_error_detail(console_errors)
        warnings.append(
            "could not read the console request profiles; models without "
            f"go.requests have no request estimates{detail}; quota burn falls back to tokens"
        )
    if aa_key and not aa_benchmarks:
        detail = _format_error_detail(aa_errors)
        warnings.append(
            "AA_API_KEY is set but no Artificial Analysis benchmarks could be fetched"
            f"{detail}; falling back to allowance ranking"
        )

    return create_snapshot(
        models,
        plan=plan,
        aa_benchmarks=aa_benchmarks,
        models_dev_metadata=models_dev_metadata,
        privacy=parse_privacy(docs_text),
        benchmark_source="aa_live" if aa_benchmarks else "none",
        warnings=warnings,
        sources=recorder.sources,
    )

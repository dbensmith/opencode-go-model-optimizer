"""Snapshot generator and persistence for OpenCode Go models."""

from __future__ import annotations

import concurrent.futures
import functools
import json
import math
import os
import time
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path
from typing import Any

import httpx

from opencode_go_model_optimizer.ingest.catalogue import fetch_catalogue
from opencode_go_model_optimizer.ingest.docs import fetch_docs_mdx, norm_name, parse_go_pricing
from opencode_go_model_optimizer.ingest.models import NormalizedModel, Snapshot, SnapshotMetadata
from opencode_go_model_optimizer.ingest.models_dev import fetch_models_dev


def apply_plan(models: list[NormalizedModel], plan: str) -> list[NormalizedModel]:
    """Apply plan-specific monthly allowances to models."""
    norm_plan = plan if plan.startswith("go") else f"go-{plan}"
    for m in models:
        if norm_plan in m.plan_allowances:
            m.monthly_allowance = m.plan_allowances[norm_plan]
        elif plan in m.plan_allowances:
            m.monthly_allowance = m.plan_allowances[plan]
    return models


def _to_float(value: Any) -> float | None:
    """Coerce a value to a finite float, returning None if not numeric."""
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _benchmarks_from_snapshot(snap: Snapshot) -> dict[str, dict[str, Any]]:
    """Build a benchmark index (by id, normalized id and normalized name) from a snapshot."""
    benchmarks: dict[str, dict[str, Any]] = {}
    for m in snap.models:
        if m.aa_iq is None and m.aa_coding is None and m.speed_tps is None:
            continue
        entry = {"iq": m.aa_iq, "coding": m.aa_coding, "tps": m.speed_tps}
        for key in (m.id, norm_name(m.id) if m.id else "", norm_name(m.name) if m.name else ""):
            if key:
                benchmarks[key] = entry
    return benchmarks


def fetch_aa_benchmarks(
    api_key: str,
    client: httpx.Client | None = None,
) -> dict[str, dict[str, Any]]:
    """Fetch live benchmark metrics from Artificial Analysis API."""
    close_client = False
    if client is None:
        client = httpx.Client(
            timeout=10.0,
            headers={"User-Agent": "opencode-go-model-optimizer/0.1.0"},
        )
        close_client = True

    try:
        r = client.get(
            "https://artificialanalysis.ai/api/v2/data/llms/models",
            headers={"x-api-key": api_key},
        )
        r.raise_for_status()
        data = r.json()
        models = data if isinstance(data, list) else data.get("data", [])
        benchmarks: dict[str, dict[str, Any]] = {}
        for item in models:
            if not isinstance(item, dict):
                continue
            slug = item.get("slug") or item.get("id") or ""
            evals = item.get("evaluations")
            if not isinstance(evals, dict):
                evals = {}
            if slug:
                entry = {
                    "iq": _to_float(evals.get("artificial_analysis_intelligence_index")),
                    "coding": _to_float(evals.get("artificial_analysis_coding_index")),
                    "tps": _to_float(item.get("median_output_tokens_per_second")),
                }
                slug = str(slug)
                benchmarks[slug] = entry
                benchmarks[norm_name(slug)] = entry
                if item.get("name") and norm_name(str(item["name"])):
                    benchmarks[norm_name(str(item["name"]))] = entry
        return benchmarks
    except (httpx.HTTPError, ValueError):
        return {}
    finally:
        if close_client:
            client.close()


def merge_docs_pricing(
    models: list[NormalizedModel],
    pricing: dict[str, dict[str, Any]],
    aa_benchmarks: dict[str, dict[str, Any]] | None = None,
    all_plan_pricing: dict[str, dict[str, dict[str, Any]]] | None = None,
    models_dev_metadata: dict[str, dict[str, Any]] | None = None,
) -> list[NormalizedModel]:
    """Merge published monthly allowances and prices into catalog models."""
    benchmarks = aa_benchmarks or {}
    external_metadata = models_dev_metadata or {}
    for m in models:
        key_name = norm_name(m.name)
        key_id = norm_name(m.id)

        info = (
            pricing.get(key_name) or pricing.get(key_id) or pricing.get(m.id) or pricing.get(m.name)
        )
        if info:
            m.monthly_allowance = info["allowance"]
            if m.cost.output == 0.0 and info["output_cost"] > 0:
                m.cost.output = info["output_cost"]
            if m.cost.input == 0.0 and info["input_cost"] > 0:
                m.cost.input = info["input_cost"]
            if m.cost.cache_read == 0.0 and info["cached_read_cost"] > 0:
                m.cost.cache_read = info["cached_read_cost"]
            if m.cost.cache_write == 0.0 and info.get("cached_write_cost", 0) > 0:
                m.cost.cache_write = info["cached_write_cost"]
            m.is_zero_cost = m.cost.input == 0.0 and m.cost.output == 0.0

        if all_plan_pricing:
            for plan_name, plan_map in all_plan_pricing.items():
                plan_info = (
                    plan_map.get(key_name)
                    or plan_map.get(key_id)
                    or plan_map.get(m.id)
                    or plan_map.get(m.name)
                )
                if plan_info:
                    m.plan_allowances[plan_name] = plan_info["allowance"]
        elif m.monthly_allowance is not None and "go" not in m.plan_allowances:
            m.plan_allowances["go"] = m.monthly_allowance

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

        bench = benchmarks.get(m.id) or benchmarks.get(key_id) or benchmarks.get(key_name)
        if bench:
            if m.aa_iq is None:
                m.aa_iq = bench.get("iq")
            if m.aa_coding is None:
                m.aa_coding = bench.get("coding")
            if m.speed_tps is None:
                m.speed_tps = bench.get("tps")

    return models


def create_snapshot(
    models: list[NormalizedModel],
    pricing: dict[str, dict[str, Any]],
    plan: str = "go",
    aa_benchmarks: dict[str, dict[str, Any]] | None = None,
    source_catalogue: str = "live",
    source_docs: str = "live",
    all_plan_pricing: dict[str, dict[str, dict[str, Any]]] | None = None,
    models_dev_metadata: dict[str, dict[str, Any]] | None = None,
) -> Snapshot:
    """Create an immutable normalized Snapshot instance."""
    merged_models = merge_docs_pricing(
        models,
        pricing,
        aa_benchmarks=aa_benchmarks,
        all_plan_pricing=all_plan_pricing,
        models_dev_metadata=models_dev_metadata,
    )
    now_utc = datetime.now(timezone.utc).isoformat(timespec="seconds")

    return Snapshot(
        schema_version=1,
        fetched_at=now_utc,
        plan=plan,
        models=merged_models,
        metadata=SnapshotMetadata(
            source_catalogue=source_catalogue,
            source_docs=source_docs,
            catalogue_count=len(models),
            docs_pricing_count=len(pricing),
        ),
    )


def save_snapshot(snapshot: Snapshot, path: str | Path) -> None:
    """Write snapshot to a JSON file."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(snapshot.model_dump_json(indent=2))


def load_snapshot(path: str | Path) -> Snapshot:
    """Load and validate snapshot from a JSON file."""
    p = Path(path)
    return Snapshot.model_validate_json(p.read_bytes())


def get_reference_snapshot_path() -> Path:
    """Return path to bundled reference snapshot JSON file."""
    ref = files("opencode_go_model_optimizer").joinpath("data", "reference_snapshot.json")
    p = Path(str(ref))
    if p.exists():
        return p
    return Path(__file__).resolve().parent.parent / "data" / "reference_snapshot.json"


@functools.lru_cache(maxsize=1)
def _load_reference_snapshot_cached(path_str: str, mtime_ns: int) -> Snapshot:
    """Internal cached loader for reference snapshot."""
    return load_snapshot(path_str)


def load_reference_snapshot() -> Snapshot | None:
    """Load bundled reference snapshot if present."""
    p = get_reference_snapshot_path()
    if p.exists():
        try:
            stat = p.stat()
            snap = _load_reference_snapshot_cached(str(p), stat.st_mtime_ns)
            return snap.model_copy(deep=True)
        except (json.JSONDecodeError, OSError, ValueError):
            return None
    return None


def get_cache_snapshot_path(plan: str = "go") -> Path:
    """Return filesystem path for the local cached snapshot."""
    base = Path(os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache"))
    return base / "opencode-go-model-optimizer" / f"snapshot-{plan}.json"


def load_cached_snapshot(plan: str = "go", ttl_seconds: int = 3600) -> Snapshot | None:
    """Load cached snapshot if within TTL, else return None."""
    if ttl_seconds <= 0:
        return None

    norm_plan = plan if plan.startswith("go") else f"go-{plan}"
    path = get_cache_snapshot_path(plan=plan)
    if path.exists():
        try:
            if time.time() - path.stat().st_mtime <= ttl_seconds:
                return load_snapshot(path)
        except (OSError, ValueError, json.JSONDecodeError):
            pass

    # Check for another plan's fresh cached snapshot that can share plan allowances
    other_plans = [p for p in ("go", "go-plus", "go-teams") if p != plan and p != norm_plan]
    now = time.time()
    for other_p in other_plans:
        other_path = get_cache_snapshot_path(plan=other_p)
        if other_path.exists():
            try:
                if now - other_path.stat().st_mtime <= ttl_seconds:
                    snap = load_snapshot(other_path)
                    if any(
                        norm_plan in m.plan_allowances or plan in m.plan_allowances
                        for m in snap.models
                    ):
                        apply_plan(snap.models, norm_plan)
                        snap.plan = norm_plan
                        return snap
            except (OSError, ValueError, json.JSONDecodeError):
                continue

    return None


def save_cached_snapshot(snapshot: Snapshot) -> Path | None:
    """Write snapshot to local cache directory and return the path."""
    target_path = get_cache_snapshot_path(plan=snapshot.plan)
    try:
        save_snapshot(snapshot, target_path)
        return target_path
    except OSError:
        return None


def fetch_and_build_snapshot(
    plan: str = "go",
    client: httpx.Client | None = None,
    use_reference_benchmarks: bool = True,
) -> Snapshot:
    """Fetch live data from catalogue and docs and build a Snapshot."""
    close_client = False
    active_client = client
    if active_client is None:
        active_client = httpx.Client(
            timeout=10.0,
            headers={"User-Agent": "opencode-go-model-optimizer/0.1.0"},
        )
        close_client = True

    try:
        aa_key = os.environ.get("AA_API_KEY")
        with concurrent.futures.ThreadPoolExecutor() as executor:
            fut_cat = executor.submit(fetch_catalogue, client=active_client)
            fut_docs = executor.submit(fetch_docs_mdx, client=active_client)
            fut_models_dev = executor.submit(fetch_models_dev, client=active_client)
            fut_aa = (
                executor.submit(fetch_aa_benchmarks, aa_key, client=active_client)
                if aa_key
                else None
            )

            models = fut_cat.result()
            docs_text = fut_docs.result()
            models_dev_metadata = fut_models_dev.result()
            aa_benchmarks = fut_aa.result() if fut_aa is not None else None

        all_plan_pricing = {}
        for p in ("go", "go-plus", "go-teams"):
            parsed = parse_go_pricing(docs_text, plan=p, fallback_to_go=False)
            if parsed:
                all_plan_pricing[p] = parsed
        pricing = all_plan_pricing.get(plan) or parse_go_pricing(docs_text, plan=plan)

        if not aa_benchmarks and use_reference_benchmarks:
            ref_snap = load_reference_snapshot()
            if ref_snap:
                aa_benchmarks = _benchmarks_from_snapshot(ref_snap)

        return create_snapshot(
            models,
            pricing,
            plan=plan,
            aa_benchmarks=aa_benchmarks,
            all_plan_pricing=all_plan_pricing,
            models_dev_metadata=models_dev_metadata,
        )
    finally:
        if close_client and active_client:
            active_client.close()


def update_reference_snapshot(
    plan: str = "go",
    client: httpx.Client | None = None,
) -> Path:
    """Fetch live data and update the repository reference snapshot file."""
    snap = fetch_and_build_snapshot(plan=plan, client=client, use_reference_benchmarks=True)
    target_path = get_reference_snapshot_path()
    save_snapshot(snap, target_path)
    return target_path

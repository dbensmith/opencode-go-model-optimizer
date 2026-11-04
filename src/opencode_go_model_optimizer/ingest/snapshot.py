"""Snapshot generator and persistence for OpenCode Go models."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from opencode_go_model_optimizer.ingest.catalogue import fetch_catalogue
from opencode_go_model_optimizer.ingest.docs import fetch_docs_mdx, norm_name, parse_go_pricing
from opencode_go_model_optimizer.ingest.models import NormalizedModel, Snapshot, SnapshotMetadata


def fetch_aa_benchmarks(
    api_key: str,
    client: httpx.Client | None = None,
) -> dict[str, dict[str, Any]]:
    """Fetch live benchmark metrics from Artificial Analysis API."""
    close_client = False
    if client is None:
        client = httpx.Client(
            timeout=10.0,
            headers={"x-api-key": api_key, "User-Agent": "opencode-go-model-optimizer/0.1.0"},
        )
        close_client = True

    try:
        r = client.get("https://artificialanalysis.ai/api/v2/data/llms/models")
        r.raise_for_status()
        data = r.json()
        models = data if isinstance(data, list) else data.get("data", [])
        benchmarks: dict[str, dict[str, Any]] = {}
        for item in models:
            if not isinstance(item, dict):
                continue
            slug = item.get("slug") or item.get("id") or ""
            evals = item.get("evaluations", {})
            iq = evals.get("artificial_analysis_intelligence_index")
            coding = evals.get("artificial_analysis_coding_index")
            tps = item.get("median_output_tokens_per_second")
            if slug:
                benchmarks[slug] = {
                    "iq": float(iq) if iq is not None else None,
                    "coding": float(coding) if coding is not None else None,
                    "tps": float(tps) if tps is not None else None,
                }
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
) -> list[NormalizedModel]:
    """Merge published monthly allowances and prices into catalog models."""
    benchmarks = aa_benchmarks or {}
    for m in models:
        key_name = norm_name(m.name)
        key_id = norm_name(m.id)

        info = pricing.get(key_name) or pricing.get(key_id)
        if info:
            m.monthly_allowance = info["allowance"]
            if m.cost.output == 0.0 and info["output_cost"] > 0:
                m.cost.output = info["output_cost"]
            if m.cost.input == 0.0 and info["input_cost"] > 0:
                m.cost.input = info["input_cost"]
            if m.cost.cache_read == 0.0 and info["cached_read_cost"] > 0:
                m.cost.cache_read = info["cached_read_cost"]

        bench = benchmarks.get(m.id) or benchmarks.get(key_id)
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
) -> Snapshot:
    """Create an immutable normalized Snapshot instance."""
    merged_models = merge_docs_pricing(models, pricing, aa_benchmarks=aa_benchmarks)
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
    with open(p, "r", encoding="utf-8") as f:
        data = json.load(f)
    return Snapshot.model_validate(data)


def fetch_and_build_snapshot(
    plan: str = "go",
    client: httpx.Client | None = None,
) -> Snapshot:
    """Fetch live data from catalogue and docs and build a Snapshot."""
    models = fetch_catalogue(client=client)
    docs_text = fetch_docs_mdx(client=client)
    pricing = parse_go_pricing(docs_text, plan=plan)

    aa_benchmarks = None
    aa_key = os.environ.get("AA_API_KEY")
    if aa_key:
        aa_benchmarks = fetch_aa_benchmarks(aa_key, client=client)

    return create_snapshot(models, pricing, plan=plan, aa_benchmarks=aa_benchmarks)

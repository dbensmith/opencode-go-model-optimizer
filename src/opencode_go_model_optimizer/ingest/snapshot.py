"""Snapshot generator and persistence for OpenCode Go models."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from opencode_go_model_optimizer.ingest.catalogue import fetch_catalogue
from opencode_go_model_optimizer.ingest.docs import fetch_docs_mdx, norm_name, parse_go_pricing
from opencode_go_model_optimizer.ingest.models import NormalizedModel, Snapshot, SnapshotMetadata

# Known reference benchmarks from Artificial Analysis (used as reference/fallback)
KNOWN_AA_BENCHMARKS: dict[str, dict[str, Any]] = {
    "kimi-k3.1-pro": {"iq": 55.0, "coding": None, "tps": 73.0},
    "gemini-4-argon": {"iq": 53.0, "coding": 53.0, "tps": 79.1},
    "muse-spark-1.3-contributor": {"iq": 48.0, "coding": None, "tps": 125.0},
    "mimo-v2.6-pro": {"iq": 46.0, "coding": None, "tps": 55.0},
    "grok-4.7": {"iq": 46.0, "coding": 47.0, "tps": 96.6},
    "deepseek-v4.1-flash": {"iq": 45.0, "coding": 46.0, "tps": 195.0},
    "glm-5.3": {"iq": 45.0, "coding": 49.0, "tps": 85.0},
    "qwen3.8-max": {"iq": 45.0, "coding": None, "tps": 42.0},
    "step-5-preview": {"iq": 44.0, "coding": None, "tps": 229.0},
    "claude-haiku-5-5": {"iq": 43.0, "coding": None, "tps": 235.6},
    "minimax-m3.1-flash": {"iq": 43.0, "coding": None, "tps": 96.0},
    "glm-5.3-flash": {"iq": 42.0, "coding": None, "tps": 69.0},
    "qwen3.8-flash": {"iq": 40.0, "coding": None, "tps": 52.0},
    "mimo-v2.6-flash": {"iq": 38.0, "coding": None, "tps": 59.0},
    "gpt-6-luna": {"iq": 38.0, "coding": None, "tps": 137.0},
    "beam-free": {"iq": 30.0, "coding": 20.0, "tps": 337.0},
    "hy3": {"iq": 25.0, "coding": None, "tps": 82.4},
    "longcat-2.0": {"iq": 19.0, "coding": None, "tps": 76.8},
}


def merge_docs_pricing(
    models: list[NormalizedModel],
    pricing: dict[str, dict[str, Any]],
) -> list[NormalizedModel]:
    """Merge published monthly allowances and prices into catalog models."""
    for m in models:
        # Match by normalized name or normalized id
        key_name = norm_name(m.name)
        key_id = norm_name(m.id)

        info = pricing.get(key_name) or pricing.get(key_id)
        if info:
            m.monthly_allowance = info["allowance"]
            # If catalog had 0 cost or missing cost, backfill from docs
            if m.cost.output == 0.0 and info["output_cost"] > 0:
                m.cost.output = info["output_cost"]
            if m.cost.input == 0.0 and info["input_cost"] > 0:
                m.cost.input = info["input_cost"]
            if m.cost.cache_read == 0.0 and info["cached_read_cost"] > 0:
                m.cost.cache_read = info["cached_read_cost"]

        # Backfill reference AA metrics if available and not set
        bench = KNOWN_AA_BENCHMARKS.get(m.id)
        if bench:
            if m.aa_iq is None:
                m.aa_iq = bench["iq"]
            if m.aa_coding is None:
                m.aa_coding = bench["coding"]
            if m.speed_tps is None:
                m.speed_tps = bench["tps"]

    return models


def create_snapshot(
    models: list[NormalizedModel],
    pricing: dict[str, dict[str, Any]],
    plan: str = "go",
    source_catalogue: str = "live",
    source_docs: str = "live",
) -> Snapshot:
    """Create an immutable normalized Snapshot instance."""
    merged_models = merge_docs_pricing(models, pricing)
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
    return create_snapshot(models, pricing, plan=plan)

"""Publishable results: per-plan tier selections with how and why they were decided.

The report is built from a live fetch (never the cache) in allowance mode, so it contains no
Artificial Analysis data. Source provenance carries only URL, status, time, sha256 and size.
"""

import json
import math
import os
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from opencode_go_model_optimizer.engine.allowance import is_eligible_free
from opencode_go_model_optimizer.engine.criteria import (
    SelectionCriteria,
    _is_free_candidate,
    passes_privacy,
)
from opencode_go_model_optimizer.engine.tiers import (
    PAID_TIERS,
    STANDARD_TIERS,
    select_tiers,
)
from opencode_go_model_optimizer.formatters import explain_tiers, settings_payload
from opencode_go_model_optimizer.ingest.http import atomic_write_text, package_version
from opencode_go_model_optimizer.ingest.models import (
    PLANS,
    NormalizedModel,
    Snapshot,
    is_incomplete_free_model,
)
from opencode_go_model_optimizer.ingest.snapshot import apply_plan, fetch_and_build_snapshot
from opencode_go_model_optimizer.numbers import json_allowance, json_multiplier
from opencode_go_model_optimizer.report_markdown import (
    _bucket_exclusion_reason,
    _fmt_cost,
    _format_allowance,
    _format_cluster_range,
    _round_chart_num,
    render_markdown,
)

SCHEMA_VERSION = 1
TOP_N = 3
# Explanation keys that derive from Artificial Analysis or are meaningless without it.
_DROP_KEYS = {"aa_iq", "aa_coding", "speed_tps", "bpe", "is_pareto", "effective_quality"}

HOW_DECIDED = [
    "Mode: allowance. No benchmark data is used; models are ranked by published plan allowance "
    "and price only.",
    "Sources: per-plan allowance and the training/ZDR flags come from the Go models API "
    "(`go.monthly_usage`, `go.training`, `go.zdr`); token prices come from the same catalogue. "
    "The docs Privacy table supplies retention days and agreement notes, and explicit docs text "
    "supplies the workspace Global-region requirement. `go.limited_regions` is recorded as "
    "advisory only, because regional availability is not the same as the Global setting.",
    "Eligibility: only active text (chat) models are considered. Models without a positive finite "
    "allowance are excluded from the paid tiers. OpenCode Go privacy defaults apply: "
    "Global-region models are blocked, paid endpoints that train on request data are blocked, "
    "and free endpoints that train are blocked. Preview models are included. Retention is "
    "unrestricted unless a retention/ZDR flag is set. A model that publishes a monthly "
    "allowance but no token prices is treated as incomplete catalogue data and excluded from "
    "every tier.",
    "Price: catalogue token prices are scaled by each model's price multiplier (peak hours, "
    "promo expiry) using the 7-day time-weighted average starting at generation time.",
    "Tiers: eligible paid models are split into three clusters by log effective output price "
    "(natural breaks): the dearest cluster is heavy, the middle balanced, the cheapest light.",
    "Ranking inside a cluster: highest monthly allowance first, then lowest effective output "
    "price, then newest release date, then largest context window, then model id.",
    "Free tier: zero-cost (or contributor/unlimited) models, newest release first, then "
    "largest context window, then model id.",
    "Quota burn: percent of the monthly allowance consumed per 1,000 turns, using the "
    "console's per-model token profile to estimate requests per month (scaled by the price "
    "multiplier). Shown for context; it is not a ranking key in allowance mode.",
    f"Fallbacks: the next {TOP_N - 1} candidates per tier, drawn from the same cluster and "
    "then from the general paid ranking.",
]


def _strip_aa(value: Any) -> Any:
    """Recursively drop Artificial Analysis derived keys."""
    if isinstance(value, dict):
        return {
            k: _strip_aa(v)
            for k, v in value.items()
            if k not in _DROP_KEYS and not str(k).startswith("aa_")
        }
    if isinstance(value, list):
        return [_strip_aa(v) for v in value]
    return value


def assert_no_aa(value: Any, path: str = "$") -> None:
    """Raise if any key looks like Artificial Analysis data (``aa_*`` or benchmark metrics)."""
    if isinstance(value, dict):
        for k, v in value.items():
            if str(k).startswith("aa_") or k in _DROP_KEYS:
                raise ValueError(f"report contains forbidden benchmark key {path}.{k}")
            assert_no_aa(v, f"{path}.{k}")
    elif isinstance(value, list):
        for i, v in enumerate(value):
            assert_no_aa(v, f"{path}[{i}]")


def exclusion_reasons(m: NormalizedModel, c: SelectionCriteria) -> list[str]:
    """Why a model cannot take a paid or free tier slot (empty when it is eligible)."""
    if m.status != "active":
        return [f"status is {m.status!r} (not active)"]
    if m.model_type != "chat":
        return [f"not a text model (type {m.model_type!r})"]
    if is_incomplete_free_model(m):
        return ["zero-priced but publishes a monthly allowance (incomplete catalogue data)"]
    reasons: list[str] = []
    p = m.privacy
    if not passes_privacy(m, c):
        if p is not None and p.requires_global_region and not c.allow_global_region:
            reasons.append("requires Global workspace region (enable --allow-global-region)")
        elif p is not None and p.trains_on_data:
            if _is_free_candidate(m):
                reasons.append("free model trains on request data (enable --allow-free-training)")
            else:
                reasons.append("paid model trains on request data (enable --allow-paid-training)")
        else:
            reasons.append("excluded by privacy criteria")
    if reasons:
        return reasons
    if is_eligible_free(m):
        return []
    allowance = m.monthly_allowance
    if allowance is None:
        reasons.append("unpriced: no monthly allowance published for this plan")
    elif allowance <= 0:
        reasons.append("unpriced: allowance is zero")
    elif math.isinf(allowance):
        reasons.append("unlimited allowance on a priced model (not eligible for paid tiers)")
    return reasons


def _candidate_row(m: NormalizedModel) -> dict[str, Any]:
    return {
        "model_id": m.full_id,
        "monthly_allowance": json_allowance(m.monthly_allowance, compact=True),
        "output_cost_per_1m": round(m.cost.output, 6),
        "price_multiplier": json_multiplier(m.price_multiplier, default=1),
        "release_date": m.release_date or None,
        "context": m.limit.context,
    }


def _plan_section(snap: Snapshot, plan: str, now: datetime) -> tuple[dict[str, Any], list[str]]:
    """Run default allowance-mode selection (top 3) for a plan and explain the outcome."""
    models = apply_plan([m.model_copy(deep=True) for m in snap.models], plan)
    criteria = SelectionCriteria(mode="allowance", top_n=TOP_N, at=now)
    selection = select_tiers(models, criteria)
    pool = selection.models
    by_full = {m.full_id: m for m in pool}
    explanations = _strip_aa(explain_tiers(selection.primary, pool, criteria))

    warnings = [
        f"[{plan}] no model available for tier {t}"
        for t in STANDARD_TIERS
        if not selection.primary[t]
    ]

    clusters = selection.clusters
    tiers: dict[str, Any] = {}
    for tier in STANDARD_TIERS:
        pick = selection.primary[tier]
        entry: dict[str, Any] = {
            "model_id": pick or None,
            "fallbacks": [f for f in selection.candidates[tier] if f != pick],
            "explanation": explanations.get(tier, {}),
        }
        candidates = [by_full[f] for f in selection.candidates[tier] if f in by_full]
        entry["candidates"] = [_candidate_row(m) for m in candidates]
        if tier in PAID_TIERS:
            costs = [m.cost.output for m in clusters[tier]]
            entry["cluster"] = {
                "size": len(costs),
                "output_cost_per_1m_range": [round(min(costs), 6), round(max(costs), 6)]
                if costs
                else None,
            }
            entry["why"] = (
                f"{pick} has the highest allowance among eligible models in the {tier} price "
                "cluster, with ties broken by lower output price, newer release, larger context"
                if pick
                else f"no eligible model in the {tier} cluster"
            )
        else:
            entry["why"] = (
                f"{pick} is the newest eligible zero-cost model (then larger context, then id)"
                if pick
                else "no eligible free model"
            )
        tiers[tier] = entry

    excluded = []
    for m in sorted(models, key=lambda x: x.id):
        reasons = exclusion_reasons(m, criteria)
        if reasons:
            excluded.append({"model_id": m.full_id, "reasons": reasons})

    section = {
        "plan": plan,
        "mode": "allowance",
        "criteria": settings_payload(criteria),
        "tiers": {t: tiers[t]["model_id"] or "" for t in STANDARD_TIERS},
        "fallbacks": {t: tiers[t]["fallbacks"] for t in STANDARD_TIERS},
        "explanations": tiers,
        "excluded": excluded,
    }
    return section, warnings


def summarize_junit(paths: list[str]) -> dict[str, Any]:
    """Summarise JUnit XML files into pass/fail/skip counts and failing test names."""
    runs = []
    for path in paths:
        name = Path(path).stem
        counts = {"passed": 0, "failed": 0, "skipped": 0}
        failing: list[str] = []
        try:
            root = ET.parse(path).getroot()
        except (OSError, ET.ParseError) as exc:
            runs.append({"name": name, **counts, "failing": [], "error": f"unreadable: {exc}"})
            continue
        for case in root.iter("testcase"):
            if case.find("failure") is not None or case.find("error") is not None:
                counts["failed"] += 1
                failing.append(f"{case.get('classname', '')}::{case.get('name', '')}")
            elif case.find("skipped") is not None:
                counts["skipped"] += 1
            else:
                counts["passed"] += 1
        runs.append({"name": name, **counts, "failing": failing})
    failed = sum(r["failed"] for r in runs) + sum(1 for r in runs if "error" in r)
    return {"result": "fail" if failed else "pass", "runs": runs}


def build_report(
    junit_paths: list[str] | None = None,
    snapshot: Snapshot | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build the results document from a live fetch (or the given snapshot)."""
    now = now or datetime.now(UTC)
    snap = snapshot or fetch_and_build_snapshot(plan="go", use_aa=False)
    warnings = list(snap.warnings)
    plans: dict[str, Any] = {}
    for plan in PLANS:
        plans[plan], plan_warnings = _plan_section(snap, plan, now)
        warnings.extend(plan_warnings)
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now.isoformat(timespec="seconds"),
        "tool_version": package_version(),
        "git_sha": os.environ.get("GITHUB_SHA"),
        "plans": plans,
        "warnings": warnings,
        "provenance": {
            "sources": snap.metadata.sources,
            "note": "sha256 and byte length of each raw response body; bodies are not published",
        },
        "how_decided": HOW_DECIDED,
        "tests": summarize_junit(junit_paths) if junit_paths else None,
    }
    assert_no_aa(report)
    return report


def write_report(report: dict[str, Any], out_dir: str | Path) -> None:
    """Write results.json and README.md into ``out_dir``."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    atomic_write_text(out / "results.json", json.dumps(report, indent=2) + "\n")
    atomic_write_text(out / "README.md", render_markdown(report))


__all__ = [
    "HOW_DECIDED",
    "SCHEMA_VERSION",
    "TOP_N",
    "_bucket_exclusion_reason",
    "_candidate_row",
    "_fmt_cost",
    "_format_allowance",
    "_format_cluster_range",
    "_round_chart_num",
    "_strip_aa",
    "assert_no_aa",
    "build_report",
    "exclusion_reasons",
    "render_markdown",
    "summarize_junit",
    "write_report",
]

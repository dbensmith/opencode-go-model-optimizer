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

from opencode_go_model_optimizer.engine.allowance import rank_paid_models
from opencode_go_model_optimizer.engine.criteria import SelectionCriteria, passes_privacy
from opencode_go_model_optimizer.engine.tiers import (
    STANDARD_TIERS,
    _allowance_clusters,
    select_tiers,
)
from opencode_go_model_optimizer.formatters.output import explain_tiers
from opencode_go_model_optimizer.ingest.http import atomic_write_text, package_version
from opencode_go_model_optimizer.ingest.models import NormalizedModel, Snapshot
from opencode_go_model_optimizer.ingest.snapshot import apply_plan, fetch_and_build_snapshot

SCHEMA_VERSION = 1
PLANS = ("go", "go-plus", "go-teams")
TOP_N = 3
_PAID = ("heavy", "balanced", "light")
# Explanation keys that derive from Artificial Analysis or are meaningless without it.
_DROP_KEYS = {"aa_iq", "aa_coding", "speed_tps", "bpe", "is_pareto"}

HOW_DECIDED = [
    "Mode: allowance. No benchmark data is used; models are ranked by published plan allowance "
    "and price only.",
    "Eligibility: only active models are considered. Preview/experimental models, models "
    "requiring a Global workspace region, and models without a positive finite allowance are "
    "excluded from the paid tiers (the privacy filter here is the default: Global-region "
    "models excluded, training and retention not restricted).",
    "Price: catalogue token prices are scaled by each model's price multiplier (peak hours, "
    "promo expiry) using the 7-day time-weighted average starting at generation time.",
    "Tiers: eligible paid models are split into three clusters by log effective output price "
    "(natural breaks): the dearest cluster is heavy, the middle balanced, the cheapest light.",
    "Ranking inside a cluster: highest monthly allowance first, then lowest effective output "
    "price, then newest release date, then largest context window, then model id.",
    "Free tier: zero-cost (or contributor/unlimited) models, newest release first, then "
    "largest context window, then model id.",
    "Quota burn: percent of the monthly allowance consumed per 1,000 turns, from the docs "
    "request estimates when published (scaled by the price multiplier), otherwise from a "
    "synthetic token mix. Shown for context; it is not a ranking key in allowance mode.",
    f"Fallbacks: the next {TOP_N - 1} candidates per tier, drawn from the same cluster and "
    "then from the general paid ranking.",
]


def _num(value: float | None) -> float | int | str | None:
    if value is None:
        return None
    if math.isinf(value):
        return "unlimited"
    return int(value) if float(value).is_integer() else round(value, 6)


def _strip_aa(value: Any) -> Any:
    """Recursively drop Artificial Analysis derived keys."""
    if isinstance(value, dict):
        return {k: _strip_aa(v) for k, v in value.items() if k not in _DROP_KEYS}
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
    reasons: list[str] = []
    if m.is_preview:
        reasons.append("preview/experimental model")
    p = m.privacy
    if p is not None and not passes_privacy(m, c):
        if p.requires_global_region and not c.allow_global_region:
            reasons.append("requires Global workspace region (excluded by default)")
        else:
            reasons.append("excluded by privacy criteria")
    if reasons:
        return reasons
    free = m.is_zero_cost or (m.is_contributor and (m.monthly_allowance == math.inf))
    if free:
        return []
    allowance = m.monthly_allowance
    if allowance is None:
        reasons.append("unpriced: no docs pricing row for this plan")
    elif allowance <= 0:
        reasons.append("unpriced: allowance is zero")
    elif math.isinf(allowance):
        reasons.append("unlimited allowance on a priced model (not eligible for paid tiers)")
    elif m.is_contributor:
        reasons.append("contributor model with a finite allowance")
    return reasons


def _candidate_row(m: NormalizedModel) -> dict[str, Any]:
    return {
        "model_id": m.full_id,
        "monthly_allowance": _num(m.monthly_allowance),
        "output_cost_per_1m": round(m.cost.output, 6),
        "price_multiplier": round(m.price_multiplier, 4) if m.price_multiplier is not None else 1,
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

    clusters = _allowance_clusters(rank_paid_models(pool, skip_models=criteria.skip_models))
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
        if tier in _PAID:
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
        "criteria": {
            "top_n": TOP_N,
            "price_basis": criteria.price_basis,
            "burn_basis": criteria.burn_basis,
            "profile": criteria.profile,
            "allow_global_region": criteria.allow_global_region,
            "allow_training": criteria.allow_training,
            "allow_contributor": criteria.allow_contributor,
            "at": now.isoformat(timespec="seconds"),
        },
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


def _fmt_cost(value: Any) -> str:
    return "-" if value is None else f"${value:g}"


def render_markdown(report: dict[str, Any]) -> str:
    """Render the report as human-readable Markdown."""
    lines = [
        "# OpenCode Go model tiers",
        "",
        f"Generated {report['generated_at']} by opencode-go-model-optimizer "
        f"{report['tool_version']}"
        + (f" (commit `{report['git_sha']}`)" if report["git_sha"] else "")
        + ". Allowance mode, default criteria, top 3 per tier.",
        "",
    ]
    for plan, section in report["plans"].items():
        lines += [f"## Plan: {plan}", ""]
        lines += [
            "| Tier | Pick | Allowance | Out $/1M | Price x | Burn %/1k turns | Basis | Fallbacks |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        for tier, entry in section["explanations"].items():
            ex = entry["explanation"]
            fb = ", ".join(f"`{f}`" for f in entry["fallbacks"]) or "-"
            lines.append(
                f"| {tier} | `{entry['model_id'] or '-'}` | {ex.get('monthly_allowance', '-')} "
                f"| {_fmt_cost(ex.get('output_cost_per_1m'))} | {ex.get('price_multiplier', 1)} "
                f"| {ex.get('quota_burn_pct', '-')} | {ex.get('burn_basis_used', '-')} | {fb} |"
            )
        lines += ["", "Why:", ""]
        for tier, entry in section["explanations"].items():
            lines.append(f"- **{tier}**: {entry['why']}")
            cluster = entry.get("cluster")
            if cluster and cluster["output_cost_per_1m_range"]:
                lo, hi = cluster["output_cost_per_1m_range"]
                lines.append(
                    f"  - cluster of {cluster['size']} model(s), effective output "
                    f"${lo:g}-${hi:g}/1M"
                )
            ex = entry["explanation"]
            if ex.get("privacy"):
                p = ex["privacy"]
                lines.append(
                    f"  - privacy: trains_on_data={p.get('trains_on_data')}, "
                    f"retention_days={p.get('retention_days')}, zdr={p.get('zdr')}, "
                    f"global_region_required={p.get('requires_global_region')}"
                )
        lines += ["", "<details><summary>Excluded models</summary>", ""]
        if section["excluded"]:
            lines += ["| Model | Reason |", "| --- | --- |"]
            lines += [
                f"| `{e['model_id']}` | {'; '.join(e['reasons'])} |" for e in section["excluded"]
            ]
        else:
            lines.append("None.")
        lines += ["", "</details>", ""]

    lines += ["## How this was decided", ""]
    lines += [f"- {item}" for item in report["how_decided"]]
    lines += ["", "## Warnings", ""]
    lines += [f"- {w}" for w in report["warnings"]] or ["None."]
    lines += ["", "## Provenance", "", "| Source | URL | HTTP | Fetched | sha256 | Bytes |"]
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for name, src in report["provenance"]["sources"].items():
        lines.append(
            f"| {name} | {src['url']} | {src['status']} | {src['fetched_at']} "
            f"| `{src['sha256'][:16]}...` | {src['bytes']} |"
        )
    lines += ["", report["provenance"]["note"] + ". Full digests are in results.json.", ""]
    lines += ["## Test results", ""]
    tests = report["tests"]
    if tests is None:
        lines.append("No test results supplied.")
    else:
        lines += [f"Overall: **{tests['result']}**", ""]
        lines += ["| Run | Passed | Failed | Skipped |", "| --- | --- | --- | --- |"]
        for run in tests["runs"]:
            lines.append(
                f"| {run['name']} | {run['passed']} | {run['failed']} | {run['skipped']} |"
            )
            if run.get("error"):
                lines.append(f"| {run['name']} | {run['error']} | | |")
        failing = [f"`{t}`" for run in tests["runs"] for t in run["failing"]]
        if failing:
            lines += ["", "Failing tests: " + ", ".join(failing)]
    return "\n".join(lines) + "\n"


def write_report(report: dict[str, Any], out_dir: str | Path) -> None:
    """Write results.json and README.md into ``out_dir``."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    atomic_write_text(out / "results.json", json.dumps(report, indent=2) + "\n")
    atomic_write_text(out / "README.md", render_markdown(report))

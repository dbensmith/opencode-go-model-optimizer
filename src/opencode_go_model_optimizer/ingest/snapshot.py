"""Snapshot generator and persistence for OpenCode Go models."""

import concurrent.futures
import hashlib
import json
import math
import os
import re
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from opencode_go_model_optimizer.ingest.catalogue import DEFAULT_MODELS_HTTP_URL, fetch_catalogue
from opencode_go_model_optimizer.ingest.docs import (
    DOCS_URLS,
    fetch_docs_mdx,
    norm_name,
    parse_endpoints_table,
    parse_go_pricing,
    parse_privacy,
    parse_request_estimates,
)
from opencode_go_model_optimizer.ingest.http import atomic_write_text, client_scope
from opencode_go_model_optimizer.ingest.models import (
    PLANS,
    NormalizedModel,
    Privacy,
    RequestEstimate,
    Snapshot,
    SnapshotMetadata,
    normalize_plan,
)
from opencode_go_model_optimizer.ingest.models_dev import MODELS_DEV_URL, fetch_models_dev


def apply_plan(models: list[NormalizedModel], plan: str) -> list[NormalizedModel]:
    """Apply plan-specific monthly allowances to models."""
    norm_plan = normalize_plan(plan)
    for m in models:
        if norm_plan in m.plan_allowances:
            m.monthly_allowance = m.plan_allowances[norm_plan]
        elif plan in m.plan_allowances:
            m.monthly_allowance = m.plan_allowances[plan]
        m.request_estimate = estimate_for(m, norm_plan)
    return models


def estimate_for(m: NormalizedModel, plan: str) -> RequestEstimate | None:
    """Return the docs request estimate for a model on a plan (None if unpublished)."""
    norm_plan = normalize_plan(plan)
    return m.request_estimates.get(norm_plan) or m.request_estimates.get(plan)


def _to_float(value: Any) -> float | None:
    """Coerce a value to a finite float, returning None if not numeric."""
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except TypeError, ValueError:
        return None
    return result if math.isfinite(result) else None


AA_MODELS_URL = "https://artificialanalysis.ai/api/v2/language/models/free"
AA_MAX_PAGES = 20

_AA_EFFORT_TAG_RE = re.compile(
    r"\(\s*(?:max|xhigh|high|medium|low|minimal|non-reasoning)(?:\s*,[^)]*)?\s*\)",
    re.IGNORECASE,
)
_AA_EFFORT_SLUG_RE = re.compile(r"-(?:xhigh|high|medium|low|minimal|non-reasoning)$", re.IGNORECASE)
_AA_ALL_EFFORT_SLUG_RE = re.compile(
    r"-(?:max|xhigh|high|medium|low|minimal|non-reasoning)$", re.IGNORECASE
)
_AA_DATE_CODE_RE = re.compile(r"(?:^|[\s_-])(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])(?=$|[\s_-])")
_AA_PARAM_SIZE_RE = re.compile(r"(?:[\s_-]+(?:a?\d+(?:\.\d+)?b))+$", re.IGNORECASE)
_AA_EDITION_RE = re.compile(r"(?:[\s_-]+(?:contributor|free|preview|next))+$", re.IGNORECASE)
_AA_TRAILING_ONE_DOT_ZERO_RE = re.compile(r"(?:[\s_-]+1(?:\.0|-0))$")


def _aa_speed_tps(item: dict[str, Any]) -> float | None:
    """Output tokens/sec from performance_v3, then the deprecated performance block."""
    perf_v3 = item.get("performance_v3")
    if isinstance(perf_v3, dict):
        value = _to_float(perf_v3.get("mean_output_tokens_per_second"))
        if value is not None:
            return value
    perf = item.get("performance")
    if isinstance(perf, dict):
        value = _to_float(perf.get("median_output_tokens_per_second"))
        if value is not None:
            return value
    return _to_float(item.get("median_output_tokens_per_second"))


def _aa_coding_score(evals: dict[str, Any]) -> float | None:
    """Coding/engineering benchmark score from AA evaluations.

    Artificial Analysis retired ``artificial_analysis_coding_index`` (always null in v2) and
    replaced it with the six Capability Indexes, where software engineering / coding is
    ``artificial_analysis_engineering_index``.
    """
    coding = _to_float(evals.get("artificial_analysis_coding_index"))
    if coding is not None:
        return coding
    return _to_float(evals.get("artificial_analysis_engineering_index"))


def _is_aa_effort_variant(slug: str, name: str) -> bool:
    """Return True when an AA record is a secondary reasoning-effort variant."""
    if "(" in name and _AA_EFFORT_TAG_RE.search(name):
        if _AA_EFFORT_SLUG_RE.search(slug):
            return True
        if slug.lower().endswith("-max") and re.search(r"\(\s*max\b", name, re.IGNORECASE):
            return True
    return False


def _aa_creator_name(item: dict[str, Any]) -> str:
    creator = item.get("model_creator")
    if isinstance(creator, dict):
        return str(creator.get("name") or "")
    return str(item.get("model_creator.name") or "")


def _aa_index_keys(slug: str, name: str, creator: str) -> list[str]:
    """Normalized lookup keys for an AA model entry."""
    clean_name = _AA_EFFORT_TAG_RE.sub(" ", name)
    base_slug = _AA_ALL_EFFORT_SLUG_RE.sub("", slug) if _is_aa_effort_variant(slug, name) else slug
    forms = (
        slug,
        base_slug,
        _AA_DATE_CODE_RE.sub(" ", base_slug),
        _AA_EDITION_RE.sub("", _AA_DATE_CODE_RE.sub(" ", base_slug)),
        clean_name,
        _AA_DATE_CODE_RE.sub(" ", clean_name),
        _AA_EDITION_RE.sub("", _AA_DATE_CODE_RE.sub(" ", clean_name)),
    )
    keys: list[str] = []
    seen: set[str] = set()

    def add(key: str) -> None:
        if key and key not in seen:
            seen.add(key)
            keys.append(key)

    add(slug)
    for form in forms:
        add(norm_name(form))
        add(norm_name(_AA_TRAILING_ONE_DOT_ZERO_RE.sub("", form)))

    c_norm = norm_name(creator)
    if c_norm:
        for key in list(keys):
            if key == slug:
                continue
            if key.startswith(c_norm) and len(key) > len(c_norm):
                add(key[len(c_norm) :])
            else:
                add(c_norm + key)
    return keys


def _aa_entry_rank(entry: dict[str, Any], is_canonical: bool) -> tuple[int, float, float]:
    iq = entry.get("iq")
    coding = entry.get("coding")
    return (
        1 if is_canonical else 0,
        float(iq) if iq is not None else -1.0,
        float(coding) if coding is not None else -1.0,
    )


def _model_aa_lookup_keys(
    m: NormalizedModel, norm_names_by_id: dict[str, tuple[str, ...]]
) -> list[str]:
    """Candidate keys for matching a catalogue model against ``fetch_aa_benchmarks``."""
    canon = m.canonical_id or ""
    canon_tail = canon.partition("/")[2] if "/" in canon else canon
    no_params_canon = _AA_PARAM_SIZE_RE.sub("", canon)
    no_params_tail = _AA_PARAM_SIZE_RE.sub("", canon_tail)
    forms: list[str] = [
        m.id,
        m.name,
        _AA_DATE_CODE_RE.sub(" ", m.name),
        canon,
        canon_tail,
        no_params_canon,
        no_params_tail,
        _AA_TRAILING_ONE_DOT_ZERO_RE.sub("", no_params_canon),
        _AA_TRAILING_ONE_DOT_ZERO_RE.sub("", no_params_tail),
        *norm_names_by_id.get(m.id, ()),
        _AA_EDITION_RE.sub("", m.id),
        _AA_EDITION_RE.sub("", m.name),
    ]
    keys: list[str] = [m.id]
    seen: set[str] = {m.id}
    for form in forms:
        nk = norm_name(form)
        if nk and nk not in seen:
            seen.add(nk)
            keys.append(nk)
    return keys


def fetch_aa_benchmarks(
    api_key: str,
    client: httpx.Client | None = None,
    errors: list[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Fetch live benchmark metrics from the Artificial Analysis Data API.

    Uses the Free-tier language model list, which accepts every key tier. The legacy
    ``/api/v2/data/*`` endpoints were retired (HTTP 410) in November 2026. When multiple
    reasoning-effort variants share a normalized model name (e.g. ``Max``, ``High``,
    ``Low``, ``Non-reasoning``), the canonical/flagship entry is retained rather than
    letting lower-effort variants overwrite it.
    """
    try:
        with client_scope(client) as http:
            benchmarks: dict[str, dict[str, Any]] = {}
            ranks: dict[str, tuple[int, float, float]] = {}
            for page in range(1, AA_MAX_PAGES + 1):
                r = http.get(
                    AA_MODELS_URL,
                    headers={"x-api-key": api_key},
                    params={"page": page},
                )
                r.raise_for_status()
                payload = r.json()
                if isinstance(payload, list):
                    models = payload
                    has_more = False
                elif isinstance(payload, dict):
                    models = payload.get("data", [])
                    pagination = payload.get("pagination")
                    has_more = (
                        bool(pagination.get("has_more")) if isinstance(pagination, dict) else False
                    )
                else:
                    raise ValueError("unexpected Artificial Analysis response")

                if not isinstance(models, list):
                    raise ValueError("unexpected Artificial Analysis model list")
                for item in models:
                    if not isinstance(item, dict):
                        continue
                    slug = str(item.get("slug") or item.get("id") or "").strip()
                    if not slug:
                        continue
                    evals = item.get("evaluations")
                    if not isinstance(evals, dict):
                        evals = {}
                    name = str(item.get("name") or "").strip()
                    creator = _aa_creator_name(item)
                    entry = {
                        "iq": _to_float(evals.get("artificial_analysis_intelligence_index")),
                        "coding": _aa_coding_score(evals),
                        "tps": _aa_speed_tps(item),
                    }
                    is_canonical = not _is_aa_effort_variant(slug, name)
                    rank = _aa_entry_rank(entry, is_canonical)
                    for key in _aa_index_keys(slug, name, creator):
                        prev_rank = ranks.get(key)
                        # Exact slug always maps to its own record; normalized keys keep the
                        # canonical/highest-IQ entry.
                        if key == slug or prev_rank is None or rank > prev_rank:
                            benchmarks[key] = entry
                            ranks[key] = (2, float("inf"), float("inf")) if key == slug else rank
                if not has_more:
                    break
            return benchmarks
    except httpx.HTTPStatusError as exc:
        if errors is not None:
            errors.append(f"Artificial Analysis returned HTTP {exc.response.status_code}")
        return {}
    except (httpx.HTTPError, ValueError) as exc:
        if errors is not None:
            errors.append(f"Artificial Analysis fetch failed ({type(exc).__name__})")
        return {}


def _build_endpoint_index(
    endpoints: dict[str, str] | None,
) -> dict[str, tuple[str, ...]]:
    """Map each endpoint model ID to its normalized display name(s) in O(1)."""
    by_id: dict[str, list[str]] = {}
    for raw_name, model_id in (endpoints or {}).items():
        key = norm_name(raw_name)
        if key:
            by_id.setdefault(model_id, []).append(key)
    return {mid: tuple(keys) for mid, keys in by_id.items()}


def _lookup_pricing(
    m: NormalizedModel,
    pricing_map: dict[str, dict[str, Any]],
    norm_names_by_id: dict[str, tuple[str, ...]],
) -> tuple[str, dict[str, Any]] | None:
    """Find a pricing row for a model: by endpoint model ID first (O(1)), then fuzzy names."""
    if m.id in norm_names_by_id:
        for key in norm_names_by_id[m.id]:
            if key in pricing_map:
                return key, pricing_map[key]
        return None
    for key in (norm_name(m.name), norm_name(m.id), m.id, m.name):
        if pricing_map.get(key):
            return key, pricing_map[key]
    return None


def merge_docs_pricing(
    models: list[NormalizedModel],
    pricing: dict[str, dict[str, Any]],
    aa_benchmarks: dict[str, dict[str, Any]] | None = None,
    all_plan_pricing: dict[str, dict[str, dict[str, Any]]] | None = None,
    models_dev_metadata: dict[str, dict[str, Any]] | None = None,
    endpoints: dict[str, str] | None = None,
    warnings: list[str] | None = None,
    _endpoint_index: dict[str, tuple[str, ...]] | None = None,
) -> list[NormalizedModel]:
    """Merge published monthly allowances and prices into catalog models.

    `endpoints` is the docs Endpoints table (display name -> model ID); pricing rows are
    matched to catalogue models by ID through it. `warnings`, if given, receives messages
    about active models without pricing and pricing rows without a model.
    """
    benchmarks = aa_benchmarks or {}
    external_metadata = models_dev_metadata or {}
    norm_names_by_id = (
        _endpoint_index if _endpoint_index is not None else _build_endpoint_index(endpoints)
    )
    used_keys: set[str] = set()
    unpriced: list[str] = []
    for m in models:
        key_name = norm_name(m.name)
        key_id = norm_name(m.id)

        found = _lookup_pricing(m, pricing, norm_names_by_id)
        info = found[1] if found else None
        if found:
            used_keys.add(found[0])
        elif m.status == "active":
            unpriced.append(m.id)
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
                plan_found = _lookup_pricing(m, plan_map, norm_names_by_id)
                if plan_found:
                    m.plan_allowances[plan_name] = plan_found[1]["allowance"]
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

        bench = next(
            (benchmarks[k] for k in _model_aa_lookup_keys(m, norm_names_by_id) if k in benchmarks),
            None,
        )
        if bench:
            if m.aa_iq is None:
                m.aa_iq = bench.get("iq")
            if m.aa_coding is None:
                m.aa_coding = bench.get("coding")
            if m.speed_tps is None:
                m.speed_tps = bench.get("tps")

    if warnings is not None:
        if unpriced:
            warnings.append(
                f"{len(unpriced)} active catalogue model(s) have no docs pricing match: "
                + ", ".join(unpriced)
            )
        orphans = [
            str(row.get("raw_name", key))
            for key, row in pricing.items()
            if key not in used_keys and isinstance(row, dict)
        ]
        if orphans:
            warnings.append(
                f"{len(orphans)} docs pricing row(s) matched no catalogue model: "
                + ", ".join(orphans)
            )

    return models


def merge_privacy(
    models: list[NormalizedModel],
    privacy: dict[str, dict[str, Any]],
    endpoints: dict[str, str] | None = None,
    warnings: list[str] | None = None,
    now: datetime | None = None,
    _endpoint_index: dict[str, tuple[str, ...]] | None = None,
) -> None:
    """Attach docs privacy rows to models, resolving names the same way pricing does.

    `warnings`, if given, receives messages about active models without a privacy row and
    expired ZDR agreement dates (relative to `now`).
    """
    norm_names_by_id = (
        _endpoint_index if _endpoint_index is not None else _build_endpoint_index(endpoints)
    )
    by_norm = {norm_name(n): row for n, row in privacy.items() if norm_name(n)}
    missing: list[str] = []
    expired: set[str] = set()
    today = (now or datetime.now(UTC)).date().isoformat()
    for m in models:
        found = _lookup_pricing(m, by_norm, norm_names_by_id)
        if not found:
            if m.status == "active":
                missing.append(m.id)
            continue
        m.privacy = Privacy(**found[1])
        until = m.privacy.agreement_valid_until
        if until and until < today:
            expired.add(until)
    if warnings is None:
        return
    if missing:
        warnings.append(
            f"{len(missing)} active model(s) have no docs privacy row: " + ", ".join(missing)
        )
    for until in sorted(expired):
        warnings.append(f"privacy agreement date {until} is in the past; docs may be stale")


def merge_request_estimates(
    models: list[NormalizedModel],
    estimates: dict[str, dict[str, dict[str, float]]],
    endpoints: dict[str, str] | None = None,
    warnings: list[str] | None = None,
    _endpoint_index: dict[str, tuple[str, ...]] | None = None,
) -> None:
    """Attach docs request estimates per plan, resolving names the same way pricing does.

    `warnings`, if given, receives a message listing active paid models with no estimate.
    """
    norm_names_by_id = (
        _endpoint_index if _endpoint_index is not None else _build_endpoint_index(endpoints)
    )
    by_plan = {
        plan: {norm_name(n): row for n, row in rows.items() if norm_name(n)}
        for plan, rows in estimates.items()
    }
    missing: list[str] = []
    for m in models:
        for plan, by_norm in by_plan.items():
            found = _lookup_pricing(m, by_norm, norm_names_by_id)
            if found:
                m.request_estimates[plan] = RequestEstimate(**found[1])
        if m.status == "active" and not m.is_zero_cost and not m.request_estimates:
            missing.append(m.id)
    if warnings is not None and missing:
        warnings.append(
            f"{len(missing)} active paid model(s) have no docs request estimate "
            "(quota burn falls back to synthetic token costs): " + ", ".join(missing)
        )


def create_snapshot(
    models: list[NormalizedModel],
    pricing: dict[str, dict[str, Any]],
    plan: str = "go",
    aa_benchmarks: dict[str, dict[str, Any]] | None = None,
    source_catalogue: str = "live",
    source_docs: str = "live",
    all_plan_pricing: dict[str, dict[str, dict[str, Any]]] | None = None,
    models_dev_metadata: dict[str, dict[str, Any]] | None = None,
    endpoints: dict[str, str] | None = None,
    benchmark_source: str = "none",
    warnings: list[str] | None = None,
    privacy: dict[str, dict[str, Any]] | None = None,
    request_estimates: dict[str, dict[str, dict[str, float]]] | None = None,
    sources: dict[str, dict[str, Any]] | None = None,
) -> Snapshot:
    """Create an immutable normalized Snapshot instance."""
    all_warnings = list(warnings or [])
    if not pricing:
        all_warnings.append(
            "no rows parsed from the go-pricing table in the docs; "
            "the docs format may have changed and no pricing was applied"
        )
    from opencode_go_model_optimizer.engine.pricing import validate_multiplier

    for m in models:
        for mult in m.multipliers:
            reason = validate_multiplier(mult)
            if reason:
                all_warnings.append(
                    f"ignoring unsupported price multiplier on {m.id} ({reason}); "
                    "prices for it may be understated"
                )
    endpoint_index = _build_endpoint_index(endpoints)
    merged_models = merge_docs_pricing(
        models,
        pricing,
        aa_benchmarks=aa_benchmarks,
        all_plan_pricing=all_plan_pricing,
        models_dev_metadata=models_dev_metadata,
        endpoints=endpoints,
        warnings=all_warnings if pricing else None,
        _endpoint_index=endpoint_index,
    )
    now = datetime.now(UTC)
    now_utc = now.isoformat(timespec="seconds")
    if privacy is not None:
        if not privacy:
            all_warnings.append(
                "no rows parsed from the docs Privacy table; privacy/region filters have no data"
            )
        else:
            merge_privacy(
                merged_models,
                privacy,
                endpoints,
                all_warnings,
                now,
                _endpoint_index=endpoint_index,
            )

    if request_estimates is not None:
        if not request_estimates:
            all_warnings.append(
                "no rows parsed from the docs go-requests table; "
                "quota burn uses synthetic token costs"
            )
        else:
            merge_request_estimates(
                merged_models,
                request_estimates,
                endpoints,
                all_warnings,
                _endpoint_index=endpoint_index,
            )

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
            docs_pricing_count=len(pricing),
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


def load_cached_snapshot(plan: str = "go", ttl_seconds: float = 3600) -> Snapshot | None:
    """Load cached snapshot if within TTL, else return None.

    Pass ``math.inf`` as the TTL to accept a cache file of any age.
    """
    if ttl_seconds <= 0:
        return None

    norm_plan = normalize_plan(plan)
    path = get_cache_snapshot_path(plan=plan)
    if path.exists():
        try:
            if time.time() - path.stat().st_mtime <= ttl_seconds:
                return load_snapshot(path)
        except OSError, ValueError, json.JSONDecodeError:
            pass

    # Check for another plan's fresh cached snapshot that can share plan allowances
    other_plans = [p for p in PLANS if p != plan and p != norm_plan]
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
            except OSError, ValueError, json.JSONDecodeError:
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


_SOURCE_LABELS: dict[str, str] = {
    DEFAULT_MODELS_HTTP_URL: "catalogue",
    **dict.fromkeys(DOCS_URLS, "docs"),
    MODELS_DEV_URL: "models_dev",
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


def _fetch_and_build(
    plan: str, active_client: httpx.Client, use_aa: bool, recorder: _SourceRecorder
) -> Snapshot:
    aa_key = os.environ.get("AA_API_KEY") if use_aa else None
    aa_errors: list[str] = []
    with concurrent.futures.ThreadPoolExecutor() as executor:
        fut_cat = executor.submit(fetch_catalogue, client=active_client)
        fut_docs = executor.submit(fetch_docs_mdx, client=active_client)
        fut_models_dev = executor.submit(fetch_models_dev, client=active_client)
        fut_aa = (
            executor.submit(fetch_aa_benchmarks, aa_key, client=active_client, errors=aa_errors)
            if aa_key
            else None
        )

        models = fut_cat.result()
        docs_text = fut_docs.result()
        models_dev_metadata = fut_models_dev.result()
        aa_benchmarks = fut_aa.result() if fut_aa is not None else None

    all_plan_pricing = {}
    for p in PLANS:
        parsed = parse_go_pricing(docs_text, plan=p, fallback_to_go=False)
        if parsed:
            all_plan_pricing[p] = parsed
    norm_plan = normalize_plan(plan)
    pricing = all_plan_pricing.get(norm_plan) or parse_go_pricing(docs_text, plan=plan)

    warnings: list[str] = []
    if aa_key and not aa_benchmarks:
        detail = f" ({'; '.join(aa_errors)})" if aa_errors else ""
        warnings.append(
            "AA_API_KEY is set but no Artificial Analysis benchmarks could be fetched"
            f"{detail}; falling back to allowance ranking"
        )

    return create_snapshot(
        models,
        pricing,
        plan=plan,
        aa_benchmarks=aa_benchmarks,
        all_plan_pricing=all_plan_pricing,
        models_dev_metadata=models_dev_metadata,
        endpoints=parse_endpoints_table(docs_text),
        privacy=parse_privacy(docs_text),
        request_estimates=parse_request_estimates(docs_text),
        benchmark_source="aa_live" if aa_benchmarks else "none",
        warnings=warnings,
        sources=recorder.sources,
    )

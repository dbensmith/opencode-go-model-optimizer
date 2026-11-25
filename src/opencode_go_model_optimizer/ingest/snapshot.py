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
    normalize_plan,
)
from opencode_go_model_optimizer.ingest.models_dev import MODELS_DEV_URL, fetch_models_dev


def apply_plan(models: list[NormalizedModel], plan: str) -> list[NormalizedModel]:
    """Apply plan-specific monthly allowances and request estimates to models."""
    norm_plan = normalize_plan(plan)
    for m in models:
        if norm_plan in m.plan_allowances:
            m.monthly_allowance = m.plan_allowances[norm_plan]
        elif plan in m.plan_allowances:
            m.monthly_allowance = m.plan_allowances[plan]
        m.request_estimate = m.request_estimates.get(norm_plan) or m.request_estimates.get(plan)
    return models


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


def _model_aa_lookup_keys(m: NormalizedModel) -> list[str]:
    """Candidate keys for matching a catalogue model against ``fetch_aa_benchmarks``."""
    canon = m.canonical_id or ""
    canon_tail = canon.partition("/")[2] if "/" in canon else canon
    no_params_canon = _AA_PARAM_SIZE_RE.sub("", canon)
    no_params_tail = _AA_PARAM_SIZE_RE.sub("", canon_tail)
    forms: list[str] = [
        # Exact release-specific identifiers first: a catalogue ID may serve a dated
        # release while AA also lists a generic variant. Never let the generic ID shadow
        # the exact release.
        m.name,
        canon,
        canon_tail,
        no_params_canon,
        no_params_tail,
        _AA_TRAILING_ONE_DOT_ZERO_RE.sub("", no_params_canon),
        _AA_TRAILING_ONE_DOT_ZERO_RE.sub("", no_params_tail),
        m.id,
        _AA_DATE_CODE_RE.sub(" ", m.name),
        _AA_EDITION_RE.sub("", m.id),
        _AA_EDITION_RE.sub("", m.name),
    ]
    keys: list[str] = []
    seen: set[str] = set()
    for form in forms:
        nk = norm_name(form)
        if nk and nk not in seen:
            seen.add(nk)
            keys.append(nk)
    # Raw IDs are a last-resort exact match for synthetic fixtures and older snapshots;
    # they never shadow the release-specific normalized keys above.
    for raw in (m.id, m.name):
        if raw and raw not in seen:
            seen.add(raw)
            keys.append(raw)
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

        bench = next(
            (benchmarks[k] for k in _model_aa_lookup_keys(m) if k in benchmarks),
            None,
        )
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
            if m.status == "active" and m.privacy is None:
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
    all_warnings = list(warnings or [])
    if not any(m.plan_allowances for m in models):
        all_warnings.append("no per-plan usage from the Go models API; no allowance was applied")
    else:
        norm_plan = normalize_plan(plan)
        no_usage = sorted(
            m.id
            for m in models
            if m.status == "active"
            and m.model_type == "chat"
            and not m.is_zero_cost
            and norm_plan not in m.plan_allowances
            and plan not in m.plan_allowances
        )
        if no_usage:
            all_warnings.append(
                f"{len(no_usage)} active paid model(s) have no go.monthly_usage for plan "
                f"{norm_plan}: " + ", ".join(no_usage)
            )
        anomalies = sorted(
            m.id
            for m in models
            if m.status == "active"
            and m.model_type == "chat"
            and m.is_zero_cost
            and m.plan_allowances
        )
        if anomalies:
            all_warnings.append(
                f"catalogue anomaly: {len(anomalies)} model(s) publish a monthly allowance "
                "but no token prices: " + ", ".join(anomalies)
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
    merged_models = merge_catalogue_metadata(
        models,
        aa_benchmarks=aa_benchmarks,
        models_dev_metadata=models_dev_metadata,
    )
    now = datetime.now(UTC)
    now_utc = now.isoformat(timespec="seconds")
    if privacy is not None:
        if not privacy and not any(m.privacy is not None for m in merged_models):
            all_warnings.append(
                "no privacy rows from the Go models API or docs Privacy table; "
                "privacy/region filters have no data"
            )
        elif privacy:
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
        detail = f" ({'; '.join(console_errors)})" if console_errors else ""
        warnings.append(
            "could not read the console request profiles; models without "
            f"go.requests have no request estimates{detail}; quota burn falls back to tokens"
        )
    if aa_key and not aa_benchmarks:
        detail = f" ({'; '.join(aa_errors)})" if aa_errors else ""
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

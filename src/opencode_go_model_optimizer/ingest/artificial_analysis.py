"""Ingestor and matching helpers for Artificial Analysis benchmarks."""

import math
import re
from typing import Any

import httpx

from opencode_go_model_optimizer.ingest.docs import norm_name
from opencode_go_model_optimizer.ingest.http import client_scope
from opencode_go_model_optimizer.ingest.models import NormalizedModel

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


def _to_float(value: Any) -> float | None:
    """Coerce a value to a finite float, returning None if not numeric."""
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except TypeError, ValueError:
        return None
    return result if math.isfinite(result) else None


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


def lookup_aa_benchmark(
    model: NormalizedModel, benchmarks: dict[str, dict[str, Any]]
) -> dict[str, Any] | None:
    """Find the best matching Artificial Analysis benchmark entry for ``model``."""
    return next(
        (benchmarks[k] for k in _model_aa_lookup_keys(model) if k in benchmarks),
        None,
    )


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

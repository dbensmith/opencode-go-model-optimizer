"""Ingestor and matching helpers for Artificial Analysis benchmarks."""

import contextlib
import json
import os
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from opencode_go_model_optimizer.ingest.docs import norm_name
from opencode_go_model_optimizer.ingest.http import atomic_write_text, client_scope
from opencode_go_model_optimizer.ingest.models import NormalizedModel, ensure_utc
from opencode_go_model_optimizer.numbers import finite_float

AA_MODELS_URL = "https://artificialanalysis.ai/api/v2/language/models/free"
AA_MAX_PAGES = 20
DEFAULT_AA_CACHE_TTL = 86400
AA_CACHE_FILENAME = "aa-benchmarks.json"
AA_RATELIMIT_FILENAME = "aa-ratelimit.json"

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
    """Mean output tokens/sec from the ``performance_v3`` block."""
    perf = item.get("performance_v3")
    return (
        finite_float(perf.get("mean_output_tokens_per_second")) if isinstance(perf, dict) else None
    )


def _aa_coding_score(evals: dict[str, Any]) -> float | None:
    """Coding/engineering score: the ``artificial_analysis_engineering_index`` Capability Index."""
    return finite_float(evals.get("artificial_analysis_engineering_index"))


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


def _cache_dir() -> Path:
    """Return the local cache directory for Artificial Analysis state."""
    base = Path(os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache"))
    return base / "opencode-go-model-optimizer"


def get_aa_cache_path() -> Path:
    """Return filesystem path for the local Artificial Analysis benchmark cache."""
    return _cache_dir() / AA_CACHE_FILENAME


def get_aa_ratelimit_path() -> Path:
    """Return filesystem path for persisted Artificial Analysis cooldown state."""
    return _cache_dir() / AA_RATELIMIT_FILENAME


def _format_epoch_iso(ts: int) -> str | None:
    """Format a positive Unix epoch timestamp as UTC ISO 8601, or None if invalid."""
    if ts <= 0:
        return None
    with contextlib.suppress(ValueError, OSError, OverflowError):
        return datetime.fromtimestamp(ts, tz=UTC).isoformat(timespec="seconds")
    return None


def _cache_age_seconds(path: Path, payload: dict[str, Any], now: float) -> float:
    """Compute cache age from ``fetched_at`` timestamp, falling back to file mtime."""
    fetched_at = payload.get("fetched_at")
    if isinstance(fetched_at, str) and fetched_at.strip():
        with contextlib.suppress(ValueError):
            ts = ensure_utc(
                datetime.fromisoformat(fetched_at.strip().replace("Z", "+00:00"))
            ).timestamp()
            return max(0.0, now - ts)
    return max(0.0, now - path.stat().st_mtime)


def load_cached_aa_benchmarks_with_age(
    ttl_seconds: float = 0,
) -> tuple[dict[str, dict[str, Any]], float] | None:
    """Load cached AA benchmarks and their age in seconds if within ``ttl_seconds``."""
    if ttl_seconds <= 0:
        return None
    path = get_aa_cache_path()
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if (
            not isinstance(payload, dict)
            or payload.get("schema_version") != 1
            or not isinstance(payload.get("benchmarks"), dict)
            or not payload["benchmarks"]
        ):
            return None
        age = _cache_age_seconds(path, payload, time.time())
    except OSError, ValueError, json.JSONDecodeError:
        return None
    if age > ttl_seconds:
        return None
    return payload["benchmarks"], age


def load_cached_aa_benchmarks(ttl_seconds: float = 0) -> dict[str, dict[str, Any]] | None:
    """Load cached Artificial Analysis benchmarks if present and within ``ttl_seconds``."""
    loaded = load_cached_aa_benchmarks_with_age(ttl_seconds=ttl_seconds)
    return loaded[0] if loaded is not None else None


def save_cached_aa_benchmarks(benchmarks: dict[str, dict[str, Any]]) -> Path | None:
    """Write non-empty Artificial Analysis benchmarks to the local cache directory."""
    if not benchmarks:
        return None
    path = get_aa_cache_path()
    payload = {
        "schema_version": 1,
        "fetched_at": datetime.fromtimestamp(time.time(), tz=UTC).isoformat(timespec="seconds"),
        "benchmarks": benchmarks,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, json.dumps(payload))
        return path
    except OSError:
        return None


def _parse_int_header(headers: httpx.Headers, name: str) -> int | None:
    """Parse a non-negative integer HTTP header, or return None."""
    raw = (headers.get(name) or "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value >= 0 else None


def _parse_rate_limit_headers(response: httpx.Response) -> dict[str, int | None]:
    """Extract rate-limit headers from an Artificial Analysis HTTP response."""
    limit = _parse_int_header(response.headers, "x-ratelimit-limit")
    remaining = _parse_int_header(response.headers, "x-ratelimit-remaining")
    reset_raw = _parse_int_header(response.headers, "x-ratelimit-reset")
    reset_at = reset_raw if reset_raw and _format_epoch_iso(reset_raw) is not None else None
    retry_raw = _parse_int_header(response.headers, "retry-after")
    retry_after = retry_raw if retry_raw and retry_raw > 0 else None
    if reset_at is None and retry_after is not None and response.status_code == 429:
        reset_at = int(time.time()) + retry_after
    return {
        "limit": limit,
        "remaining": remaining,
        "reset_at": reset_at,
        "retry_after": retry_after,
    }


def load_aa_rate_limit() -> dict[str, Any] | None:
    """Return active local AA cooldown metadata when still within its reset window."""
    path = get_aa_ratelimit_path()
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError, ValueError, json.JSONDecodeError:
        return None
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        return None
    reset_at = payload.get("reset_at")
    if isinstance(reset_at, bool) or not isinstance(reset_at, int) or reset_at <= time.time():
        return None
    reset_iso = _format_epoch_iso(reset_at)
    if reset_iso is None:
        return None
    return {
        "reset_at": reset_at,
        "reset_at_iso": payload.get("reset_at_iso") or reset_iso,
        "limit": payload.get("limit"),
        "remaining": payload.get("remaining"),
        "message": payload.get("message") or "",
    }


def save_aa_rate_limit(
    reset_at: int,
    *,
    limit: int | None = None,
    remaining: int | None = None,
    message: str = "",
) -> Path | None:
    """Persist an active Artificial Analysis cooldown window to the cache directory."""
    if isinstance(reset_at, bool) or reset_at <= time.time():
        return None
    reset_iso = _format_epoch_iso(reset_at)
    if reset_iso is None:
        return None
    path = get_aa_ratelimit_path()
    payload = {
        "schema_version": 1,
        "reset_at": reset_at,
        "reset_at_iso": reset_iso,
        "limit": limit,
        "remaining": remaining,
        "message": message,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, json.dumps(payload))
        return path
    except OSError:
        return None


def clear_aa_rate_limit() -> None:
    """Remove any stale local Artificial Analysis cooldown file."""
    with contextlib.suppress(OSError):
        get_aa_ratelimit_path().unlink()


def _format_http_error(response: httpx.Response, api_key: str) -> str:
    """Format an Artificial Analysis HTTP status error with safe rate-limit/body details."""
    base = f"Artificial Analysis returned HTTP {response.status_code}"
    details: list[str] = []
    with contextlib.suppress(Exception):
        body = response.json()
        if isinstance(body, dict):
            for key in ("error", "message"):
                val = body.get(key)
                if isinstance(val, str):
                    msg = " ".join(val.split())
                    if msg and (not api_key or api_key not in msg):
                        details.append(msg[:120])
                        break
    if response.status_code == 429:
        reset_raw = (response.headers.get("x-ratelimit-reset") or "").strip()
        retry_raw = (response.headers.get("retry-after") or "").strip()
        reset_added = False
        if reset_raw:
            with contextlib.suppress(ValueError):
                reset_iso = _format_epoch_iso(int(reset_raw))
                if reset_iso is not None:
                    details.append(f"resets at {reset_iso}")
                    reset_added = True
        if not reset_added and retry_raw and (not api_key or api_key not in retry_raw):
            details.append(
                f"retry after {retry_raw}s" if retry_raw.isdigit() else f"retry after {retry_raw}"
            )
    return f"{base} ({'; '.join(details)})" if details else base


def _record_rl_stats(req_stats: dict[str, Any], rl: dict[str, int | None]) -> None:
    """Update request telemetry dictionary from parsed rate-limit headers."""
    if rl["limit"] is not None:
        req_stats["ratelimit_limit"] = rl["limit"]
    if rl["remaining"] is not None:
        req_stats["ratelimit_remaining"] = rl["remaining"]
    if rl["reset_at"] is not None:
        reset_iso = _format_epoch_iso(rl["reset_at"])
        if reset_iso is not None:
            req_stats["ratelimit_reset"] = reset_iso


def fetch_aa_benchmarks(
    api_key: str,
    client: httpx.Client | None = None,
    errors: list[str] | None = None,
    stats: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """Fetch live benchmark metrics from the Artificial Analysis Data API.

    Uses the Free-tier language model list, which accepts every key tier. When multiple
    reasoning-effort variants share a normalized model name (e.g. ``Max``, ``High``,
    ``Low``, ``Non-reasoning``), the canonical/flagship entry is retained rather than
    letting lower-effort variants overwrite it. Respects local 429 cooldown windows
    and aborts pagination early when remaining quota cannot finish all pages.
    """
    req_stats: dict[str, Any] = {"requests": 0, "pages_fetched": 0}

    def publish_stats() -> None:
        if stats is not None:
            stats.update({k: v for k, v in req_stats.items() if v is not None})

    cooldown = load_aa_rate_limit()
    if cooldown is not None:
        req_stats["cooldown_active"] = True
        req_stats["ratelimit_reset"] = cooldown.get("reset_at_iso")
        req_stats["ratelimit_limit"] = cooldown.get("limit")
        req_stats["ratelimit_remaining"] = cooldown.get("remaining")
        publish_stats()
        if errors is not None:
            errors.append(
                f"Artificial Analysis rate-limited until {cooldown['reset_at_iso']} "
                "(skipped live request during local cooldown)"
            )
        return {}

    try:
        with client_scope(client) as http:
            benchmarks: dict[str, dict[str, Any]] = {}
            ranks: dict[str, tuple[int, float, float]] = {}
            for page in range(1, AA_MAX_PAGES + 1):
                req_stats["requests"] += 1
                r = http.get(
                    AA_MODELS_URL,
                    headers={"x-api-key": api_key},
                    params={"page": page},
                )
                r.raise_for_status()
                req_stats["pages_fetched"] += 1
                rl = _parse_rate_limit_headers(r)
                _record_rl_stats(req_stats, rl)

                payload = r.json()
                if not isinstance(payload, dict):
                    raise ValueError("unexpected Artificial Analysis response")
                models = payload.get("data", [])
                pagination = payload.get("pagination")
                has_more = (
                    bool(pagination.get("has_more")) if isinstance(pagination, dict) else False
                )

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
                        "iq": finite_float(evals.get("artificial_analysis_intelligence_index")),
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

                if has_more:
                    total_pages = (
                        pagination.get("total_pages") if isinstance(pagination, dict) else None
                    )
                    pages_left = (
                        total_pages - page
                        if isinstance(total_pages, int)
                        and not isinstance(total_pages, bool)
                        and total_pages > page
                        else 1
                    )
                    if rl["remaining"] is not None and rl["remaining"] < pages_left:
                        reset_iso = (
                            _format_epoch_iso(rl["reset_at"])
                            if rl["reset_at"] is not None
                            else None
                        )
                        if rl["remaining"] == 0 and rl["reset_at"] is not None:
                            save_aa_rate_limit(
                                rl["reset_at"],
                                limit=rl["limit"],
                                remaining=0,
                                message="Artificial Analysis quota exhausted mid-pagination",
                            )
                        publish_stats()
                        if errors is not None:
                            reset_suffix = f"; resets at {reset_iso}" if reset_iso else ""
                            errors.append(
                                f"Artificial Analysis quota too low to finish pagination "
                                f"({rl['remaining']} request(s) remaining, {pages_left} more "
                                f"page(s) needed{reset_suffix})"
                            )
                        return {}
                if not has_more:
                    break
            clear_aa_rate_limit()
            publish_stats()
            return benchmarks
    except httpx.HTTPStatusError as exc:
        rl = _parse_rate_limit_headers(exc.response)
        _record_rl_stats(req_stats, rl)
        formatted = _format_http_error(exc.response, api_key)
        if exc.response.status_code == 429 and rl["reset_at"] is not None:
            save_aa_rate_limit(
                rl["reset_at"],
                limit=rl["limit"],
                remaining=rl["remaining"] if rl["remaining"] is not None else 0,
                message=formatted,
            )
        publish_stats()
        if errors is not None:
            errors.append(formatted)
        return {}
    except (httpx.HTTPError, ValueError) as exc:
        publish_stats()
        if errors is not None:
            errors.append(f"Artificial Analysis fetch failed ({type(exc).__name__})")
        return {}

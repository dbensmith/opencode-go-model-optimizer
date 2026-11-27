"""Ingestor for the OpenCode Go model catalogue."""

import json
import math
import re
import shutil
import subprocess
from typing import Any

import httpx

from opencode_go_model_optimizer.ingest.console import RequestProfiles
from opencode_go_model_optimizer.ingest.http import client_scope
from opencode_go_model_optimizer.ingest.models import (
    PLANS,
    ModelLimit,
    NormalizedModel,
    PriceMultiplier,
    Privacy,
    RequestEstimate,
    TokenCost,
    publishes_monthly_allowance,
)
from opencode_go_model_optimizer.numbers import coerce_nested_float, parse_inf_float

DEFAULT_MODELS_HTTP_URL = "https://opencode.ai/inference/go/v1/models"
HEADER_PATTERN = re.compile(r"^[a-zA-Z0-9_\-]+/[a-zA-Z0-9._\-]+$")

_FIVE_HOUR_FRACTION = 0.2  # docs: the 5-hour window is 20% of the monthly allowance
_WEEK_FRACTION = 0.5  # docs: the weekly window is 50% of the monthly allowance
_MONTH_FRACTION = 1.0


def _extract_token_cost(cost_raw: Any) -> TokenCost:
    """Extract TokenCost from either flat CLI format or nested HTTP format."""
    if not isinstance(cost_raw, dict):
        return TokenCost()

    # Case 1: Nested HTTP format (cost.text.input.price, etc.)
    if "text" in cost_raw and isinstance(cost_raw["text"], dict):
        text_obj = cost_raw["text"]
        inp_obj = text_obj.get("input", {})
        inp_price = inp_obj.get("price", 0.0) if isinstance(inp_obj, dict) else inp_obj
        cache_obj = inp_obj.get("cache", {}) if isinstance(inp_obj, dict) else {}
        out_obj = text_obj.get("output", {})
        out_price = out_obj.get("price", 0.0) if isinstance(out_obj, dict) else out_obj
    else:
        # Case 2: Flat CLI format
        inp_price = cost_raw.get("input", 0.0)
        out_price = cost_raw.get("output", 0.0)
        cache_obj = cost_raw.get("cache", {})

    cache_read = cache_obj.get("read", 0.0) if isinstance(cache_obj, dict) else 0.0
    cache_write = cache_obj.get("write", 0.0) if isinstance(cache_obj, dict) else 0.0

    return TokenCost(
        input=coerce_nested_float(inp_price),
        output=coerce_nested_float(out_price),
        cache_read=coerce_nested_float(cache_read),
        cache_write=coerce_nested_float(cache_write),
    )


def _extract_model_limit(limit_raw: Any) -> ModelLimit:
    """Extract context and output token limits."""
    if not isinstance(limit_raw, dict):
        return ModelLimit()
    return ModelLimit(
        context=int(limit_raw.get("context", 0) or 0),
        output=int(limit_raw.get("output", 0) or 0),
    )


def _extract_multipliers(cost_raw: Any) -> list[PriceMultiplier]:
    """Extract `cost.multipliers` ([{when, factor}]); malformed entries are skipped."""
    raw = cost_raw.get("multipliers") if isinstance(cost_raw, dict) else None
    if not isinstance(raw, list):
        return []
    result: list[PriceMultiplier] = []
    for item in raw:
        if not isinstance(item, dict) or not isinstance(item.get("when"), dict):
            continue
        factor = item.get("factor")
        if isinstance(factor, bool) or not isinstance(factor, (int, float)):
            continue
        result.append(PriceMultiplier(factor=float(factor), when=item["when"]))
    return result


def _extract_plan_allowances(go_raw: Any) -> dict[str, float]:
    """Extract `go.monthly_usage` ({plan: USD}) into per-plan allowances.

    `None` means the model has no paid allowance on that plan (free models), so it is
    skipped rather than treated as zero.
    """
    usage = go_raw.get("monthly_usage") if isinstance(go_raw, dict) else None
    if not isinstance(usage, dict):
        return {}
    allowances: dict[str, float] = {}
    for plan, value in usage.items():
        allowance = parse_inf_float(value)
        if allowance is not None:
            allowances[str(plan)] = allowance
    return allowances


def _extract_go_privacy(go_raw: Any) -> Privacy | None:
    """Extract the Go data-handling flags (`training`, `zdr`, `limited_regions`).

    `limited_regions` means some countries are refused, which is not the same as the
    workspace-level Global region requirement, so it is recorded separately and does not
    block a model by itself. That requirement comes from explicit docs text.
    """
    if not isinstance(go_raw, dict):
        return None
    training = go_raw.get("training")
    zdr = go_raw.get("zdr")
    limited = go_raw.get("limited_regions")
    return Privacy(
        trains_on_data=training if isinstance(training, bool) else None,
        zdr=zdr if isinstance(zdr, bool) else None,
        limited_regions=limited if isinstance(limited, bool) else False,
    )


def _extract_go_requests(go_raw: Any) -> float | None:
    """Optional `go.requests` count for the Go plan's 5-hour window."""
    value = go_raw.get("requests") if isinstance(go_raw, dict) else None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) and value > 0 else None


def _round_sig(value: float) -> float:
    """Round to three significant digits, matching the console's request estimates."""
    if not math.isfinite(value) or value <= 0:
        return value
    step = 10 ** max(0, math.floor(math.log10(value)) - 2)
    return float(round(value / step) * step)


def estimate_requests(
    model_id: str,
    cost: TokenCost,
    allowances: dict[str, float],
    go_requests: float | None,
    profiles: RequestProfiles | None,
) -> dict[str, RequestEstimate]:
    """Estimate requests covered by each plan.

    Order of authority, as required: `go.requests` from the API first; otherwise the
    console's scraped per-model profile (or its generic default); otherwise no estimate.
    Requests = (window fraction * monthly allowance) / cost of a profile-sized request.
    """
    if cost.input == 0.0 and cost.output == 0.0:
        if publishes_monthly_allowance(allowances):
            return {}
        unlimited = RequestEstimate(
            per_5h=float("inf"), per_week=float("inf"), per_month=float("inf")
        )
        return dict.fromkeys(PLANS, unlimited)

    go_allowance = allowances.get("go")
    use_go_requests = (
        go_requests is not None
        and go_allowance is not None
        and go_allowance > 0
        and math.isfinite(go_allowance)
    )

    per_request: float | None = None
    if not use_go_requests:
        if profiles is None:
            return {}
        profile_in, profile_cache, profile_out = profiles.by_model.get(model_id, profiles.default)
        per_request = (
            profile_in * cost.input + profile_cache * cost.cache_read + profile_out * cost.output
        ) / 1_000_000.0
        if per_request <= 0.0:
            return {}

    estimates: dict[str, RequestEstimate] = {}
    for plan, allowance in allowances.items():
        if math.isinf(allowance):
            estimates[plan] = RequestEstimate(
                per_5h=float("inf"), per_week=float("inf"), per_month=float("inf")
            )
            continue
        if allowance <= 0:
            continue
        if use_go_requests:
            five_hour = go_requests if plan == "go" else go_requests * allowance / go_allowance
        else:
            five_hour = _FIVE_HOUR_FRACTION * allowance / per_request  # type: ignore[operator]
        estimates[plan] = RequestEstimate(
            per_5h=_round_sig(five_hour),
            per_week=_round_sig(five_hour * _WEEK_FRACTION / _FIVE_HOUR_FRACTION),
            per_month=_round_sig(five_hour * _MONTH_FRACTION / _FIVE_HOUR_FRACTION),
        )
    return estimates


def apply_request_estimates(
    models: list[NormalizedModel], profiles: RequestProfiles | None
) -> list[NormalizedModel]:
    """Compute per-plan request estimates for API models without `go.requests`."""
    for m in models:
        m.request_estimates = estimate_requests(
            m.id, m.cost, m.plan_allowances, m.go_requests, profiles
        )
    return models


def model_from_dict(d: dict[str, Any], profiles: RequestProfiles | None = None) -> NormalizedModel:
    """Convert raw catalog dictionary to a NormalizedModel."""
    model_id = str(d.get("id", ""))
    # `owned_by` is the vendor label ("opencode"), not a provider ID; never use it.
    provider_id = str(d.get("providerID") or "opencode-go")
    name = str(d.get("name", model_id))
    canonical_id = str(d.get("canonical_id") or "")
    model_type = str(d.get("type") or "chat")
    status = str(d.get("status", "active"))
    release_date = str(d.get("release_date", d.get("last_updated", "")))

    cost = _extract_token_cost(d.get("cost"))
    limit = _extract_model_limit(d.get("limit"))
    go_raw = d.get("go")
    plan_allowances = _extract_plan_allowances(go_raw)
    go_requests = _extract_go_requests(go_raw)

    return NormalizedModel(
        id=model_id,
        provider_id=provider_id,
        canonical_id=canonical_id,
        name=name,
        model_type=model_type,
        status=status,
        release_date=release_date,
        cost=cost,
        limit=limit,
        multipliers=_extract_multipliers(d.get("cost")),
        plan_allowances=plan_allowances,
        privacy=_extract_go_privacy(go_raw),
        go_requests=go_requests,
        request_estimates=estimate_requests(model_id, cost, plan_allowances, go_requests, profiles),
    )


def parse_catalogue_cli(
    text: str, profiles: RequestProfiles | None = None
) -> list[NormalizedModel]:
    """Parse `opencode models --verbose opencode-go` text output.

    Records are delimited by header lines like `opencode-go/<model_id>`
    followed by the JSON payload.
    """
    models: list[NormalizedModel] = []
    lines = text.splitlines()
    cur_header: str | None = None
    buf: list[str] = []

    def flush() -> None:
        nonlocal cur_header, buf
        if cur_header is None:
            return
        body = "\n".join(buf).strip()
        cur_header = None
        buf = []
        if not body:
            return
        try:
            data = json.loads(body)
            if isinstance(data, dict):
                models.append(model_from_dict(data, profiles))
        except json.JSONDecodeError, ValueError, KeyError:
            pass

    for line in lines:
        stripped = line.strip()
        # Header line pattern: provider/id without JSON delimiters
        if HEADER_PATTERN.match(stripped):
            flush()
            cur_header = stripped
        elif cur_header is not None:
            buf.append(line)

    flush()
    return models


def parse_catalogue_http(
    data: dict[str, Any] | list[Any], profiles: RequestProfiles | None = None
) -> list[NormalizedModel]:
    """Parse HTTP API response from `/inference/go/v1/models`."""
    items = data.get("data", []) if isinstance(data, dict) else data
    models: list[NormalizedModel] = []
    for item in items:
        if isinstance(item, dict):
            models.append(model_from_dict(item, profiles))
    return models


def _fetch_catalogue_cli(profiles: RequestProfiles | None = None) -> list[NormalizedModel]:
    """Fallback: read the catalogue from the local `opencode` CLI, or [] on any failure."""
    if not shutil.which("opencode"):
        return []
    try:
        proc = subprocess.run(
            ["opencode", "models", "--verbose", "opencode-go"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except subprocess.SubprocessError, OSError:
        return []
    if proc.returncode == 0 and proc.stdout.strip():
        return parse_catalogue_cli(proc.stdout, profiles)
    return []


def fetch_catalogue(
    http_url: str = DEFAULT_MODELS_HTTP_URL,
    client: httpx.Client | None = None,
    profiles: RequestProfiles | None = None,
) -> list[NormalizedModel]:
    """Fetch the model catalogue via HTTP (primary), falling back to the local CLI.

    The HTTP payload is richer (multipliers, release_date, limit). If HTTP fails and the
    CLI fallback yields nothing, the original HTTP error is re-raised.
    """
    try:
        with client_scope(client) as http:
            response = http.get(http_url)
            response.raise_for_status()
            models = parse_catalogue_http(response.json(), profiles)
        if models:
            return models
        http_error: Exception = ValueError("catalogue HTTP response contained no models")
    except (httpx.HTTPError, ValueError) as exc:
        http_error = exc

    models = _fetch_catalogue_cli(profiles)
    if models:
        return models
    raise http_error

"""Ingestor for the OpenCode Go model catalogue."""

import json
import re
import shutil
import subprocess
from typing import Any

import httpx

from opencode_go_model_optimizer.ingest.http import client_scope
from opencode_go_model_optimizer.ingest.models import (
    ModelLimit,
    NormalizedModel,
    PriceMultiplier,
    TokenCost,
)

DEFAULT_MODELS_HTTP_URL = "https://opencode.ai/inference/go/v1/models"
HEADER_PATTERN = re.compile(r"^[a-zA-Z0-9_\-]+/[a-zA-Z0-9._\-]+$")


def _safe_float(val: Any) -> float:
    """Safely convert value to float, handling nested dicts, strings, and nulls."""
    if val is None or isinstance(val, bool):
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, dict):
        for k in ("price", "default", "standard"):
            if k in val and not isinstance(val[k], bool) and isinstance(val[k], (int, float, str)):
                return _safe_float(val[k])
        for v in val.values():
            if not isinstance(v, bool) and isinstance(v, (int, float, str)):
                try:
                    return float(v)
                except ValueError, TypeError:
                    continue
        return 0.0
    try:
        return float(val)
    except ValueError, TypeError:
        return 0.0


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
        cache_read = cache_obj.get("read", 0.0) if isinstance(cache_obj, dict) else 0.0
        cache_write = cache_obj.get("write", 0.0) if isinstance(cache_obj, dict) else 0.0

        out_obj = text_obj.get("output", {})
        out_price = out_obj.get("price", 0.0) if isinstance(out_obj, dict) else out_obj

        return TokenCost(
            input=_safe_float(inp_price),
            output=_safe_float(out_price),
            cache_read=_safe_float(cache_read),
            cache_write=_safe_float(cache_write),
        )

    # Case 2: Flat CLI format
    inp_price = cost_raw.get("input", 0.0)
    out_price = cost_raw.get("output", 0.0)
    cache_dict = cost_raw.get("cache", {})
    cache_read = cache_dict.get("read", 0.0) if isinstance(cache_dict, dict) else 0.0
    cache_write = cache_dict.get("write", 0.0) if isinstance(cache_dict, dict) else 0.0

    return TokenCost(
        input=_safe_float(inp_price),
        output=_safe_float(out_price),
        cache_read=_safe_float(cache_read),
        cache_write=_safe_float(cache_write),
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


def model_from_dict(d: dict[str, Any]) -> NormalizedModel:
    """Convert raw catalog dictionary to a NormalizedModel."""
    model_id = str(d.get("id", ""))
    # `owned_by` is the vendor label ("opencode"), not a provider ID; never use it.
    provider_id = str(d.get("providerID") or "opencode-go")
    name = str(d.get("name", model_id))
    status = str(d.get("status", "active"))
    release_date = str(d.get("release_date", d.get("last_updated", "")))

    cost = _extract_token_cost(d.get("cost"))
    limit = _extract_model_limit(d.get("limit"))

    return NormalizedModel(
        id=model_id,
        provider_id=provider_id,
        name=name,
        status=status,
        release_date=release_date,
        cost=cost,
        limit=limit,
        multipliers=_extract_multipliers(d.get("cost")),
    )


def parse_catalogue_cli(text: str) -> list[NormalizedModel]:
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
                models.append(model_from_dict(data))
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


def parse_catalogue_http(data: dict[str, Any] | list[Any]) -> list[NormalizedModel]:
    """Parse HTTP API response from `/inference/go/v1/models`."""
    items = data.get("data", []) if isinstance(data, dict) else data
    models: list[NormalizedModel] = []
    for item in items:
        if isinstance(item, dict):
            models.append(model_from_dict(item))
    return models


def _fetch_catalogue_cli() -> list[NormalizedModel]:
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
        return parse_catalogue_cli(proc.stdout)
    return []


def fetch_catalogue(
    http_url: str = DEFAULT_MODELS_HTTP_URL,
    client: httpx.Client | None = None,
) -> list[NormalizedModel]:
    """Fetch the model catalogue via HTTP (primary), falling back to the local CLI.

    The HTTP payload is richer (multipliers, release_date, limit). If HTTP fails and the
    CLI fallback yields nothing, the original HTTP error is re-raised.
    """
    try:
        with client_scope(client) as http:
            response = http.get(http_url)
            response.raise_for_status()
            models = parse_catalogue_http(response.json())
        if models:
            return models
        http_error: Exception = ValueError("catalogue HTTP response contained no models")
    except (httpx.HTTPError, ValueError) as exc:
        http_error = exc

    models = _fetch_catalogue_cli()
    if models:
        return models
    raise http_error

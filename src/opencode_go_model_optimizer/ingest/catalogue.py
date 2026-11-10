"""Ingestor for the OpenCode Go model catalogue."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from typing import Any

import httpx

from opencode_go_model_optimizer.ingest.models import ModelLimit, NormalizedModel, TokenCost

DEFAULT_MODELS_HTTP_URL = "https://opencode.ai/inference/go/v1/models"
HEADER_PATTERN = re.compile(r"^[a-zA-Z0-9_\-]+/[a-zA-Z0-9._\-]+$")


def _safe_float(val: Any) -> float:
    """Safely convert value to float, handling nested dicts, strings, and nulls."""
    if val is None:
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, dict):
        for k in ("price", "default", "standard"):
            if k in val and isinstance(val[k], (int, float, str)):
                return _safe_float(val[k])
        for v in val.values():
            if isinstance(v, (int, float, str)):
                try:
                    return float(v)
                except (ValueError, TypeError):
                    continue
        return 0.0
    try:
        return float(val)
    except (ValueError, TypeError):
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


def model_from_dict(d: dict[str, Any]) -> NormalizedModel:
    """Convert raw catalog dictionary to a NormalizedModel."""
    model_id = str(d.get("id", ""))
    provider_id = str(d.get("providerID", d.get("owned_by", "opencode-go")))
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
        except (json.JSONDecodeError, ValueError, KeyError):
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


def fetch_catalogue(
    http_url: str = DEFAULT_MODELS_HTTP_URL,
    client: httpx.Client | None = None,
) -> list[NormalizedModel]:
    """Fetch model catalogue via local CLI if available, otherwise direct HTTP."""
    # 1. Attempt CLI
    if shutil.which("opencode"):
        try:
            proc = subprocess.run(
                ["opencode", "models", "--verbose", "opencode-go"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                models = parse_catalogue_cli(proc.stdout)
                if models:
                    return models
        except (subprocess.SubprocessError, OSError):
            pass

    # 2. Fallback to direct HTTP
    close_client = False
    if client is None:
        client = httpx.Client(
            timeout=10.0, headers={"User-Agent": "opencode-go-model-optimizer/0.1.0"}
        )
        close_client = True

    try:
        response = client.get(http_url)
        response.raise_for_status()
        return parse_catalogue_http(response.json())
    finally:
        if close_client:
            client.close()

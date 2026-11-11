"""Optional metadata enrichment from models.dev."""

from typing import Any

import httpx

from opencode_go_model_optimizer.ingest.docs import norm_name
from opencode_go_model_optimizer.ingest.http import client_scope

MODELS_DEV_URL = "https://models.dev/api.json"


def _extract_metadata(entry: dict[str, Any]) -> dict[str, Any]:
    """Extract the metadata fields this optimizer can backfill from a models.dev entry."""
    metadata: dict[str, Any] = {}
    release_date = entry.get("release_date") or entry.get("releaseDate")
    if release_date:
        metadata["release_date"] = str(release_date)

    limit = entry.get("limit")
    if isinstance(limit, dict):
        context = limit.get("context")
        output = limit.get("output")
        if isinstance(context, (int, float)) and context > 0:
            metadata["context"] = int(context)
        if isinstance(output, (int, float)) and output > 0:
            metadata["output"] = int(output)
    return metadata


def _register(result: dict[str, dict[str, Any]], key: str, metadata: dict[str, Any]) -> None:
    """Register metadata under its normalized key without overwriting earlier entries."""
    normalized = norm_name(key)
    if normalized and normalized not in result:
        result[normalized] = metadata


def parse_models_dev(data: Any) -> dict[str, dict[str, Any]]:
    """Flatten a models.dev API payload into normalized-key metadata lookups."""
    result: dict[str, dict[str, Any]] = {}
    if not isinstance(data, dict):
        return result

    for provider in data.values():
        if not isinstance(provider, dict):
            continue
        entries = provider.get("models")
        if not isinstance(entries, dict):
            continue
        for model_id, entry in entries.items():
            if not isinstance(entry, dict):
                continue
            metadata = _extract_metadata(entry)
            if not metadata:
                continue
            _register(result, str(model_id), metadata)
            name = entry.get("name")
            if name:
                _register(result, str(name), metadata)

    return result


def fetch_models_dev(
    client: httpx.Client | None = None,
    url: str = MODELS_DEV_URL,
) -> dict[str, dict[str, Any]]:
    """Fetch models.dev metadata, returning {} on any network or payload error."""
    try:
        with client_scope(client) as http:
            response = http.get(url)
            response.raise_for_status()
            return parse_models_dev(response.json())
    except httpx.HTTPError, ValueError:
        return {}

"""Read the console's client-side request-estimate profiles.

The Go models API does not publish the per-model token profile the console uses for its
"Est. requests / 5 hr" column (it only has an optional ``go.requests``). The console
ships that profile table in its JavaScript bundle, so this module extracts it from the
live page. Profiles are applied only to model IDs returned by the models API.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass
from urllib.parse import urljoin

import httpx

from opencode_go_model_optimizer.ingest.http import client_scope

CONSOLE_PAGE_URL = "https://opencode.ai/console/go"

_ASSET_JS_RE = re.compile(r'"(/[^"]*_build/assets/[^"]+\.js)"')
_NUM = r"[0-9]+(?:\.[0-9]+)?(?:e[0-9]+)?"
_ENTRY_RE = re.compile(
    rf'(?:"([^"]+)"|([A-Za-z0-9_.-]+)):'
    rf"\{{input:({_NUM}),cached:({_NUM}),output:({_NUM})\}}"
)
_DEFAULT_RE = re.compile(rf"(?<!:){{input:({_NUM}),cached:({_NUM}),output:({_NUM})}}")


@dataclass(frozen=True)
class RequestProfiles:
    """Console token profiles: (input, cached read, output) tokens per request."""

    default: tuple[int, int, int]
    by_model: dict[str, tuple[int, int, int]]
    source_url: str = ""


def _number(value: str) -> int:
    return round(float(value))


def parse_console_profiles(js: str, source_url: str = "") -> RequestProfiles | None:
    """Extract the per-model profile map and the generic default from the bundle.

    Minified identifiers are not stable, so this matches the profile object shape rather
    than the variable names. The map is the set of keyed `{input,cached,output}` objects;
    the default is the standalone object of the same shape.
    """
    default_match = _DEFAULT_RE.search(js)
    if not default_match:
        return None
    by_model: dict[str, tuple[int, int, int]] = {}
    for match in _ENTRY_RE.finditer(js):
        model_id = match.group(1) or match.group(2)
        if not model_id:
            continue
        profile = (_number(match.group(3)), _number(match.group(4)), _number(match.group(5)))
        if all(part > 0 for part in profile):
            by_model[model_id] = profile
    if not by_model:
        return None
    return RequestProfiles(
        default=(
            _number(default_match.group(1)),
            _number(default_match.group(2)),
            _number(default_match.group(3)),
        ),
        by_model=by_model,
        source_url=source_url,
    )


def fetch_console_profiles(
    client: httpx.Client | None = None,
    page_url: str = CONSOLE_PAGE_URL,
    errors: list[str] | None = None,
) -> RequestProfiles | None:
    """Fetch the console page and extract its request-profile table.

    The console computes estimates in the browser, so this reads its current JS bundle
    rather than the server-rendered table (which can be stale before hydration). Returns
    ``None`` and appends a reason to ``errors`` when the page or bundle cannot be read.
    """
    try:
        with client_scope(client) as http:
            response = http.get(page_url)
            response.raise_for_status()
            scripts = [
                urljoin(page_url, src)
                for src in _ASSET_JS_RE.findall(response.text)
                if "/_build/assets/" in src
            ]
            # The profile table is in the page's own component bundle; try those first.
            scripts = sorted(
                dict.fromkeys(scripts),
                key=lambda url: 0 if "go" in posixpath.basename(url).lower() else 1,
            )
            for script_url in scripts:
                try:
                    script = http.get(script_url)
                    script.raise_for_status()
                except httpx.HTTPError:
                    continue
                profiles = parse_console_profiles(script.text, source_url=page_url)
                if profiles is not None:
                    return profiles
        reason = "console page contained no request-profile table"
    except (httpx.HTTPError, OSError) as exc:
        reason = f"console profile fetch failed ({type(exc).__name__}: {exc})"
    if errors is not None:
        errors.append(reason)
    return None


__all__ = [
    "CONSOLE_PAGE_URL",
    "RequestProfiles",
    "fetch_console_profiles",
    "parse_console_profiles",
]

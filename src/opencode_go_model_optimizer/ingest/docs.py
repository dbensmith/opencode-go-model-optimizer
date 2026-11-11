"""Parser for OpenCode Go MDX documentation."""

from __future__ import annotations

import functools
import re
from typing import Any

import httpx

from opencode_go_model_optimizer.ingest.http import client_scope

DOCS_URLS = [
    "https://raw.githubusercontent.com/anomalyco/opencode/dev/services/site/src/docs/content/console/go.mdx",
    "https://raw.githubusercontent.com/anomalyco/opencode/dev/services/www/src/docs/content/console/go.mdx",
]
DEFAULT_DOCS_URL = DOCS_URLS[0]

_TAG_RE = re.compile(r"<[^>]*>")
_PAREN_RE = re.compile(r"\([^)]*\)")
_NON_ALNUM_RE = re.compile(r"[^A-Za-z0-9]+")
_SEP_ROW_RE = re.compile(r"^:?-+:?$")
_STRIKE_RE = re.compile(r"~~.*?~~")
_PRICE_RE = re.compile(r"\$?([0-9]+(?:\.[0-9]+)?)")
_BOLD_DOLLAR_RE = re.compile(r"\*\*\$(\d+(?:\.\d+)?)\*\*")
_PLAIN_DOLLAR_RE = re.compile(r"\$(\d+(?:\.\d+)?)")


@functools.lru_cache(maxsize=512)
def norm_name(s: str) -> str:
    """Normalize model names for deterministic matching.

    Strips HTML tags, parenthetical suffixes like '(Off-Peak)' or '(50% off)',
    and non-alphanumeric characters, returning a lowercase string.
    """
    s = _TAG_RE.sub(" ", str(s))
    s = _PAREN_RE.sub(" ", s)
    s = _NON_ALNUM_RE.sub("", s)
    return s.lower()


def extract_plan_section(text: str, plan_id: str = "go-pricing", slot: str = "go") -> str:
    """Extract a slot subsection from within a PlanTabs block in MDX."""
    start_tag = f'<PlanTabs id="{plan_id}"'
    start_idx = text.find(start_tag)
    if start_idx == -1:
        return ""

    end_tabs = text.find("</PlanTabs>", start_idx)
    block = text[start_idx:end_tabs] if end_tabs != -1 else text[start_idx:]

    slot_tag = f'<div slot="{slot}">'
    s_idx = block.find(slot_tag)
    if s_idx == -1:
        return ""

    e_idx = block.find("</div>", s_idx)
    return block[s_idx:e_idx] if e_idx != -1 else block[s_idx:]


def parse_table_rows(section: str) -> list[list[str]]:
    """Parse Markdown table rows into cleaned cell lists, ignoring headers."""
    rows: list[list[str]] = []
    for line in section.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        content = line.strip("|")
        cells = [c.strip() for c in content.split("|")]
        if len(cells) < 2:
            continue
        first_col = cells[0].lower()
        if first_col == "model" or _SEP_ROW_RE.match(cells[0].replace(" ", "")):
            continue
        rows.append(cells)
    return rows


def parse_price_value(cell: str) -> float:
    """Extract numeric price from a cell, handling strikethroughs and 'Free'."""
    if not cell:
        return 0.0
    # Strip strikethroughs: ~~$4.00~~
    cleaned = _STRIKE_RE.sub("", cell).strip()
    if "free" in cleaned.lower():
        return 0.0
    # Match numeric dollar or decimal value
    match = _PRICE_RE.search(cleaned)
    if match:
        return float(match.group(1))
    return 0.0


def parse_go_pricing(
    mdx_content: str, plan: str = "go", *, fallback_to_go: bool = True
) -> dict[str, dict[str, Any]]:
    """Parse `<PlanTabs id="go-pricing">` `<div slot="{plan}">` table from MDX.

    Supported plan slots: 'go', 'go-plus', 'go-teams'.

    Returns a dict mapping normalized model name to a dictionary containing:
      - allowance: float (or float('inf') for unlimited)
      - output_cost: float
      - input_cost: float
      - cached_read_cost: float
      - raw_name: str
    """
    # Normalize plan alias (e.g., 'plus' -> 'go-plus', 'teams' -> 'go-teams')
    slot = plan if plan.startswith("go") else f"go-{plan}"
    section = extract_plan_section(mdx_content, plan_id="go-pricing", slot=slot)
    if not section and fallback_to_go:
        # Fallback to base 'go' if custom slot not found
        slot = "go"
        section = extract_plan_section(mdx_content, plan_id="go-pricing", slot="go")
    rows = parse_table_rows(section)

    pricing_by_norm_name: dict[str, dict[str, Any]] = {}

    for cells in rows:
        raw_name = cells[0]
        key = norm_name(raw_name)
        if not key:
            continue

        monthly_col = cells[-1]
        is_unlimited = "unlimited" in monthly_col.lower()
        dollar_match = _BOLD_DOLLAR_RE.search(monthly_col)

        if is_unlimited:
            allowance = float("inf")
        elif dollar_match:
            allowance = float(dollar_match.group(1))
        else:
            # Check for plain dollar amounts
            plain_dollar = _PLAIN_DOLLAR_RE.search(monthly_col)
            allowance = float(plain_dollar.group(1)) if plain_dollar else 0.0

        input_cost = parse_price_value(cells[1]) if len(cells) > 1 else 0.0
        output_cost = parse_price_value(cells[2]) if len(cells) > 2 else 0.0
        cached_read = parse_price_value(cells[3]) if len(cells) > 3 else 0.0
        cached_write = parse_price_value(cells[4]) if len(cells) > 4 else 0.0

        existing = pricing_by_norm_name.get(key)
        # Selection rule: highest allowance wins; if tied, cheapest output wins
        if (
            existing is None
            or allowance > existing["allowance"]
            or (allowance == existing["allowance"] and output_cost < existing["output_cost"])
        ):
            pricing_by_norm_name[key] = {
                "allowance": allowance,
                "output_cost": output_cost,
                "input_cost": input_cost,
                "cached_read_cost": cached_read,
                "cached_write_cost": cached_write,
                "raw_name": raw_name,
            }

    return pricing_by_norm_name


def fetch_docs_mdx(
    url: str = DEFAULT_DOCS_URL,
    client: httpx.Client | None = None,
) -> str:
    """Fetch raw OpenCode Go documentation MDX."""
    urls_to_try = DOCS_URLS if url == DEFAULT_DOCS_URL else [url]
    last_err: Exception | None = None
    with client_scope(client) as http:
        for target_url in urls_to_try:
            try:
                response = http.get(target_url)
                response.raise_for_status()
                return response.text
            except (httpx.HTTPError, OSError) as e:
                last_err = e
                continue
    if last_err:
        raise last_err
    raise httpx.RequestError("No URLs provided to fetch_docs_mdx")

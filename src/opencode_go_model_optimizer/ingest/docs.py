"""Parser for OpenCode Go MDX documentation."""

import functools
import re
from datetime import datetime
from typing import Any

import httpx

from opencode_go_model_optimizer.ingest.http import client_scope
from opencode_go_model_optimizer.ingest.models import PLANS, normalize_plan

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
    slot = normalize_plan(plan)
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


_COUNT_RE = re.compile(r"[0-9][0-9,]*(?:\.[0-9]+)?")


def _parse_count(cell: str) -> float | None:
    """Parse '6,320' to a float and 'Unlimited' to inf; None when unparseable."""
    if "unlimited" in cell.lower():
        return float("inf")
    match = _COUNT_RE.search(cell)
    return float(match.group(0).replace(",", "")) if match else None


def parse_request_estimates(mdx: str) -> dict[str, dict[str, dict[str, float]]]:
    """Parse `<PlanTabs id="go-requests">` into {plan: {display name: estimate dict}}.

    Columns are `Model | Requests per 5 hours | Requests per week | Requests per month`.
    Plans with no parseable rows are omitted.
    """
    result: dict[str, dict[str, dict[str, float]]] = {}
    for plan in PLANS:
        section = extract_plan_section(mdx, plan_id="go-requests", slot=plan)
        rows: dict[str, dict[str, float]] = {}
        for cells in parse_table_rows(section):
            if len(cells) < 4:
                continue
            name = _TAG_RE.sub(" ", cells[0]).strip()
            counts = [_parse_count(c) for c in cells[1:4]]
            if not name or any(c is None for c in counts):
                continue
            rows[name] = dict(zip(("per_5h", "per_week", "per_month"), counts, strict=True))  # type: ignore[arg-type]
        if rows:
            result[plan] = rows
    return result


def _extract_heading_section(mdx: str, heading: str) -> str:
    """Extract markdown text under a `## <heading>` section up to the next `## ` heading."""
    marker = f"## {heading}"
    idx = mdx.find(marker)
    if idx == -1:
        return ""
    rest = mdx[idx + len(marker) :]
    nxt = re.search(r"^## ", rest, re.MULTILINE)
    return rest[: nxt.start()] if nxt else rest


def parse_endpoints_table(mdx: str) -> dict[str, str]:
    """Parse the `## Endpoints` table into {display name: model id}.

    The table header is `| Model | Model ID | Endpoint | AI SDK Package |`. Returns {}
    when the section or table is missing.
    """
    section = _extract_heading_section(mdx, "Endpoints")
    if not section:
        return {}
    mapping: dict[str, str] = {}
    for cells in parse_table_rows(section):
        if len(cells) < 2:
            continue
        name = _TAG_RE.sub(" ", cells[0]).strip()
        model_id = cells[1].strip().strip("`").strip()
        if name and model_id and model_id.lower() != "model id":
            mapping[name] = model_id
    return mapping


_NOTE_RE = re.compile(r"^\s*[-*]\s+\*\*(.+?):\*\*\s*(.*)$")
_GLOBAL_RE = re.compile(r"requires\s+\W*global\W*\s+regions?", re.IGNORECASE)
_VALID_THROUGH_RE = re.compile(r"valid\s+through\s+([A-Za-z]+\s+\d{1,2},\s*\d{4})", re.IGNORECASE)
_RETENTION_RE = re.compile(r"(\d+)\s*days?", re.IGNORECASE)


def _parse_valid_through(text: str) -> str | None:
    match = _VALID_THROUGH_RE.search(text)
    if not match:
        return None
    try:
        return datetime.strptime(" ".join(match.group(1).split()), "%B %d, %Y").date().isoformat()
    except ValueError:
        return None


def parse_privacy(mdx: str) -> dict[str, dict[str, Any]]:
    """Parse the `## Privacy` table and notes into {display name: Privacy field dict}.

    Table columns are `Model | Model training | Data retention`. Bullet notes of the form
    `- **Name / Other:** text` attach to every row whose name equals or starts with a
    note key (so "Beta" covers all Beta rows). Returns {} when the section is missing.
    """
    section = _extract_heading_section(mdx, "Privacy")
    result: dict[str, dict[str, Any]] = {}
    for cells in parse_table_rows(section):
        if len(cells) < 3:
            continue
        name = _TAG_RE.sub(" ", cells[0]).strip()
        if not name:
            continue
        training = cells[1].replace("\\", "").replace("*", "").strip().lower()
        retention = cells[2].replace("\\", "").replace("*", "").strip().lower()
        days = _RETENTION_RE.search(retention)
        retention_days = int(days.group(1)) if days else None
        result[name] = {
            "trains_on_data": True
            if training.startswith("yes")
            else False
            if training.startswith("not used")
            else None,
            "retention_days": retention_days,
            "zdr": (retention_days == 0) if days else (False if "not zdr" in retention else None),
            "requires_global_region": False,
            "note": None,
            "agreement_valid_until": None,
        }

    for line in section.splitlines():
        note_match = _NOTE_RE.match(line)
        if not note_match:
            continue
        keys = [norm_name(k) for k in note_match.group(1).split("/")]
        text = note_match.group(2).strip()
        for name, row in result.items():
            if not any(k and norm_name(name).startswith(k) for k in keys):
                continue
            row["note"] = f"{row['note']} {text}" if row["note"] else text
            if _GLOBAL_RE.search(text):
                row["requires_global_region"] = True
            row["agreement_valid_until"] = (
                _parse_valid_through(text) or row["agreement_valid_until"]
            )
    return result


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

"""Parser for OpenCode Go MDX documentation."""

import functools
import re
from datetime import datetime
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


def _extract_heading_section(mdx: str, heading: str) -> str:
    """Extract markdown text under a `## <heading>` section up to the next `## ` heading."""
    marker = f"## {heading}"
    idx = mdx.find(marker)
    if idx == -1:
        return ""
    rest = mdx[idx + len(marker) :]
    nxt = re.search(r"^## ", rest, re.MULTILINE)
    return rest[: nxt.start()] if nxt else rest


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

    Current docs use `Model | Data retention`; collection of the training flag moved to the
    Go models API (`go.training`). Bullet notes of the form `- **Name / Other:** text` attach
    to every row whose name equals or starts with a note key (so "Beta" covers all Beta rows).
    Returns {} when the section is missing.
    """
    section = _extract_heading_section(mdx, "Privacy")
    result: dict[str, dict[str, Any]] = {}
    for cells in parse_table_rows(section):
        if len(cells) < 2:
            continue
        name = _TAG_RE.sub(" ", cells[0]).strip()
        if not name:
            continue
        retention = cells[1].replace("\\", "").replace("*", "").strip().lower()
        days = _RETENTION_RE.search(retention)
        retention_days = int(days.group(1)) if days else None
        result[name] = {
            "retention_days": retention_days,
            "zdr": (retention_days == 0) if days else (False if "not zdr" in retention else None),
            "requires_global_region": False,
            "note": None,
            "agreement_valid_until": None,
        }

    norm_by_name = {name: norm_name(name) for name in result}
    for line in section.splitlines():
        note_match = _NOTE_RE.match(line)
        if not note_match:
            continue
        keys = [norm_name(k) for k in note_match.group(1).split("/")]
        text = note_match.group(2).strip()
        for name, row in result.items():
            if not any(k and norm_by_name[name].startswith(k) for k in keys):
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

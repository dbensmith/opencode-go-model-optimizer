"""Verify the scraped console request profiles against the hydrated Go console.

The console page renders its model table client-side: the served HTML carries a
build-time snapshot (rows stay ``hidden`` with stale numbers even after hydration),
and the visible cells count up through an animation. The build-time snapshot and hidden
rows are therefore not valid evidence.

This tool drives a real headless browser to the console's ``#limits`` anchor (which
renders every model row directly — no featured subset, no count-labelled button),
waits for hydration, and reads the final values from the rendered rows'
screen-reader (``sr-only``) spans via ``innerText`` — React updates them in the same render
that reveals a row, so no animation wait is needed. It then checks that our
computed estimates (Go models API allowances + the profiles scraped from the console's
JS bundle) reproduce those numbers exactly, and requires **full coverage**: the console
hydrates every model the API publishes, so any API model missing from the rendered rows
— or any rendered row missing from the API — is a failure, not a partial pass. If the
anchor ever stops revealing all rows, a count-agnostic ``VIEW ALL`` click is the
fallback. Unless ``--fast`` is given it also waits for the count-up animation to settle
and fails loudly if the visible cells ever disagree with the screen-reader values.

Usage:
    uv run python scripts/verify_console_hydration.py [--timeout 90] [--min-rows 5]

Exits 0 when every rendered row matches and at least ``--min-rows`` rows were compared;
1 otherwise. Requires the dev dependency group (``uv sync``) and a Playwright Chromium
build (``uv run playwright install --with-deps chromium``). On Linux, missing host
libraries trigger one automatic dependency-install attempt through Playwright; failures
report the exact command to run manually.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from opencode_go_model_optimizer.ingest.catalogue import (
    apply_request_estimates,
    fetch_catalogue,
)
from opencode_go_model_optimizer.ingest.console import (
    CONSOLE_PAGE_URL,
    RequestProfiles,
    fetch_console_profiles,
)
from opencode_go_model_optimizer.ingest.snapshot import apply_plan

# The #limits anchor deep-links to the full model table: every API model row renders
# immediately at hydration, bypassing the featured subset and its count-labelled
# "VIEW ALL N MODELS" button entirely — nothing count-dependent, nothing to click.
CONSOLE_LIMITS_URL = f"{CONSOLE_PAGE_URL}#limits"

SETTLE_POLLS = 3  # consecutive identical reads required before values count as settled
POLL_INTERVAL = 0.75


class VerifyError(Exception):
    """Fatal verification error with actionable guidance."""


def _fail(msg: str) -> int:
    print(f"ERROR: {msg}", file=sys.stderr)
    return 1


# --- Browser side ---------------------------------------------------------------


def _reveal_all_rows(page, timeout: float) -> None:
    """Fallback: expand the table if the #limits anchor stopped revealing every row.

    The anchor URL renders all model rows directly; if that ever changes, click the
    console's ``VIEW ALL N MODELS`` button (matched count-agnostically) so every API
    model is rendered. Unexpanded rows stay ``hidden`` with stale prerendered values,
    so partial coverage is a verification failure, not a page property.
    """
    hidden = page.eval_on_selector_all(
        "tr[data-model]", "els => els.filter(e => e.hasAttribute('hidden')).length"
    )
    if not hidden:
        return
    button = page.get_by_role("button", name=re.compile(r"view all \d+ models", re.IGNORECASE))
    if button.count() == 0:
        raise VerifyError(
            f"{hidden} model row(s) stay hidden and no 'VIEW ALL' button was found; "
            "the page structure changed — inspect manually"
        )
    button.first.click()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        hidden = page.eval_on_selector_all(
            "tr[data-model]", "els => els.filter(e => e.hasAttribute('hidden')).length"
        )
        if not hidden:
            return
        time.sleep(POLL_INTERVAL)
    raise VerifyError(f"{hidden} model row(s) never rendered after 'VIEW ALL' within {timeout:g}s")


def _collect_final_rows(page) -> list[dict[str, Any]]:
    """Read the final hydrated values from each rendered row's screen-reader spans.

    React updates the ``sr-only`` spans in the same hydrated render that un-hides the
    row, so they hold the final numbers immediately — no animation wait. (On rows that
    stay ``hidden`` the prerendered stale values remain, which is why only rendered
    rows are ever read.) Falls back to ``raw_sr_*`` fields when the text cannot be
    parsed so the caller can fail with the actual page content.
    """
    return page.eval_on_selector_all(
        "tr[data-model]:not([hidden])",
        """rows => rows.map(e => {
        const sr = (kind) => e.querySelector(
            `td[data-allowance-type="${kind}"] span.sr-only`
        )?.innerText.trim() ?? null;
        return {
            model: e.getAttribute('data-model'),
            requests_sr: sr('requests'),
            usage_sr: sr('usage'),
        };
    })""",
    )


def _parse_sr_values(text: str | None) -> tuple[float | None, float | None]:
    """'Go: 2,640; Go Plus: 5,280' -> (2640.0, 5280.0); dollar/∞ forms accepted."""
    if not text:
        return None, None
    match = re.search(r"Go:\s*([^;]+?);\s*Go Plus:\s*(.+)$", text, re.IGNORECASE)
    if not match:
        return None, None
    return _parse_display_number(match.group(1)), _parse_display_number(match.group(2))


def _finalize_rows(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Parse the sr-only texts into the same row shape the comparison consumes."""
    rows: list[dict[str, Any]] = []
    for entry in raw:
        r_go, r_plus = _parse_sr_values(entry.get("requests_sr"))
        u_go, u_plus = _parse_sr_values(entry.get("usage_sr"))
        rows.append(
            {
                "model": entry.get("model"),
                "requests_go": r_go,
                "requests_plus": r_plus,
                "usage_go": u_go,
                "usage_plus": u_plus,
                "_unparsed": (entry.get("requests_sr") is not None and None in (r_go, r_plus))
                or (entry.get("usage_sr") is not None and None in (u_go, u_plus)),
                "_raw": entry,
            }
        )
    return rows


def _collect_displayed_rows(page) -> list[dict[str, Any]]:
    """Read the animated visible cells of every rendered row, per plan and value type."""
    return page.eval_on_selector_all(
        "tr[data-model]:not([hidden])",
        """rows => rows.map(e => {
        const cell = (kind, plan) => e.querySelector(
            `td[data-allowance-type="${kind}"] span[data-plan="${plan}"]`
            + ' span[data-layer="current-value"]'
        );
        return {
            model: e.getAttribute('data-model'),
            requests_go: cell('requests', 'go')?.innerText.trim() ?? null,
            requests_plus: cell('requests', 'go-plus')?.innerText.trim() ?? null,
            usage_go: cell('usage', 'go')?.innerText.trim() ?? null,
            usage_plus: cell('usage', 'go-plus')?.innerText.trim() ?? null,
        };
    })""",
    )


def _wait_until_settled(page, timeout: float) -> list[dict[str, Any]]:
    """Poll the visible cells until they stop changing (the scene animates a count-up)."""
    page.wait_for_selector("tr[data-model]:not([hidden])", timeout=timeout * 1000)
    deadline = time.monotonic() + timeout
    previous: str | None = None
    stable = 0
    while time.monotonic() < deadline:
        rows = _collect_displayed_rows(page)
        serialized = json.dumps(rows, sort_keys=True)
        if serialized == previous:
            stable += 1
            if stable >= SETTLE_POLLS:
                return rows
        else:
            stable = 0
            previous = serialized
        time.sleep(POLL_INTERVAL)
    raise VerifyError(
        f"console values did not settle within {timeout:g}s (animation still changing); "
        "refusing to confirm the visible cells — retry or raise --timeout"
    )


def _cross_check(finals: list[dict[str, Any]], displayed: list[dict[str, Any]]) -> None:
    """Assert the animation landed on the sr-only final values for every rendered row."""
    by_model = {row["model"]: row for row in displayed}
    diverged: list[str] = []
    for final in finals:
        shown = by_model.get(final["model"])
        if shown is None:
            diverged.append(f"{final['model']}: missing from settled visible rows")
            continue
        for field in ("requests_go", "requests_plus", "usage_go", "usage_plus"):
            if final[field] != _parse_display_number(shown.get(field)):
                diverged.append(
                    f"{final['model']}.{field}: sr-only={final[field]} displayed={shown.get(field)}"
                )
    if diverged:
        raise VerifyError(
            "the settled visible cells do not match the sr-only final values "
            f"({'; '.join(diverged)}); the page structure changed — inspect manually"
        )


def _browser_registry_pins() -> dict[str, str]:
    """Point Playwright at the user's browser cache when pytest overrides XDG_CACHE_HOME."""
    if os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        return {}
    xdg = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "ms-playwright"
    real = Path.home() / ".cache" / "ms-playwright"
    if not xdg.is_dir() and real.is_dir():
        return {"PLAYWRIGHT_BROWSERS_PATH": str(real)}
    return {}


def _start_playwright():
    """Start the Playwright driver with the browser registry pinned via process env.

    pytest overrides ``XDG_CACHE_HOME`` per run, which would hide browsers installed
    under the user's real cache; pin ``PLAYWRIGHT_BROWSERS_PATH`` for the driver spawn
    and restore the process env afterwards.
    """
    from playwright.sync_api import sync_playwright

    pins = _browser_registry_pins()
    saved = {k: os.environ.get(k) for k in pins}
    os.environ.update(pins)
    try:
        return sync_playwright().start()
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _install_browser_dependencies() -> None:
    """Install Chromium and its OS dependencies once through Playwright's package manager."""
    command = [sys.executable, "-m", "playwright", "install", "--with-deps", "chromium"]
    try:
        subprocess.run(
            command,
            check=True,
            env={**os.environ, **_browser_registry_pins()},
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise VerifyError(
            f"could not install Chromium system dependencies; run `{shlex.join(command)}` and retry"
        ) from exc


def _rendered_rows(timeout: float, fast: bool = False) -> list[dict[str, Any]]:
    try:
        from playwright.sync_api import Error as PlaywrightError
    except ImportError as exc:
        raise VerifyError(
            "playwright is not installed; run `uv sync` (dev dependency group)"
        ) from exc
    p = _start_playwright()
    try:
        try:
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
        except PlaywrightError as exc:
            message = str(exc)
            if "doesn't exist" in message or "Executable doesn't exist" in message:
                raise VerifyError(
                    "no Playwright Chromium build found; run "
                    "`uv run playwright install --with-deps chromium`"
                ) from exc
            missing_dependencies = "Host system is missing dependencies" in message or (
                "error while loading shared libraries" in message
            )
            if sys.platform == "linux" and missing_dependencies:
                _install_browser_dependencies()
                browser = p.chromium.launch(
                    headless=True,
                    args=["--no-sandbox", "--disable-dev-shm-usage"],
                )
            else:
                raise
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 2400})
            page.goto(CONSOLE_LIMITS_URL, timeout=timeout * 1000, wait_until="networkidle")
            # The sr-only spans of rendered rows hold the final hydrated values in the
            # same React render that un-hides the row, so they need no animation wait.
            page.wait_for_selector("tr[data-model]:not([hidden])", timeout=timeout * 1000)
            _reveal_all_rows(page, timeout)
            finals = _finalize_rows(_collect_final_rows(page))
            unparsed = [r["model"] for r in finals if r["_unparsed"]]
            if unparsed:
                raise VerifyError(
                    f"could not parse the sr-only values of rendered row(s) {unparsed}; "
                    "page structure changed — inspect manually"
                )
            if not fast:
                # Guard: the visible cells animate a count-up; confirm they land on the
                # sr-only finals so a future divergence fails loudly instead of silently.
                displayed = _wait_until_settled(page, timeout)
                _cross_check(finals, displayed)
            return finals
        finally:
            browser.close()
    finally:
        p.stop()


# --- Comparison -----------------------------------------------------------------


def _lim_ok(displayed: float, allowance: float | None) -> bool:
    if displayed == float("inf"):
        return allowance is None or allowance == float("inf")
    return allowance is not None and abs(allowance - displayed) < 0.5


def _compare(rows: list[dict[str, Any]], min_rows: int, profiles: RequestProfiles) -> int:
    models = apply_request_estimates(fetch_catalogue(), profiles)
    by_plan = {
        plan: {m.id: m for m in apply_plan([m.model_copy(deep=True) for m in models], plan)}
        for plan in ("go", "go-plus")
    }
    expected = {m.id for m in models if m.status == "active" and m.model_type == "chat"}
    rendered = {str(row["model"]) for row in rows}

    failures = 0
    missing = sorted(expected - rendered)
    if missing:
        print(
            f"ERROR: {len(missing)} API model(s) were not rendered by the console: "
            + ", ".join(missing),
            file=sys.stderr,
        )
        failures += 1
    extra = sorted(rendered - expected)
    if extra:
        print(
            f"ERROR: {len(extra)} rendered model(s) are not in the API catalogue: "
            + ", ".join(extra),
            file=sys.stderr,
        )
        failures += 1

    matched = mismatches = 0
    print(f"{'model':34s} {'go (rendered/ours)':>24s} {'go-plus (rendered/ours)':>26s}  verdict")
    for row in rows:
        mid = row["model"]
        r_go, r_plus = row["requests_go"], row["requests_plus"]
        u_go, u_plus = row["usage_go"], row["usage_plus"]
        if None in (r_go, r_plus, u_go, u_plus):
            print(f"{mid:34s} rendered row is missing cells — page structure changed?")
            mismatches += 1
            continue
        go_model = by_plan["go"].get(mid)
        plus_model = by_plan["go-plus"].get(mid)
        if go_model is None or plus_model is None:
            print(f"{mid:34s} rendered but not in the API catalogue")
            mismatches += 1
            continue

        ours_go = go_model.request_estimate.per_5h if go_model.request_estimate else None
        ours_plus = plus_model.request_estimate.per_5h if plus_model.request_estimate else None
        row_ok = (
            ours_go == r_go
            and ours_plus == r_plus
            and _lim_ok(u_go, go_model.monthly_allowance)
            and _lim_ok(u_plus, plus_model.monthly_allowance)
        )
        matched, mismatches = (matched + 1, mismatches) if row_ok else (matched, mismatches + 1)
        print(
            f"{mid:34s} {r_go!s:>10}/{ours_go!s:<12} "
            f"{r_plus!s:>10}/{ours_plus!s:<14}  "
            f"{'OK' if row_ok else '** MISMATCH **'}  "
            f"(limits {u_go:g}/{u_plus:g} vs API "
            f"{go_model.monthly_allowance!s}/{plus_model.monthly_allowance!s})"
        )

    print(
        f"\nrendered={len(rows)} matched={matched} mismatches={mismatches} "
        f"api-active-chat={len(expected)} coverage={len(rendered & expected)}/{len(expected)}"
    )
    if matched + mismatches < min_rows:
        print(
            f"ERROR: only {matched + mismatches} rendered rows were comparable; expected at "
            f"least {min_rows} (page structure or plan toggle changed?)",
            file=sys.stderr,
        )
        failures += 1
    return 1 if (mismatches or failures) else 0


def _parse_display_number(text: str | None) -> float | None:
    """'2,640' -> 2640.0, '$200' -> 200.0, '∞' -> inf; None when unreadable."""
    if text is None:
        return None
    s = text.strip().replace(",", "").replace("$", "")
    if not s:
        return None
    if "∞" in s or s.lower() in ("unlimited", "inf"):
        return float("inf")
    try:
        return float(s)
    except ValueError:
        return None


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--timeout", type=float, default=90.0, help="seconds to wait for load/hydration/settle"
    )
    parser.add_argument(
        "--min-rows", type=int, default=5, help="minimum rendered rows that must be compared"
    )
    parser.add_argument(
        "--fast",
        action="store_true",
        help="skip the settle cross-check; compare the sr-only finals without waiting "
        "for the count-up animation",
    )
    args = parser.parse_args(argv)

    try:
        profiles = fetch_console_profiles()
    except Exception as exc:
        return _fail(f"could not scrape the console profiles bundle: {exc}")
    if profiles is None:
        return _fail("could not scrape the console request profiles; there is nothing to verify")
    print(f"scraped {len(profiles.by_model)} profiles (default {profiles.default})")

    try:
        rows = _rendered_rows(args.timeout, fast=args.fast)
    except VerifyError as exc:
        return _fail(str(exc))
    except Exception as exc:
        return _fail(f"browser verification failed: {type(exc).__name__}: {exc}")
    if not rows:
        return _fail("the hydrated console rendered no model rows")
    return _compare(rows, args.min_rows, profiles)


if __name__ == "__main__":
    raise SystemExit(main())

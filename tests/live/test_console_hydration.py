"""Opt-in headless-browser verification of the console scrape against the hydrated page.

Unlike the other live tests this needs a real browser, so it is gated separately:
set ``OCGO_BROWSER=1`` (plus the dev dependency group and ``playwright install chromium``).
The served HTML carries stale build-time numbers, so nothing short of the settled,
rendered cells is comparable — see scripts/verify_console_hydration.py.
"""

import importlib.util
import os
from pathlib import Path

import pytest

if os.environ.get("OCGO_BROWSER") != "1":
    pytest.skip("console hydration check needs OCGO_BROWSER=1", allow_module_level=True)

pytestmark = pytest.mark.browser

_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "verify_console_hydration.py"
_SPEC = importlib.util.spec_from_file_location("verify_console_hydration", _SCRIPT_PATH)
assert _SPEC and _SPEC.loader
_MOD = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MOD)


def test_console_hydration_matches():
    """Every rendered console row must equal our computed estimates on both plans."""
    assert _MOD.main([]) == 0

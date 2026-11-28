"""Offline unit tests and opt-in browser verification for console hydration.

The unit tests exercise the verifier with mocks and fakes. The browser check needs a real
browser, so it is gated separately: set ``OCGO_BROWSER=1`` (plus the dev dependency group
and ``playwright install --with-deps chromium``). The served HTML carries stale build-time
numbers, so the verifier reads the hydrated rows' screen-reader values — see
scripts/verify_console_hydration.py.
"""

import importlib.util
import os
import shlex
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

from opencode_go_model_optimizer.ingest.console import RequestProfiles

_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "verify_console_hydration.py"
_SPEC = importlib.util.spec_from_file_location("verify_console_hydration", _SCRIPT_PATH)
assert _SPEC and _SPEC.loader
_MOD = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MOD)


def test_main_fetches_console_profiles_once(monkeypatch: pytest.MonkeyPatch) -> None:
    profiles = RequestProfiles(default=(800, 65_000, 250), by_model={"a": (1, 1, 1)})
    catalogue: list[object] = []
    fetch_profiles = Mock(return_value=profiles)
    apply_estimates = Mock(return_value=[])
    monkeypatch.setattr(_MOD, "fetch_console_profiles", fetch_profiles)
    monkeypatch.setattr(_MOD, "fetch_catalogue", Mock(return_value=catalogue))
    monkeypatch.setattr(_MOD, "apply_request_estimates", apply_estimates)
    monkeypatch.setattr(
        _MOD,
        "_rendered_rows",
        Mock(
            return_value=[
                {
                    "model": "a",
                    "requests_go": 10.0,
                    "requests_plus": 20.0,
                    "usage_go": 15.0,
                    "usage_plus": 60.0,
                }
            ]
        ),
    )

    assert _MOD.main(["--fast", "--min-rows", "0"]) == 1
    fetch_profiles.assert_called_once_with()
    apply_estimates.assert_called_once_with(catalogue, profiles)


def test_cross_check_fails_when_displayed_row_is_missing() -> None:
    finals = [
        {
            "model": "m1",
            "requests_go": 100.0,
            "requests_plus": 200.0,
            "usage_go": 15.0,
            "usage_plus": 60.0,
        }
    ]

    with pytest.raises(_MOD.VerifyError, match="m1: missing from settled visible rows"):
        _MOD._cross_check(finals, [])


def test_install_browser_dependencies_uses_active_python(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = Mock()
    monkeypatch.setattr(
        _MOD, "_browser_registry_pins", lambda: {"PLAYWRIGHT_BROWSERS_PATH": "/cached"}
    )
    monkeypatch.setattr(_MOD.subprocess, "run", run)

    _MOD._install_browser_dependencies()

    command = [_MOD.sys.executable, "-m", "playwright", "install", "--with-deps", "chromium"]
    run.assert_called_once_with(
        command,
        check=True,
        env={**os.environ, "PLAYWRIGHT_BROWSERS_PATH": "/cached"},
    )


def test_install_browser_dependencies_failure_reports_exact_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command = [_MOD.sys.executable, "-m", "playwright", "install", "--with-deps", "chromium"]
    monkeypatch.setattr(_MOD, "_browser_registry_pins", lambda: {})
    monkeypatch.setattr(
        _MOD.subprocess,
        "run",
        Mock(side_effect=subprocess.CalledProcessError(1, command)),
    )

    with pytest.raises(_MOD.VerifyError) as exc_info:
        _MOD._install_browser_dependencies()

    assert f"`{shlex.join(command)}`" in str(exc_info.value)


@pytest.mark.parametrize(
    "message",
    [
        "Host system is missing dependencies to run browsers.",
        "BrowserType.launch: error while loading shared libraries: libasound.so.2",
    ],
)
def test_rendered_rows_installs_missing_dependencies_once(
    monkeypatch: pytest.MonkeyPatch, message: str
) -> None:
    from playwright.sync_api import Error as PlaywrightError

    playwright = Mock()
    playwright.chromium.launch.side_effect = [
        PlaywrightError(message),
        RuntimeError("retry reached"),
    ]
    install = Mock()
    monkeypatch.setattr(_MOD.sys, "platform", "linux")
    monkeypatch.setattr(_MOD, "_start_playwright", lambda: playwright)
    monkeypatch.setattr(_MOD, "_install_browser_dependencies", install)

    with pytest.raises(RuntimeError, match="retry reached"):
        _MOD._rendered_rows(1)

    assert playwright.chromium.launch.call_count == 2
    install.assert_called_once_with()
    playwright.stop.assert_called_once_with()


@pytest.mark.browser
@pytest.mark.skipif(
    os.environ.get("OCGO_BROWSER") != "1",
    reason="console hydration check needs OCGO_BROWSER=1",
)
def test_console_hydration_matches():
    """Every rendered console row must equal our computed estimates on both plans."""
    assert _MOD.main([]) == 0

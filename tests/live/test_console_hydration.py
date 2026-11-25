"""Offline unit tests and opt-in browser verification for console hydration.

The unit tests exercise the verifier with mocks and fakes. The browser check needs a real
browser, so it is gated separately: set ``OCGO_BROWSER=1`` (plus the dev dependency group
and ``playwright install chromium``). The served HTML carries stale build-time numbers,
so the verifier reads the hydrated rows' screen-reader values — see
scripts/verify_console_hydration.py.
"""

import importlib.util
import os
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


def _cache_lib_dir(cache: Path, package: str, *libraries: str) -> Path:
    lib_dir = cache / package / "usr" / "lib" / "x86_64-linux-gnu"
    lib_dir.mkdir(parents=True)
    for library in libraries:
        (lib_dir / library).touch()
    return lib_dir


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


@pytest.mark.parametrize(
    ("current", "expected"),
    [
        (None, "/cached/lib"),
        ("", "/cached/lib"),
        ("/system/lib::/other:", "/cached/lib:/system/lib:/other"),
    ],
)
def test_browser_env_filters_empty_library_path_entries(
    monkeypatch: pytest.MonkeyPatch, current: str | None, expected: str
) -> None:
    if current is None:
        monkeypatch.delenv("LD_LIBRARY_PATH", raising=False)
    else:
        monkeypatch.setenv("LD_LIBRARY_PATH", current)

    env = _MOD._browser_env(["/cached/lib", ""])

    assert env is not None
    assert env["LD_LIBRARY_PATH"] == expected


def test_browser_env_is_not_overridden_without_bootstrap_dirs() -> None:
    assert _MOD._browser_env([]) is None


def test_bootstrap_reuses_cache_when_all_missing_libs_are_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "browser-libs"
    cache.mkdir()
    cached_dir = _cache_lib_dir(cache, "cached", "libasound.so.2")
    run = Mock()
    monkeypatch.setattr(_MOD, "_LIB_CACHE", cache)
    monkeypatch.setattr(_MOD, "_missing_shared_libs", lambda executable: ["libasound.so.2"])
    monkeypatch.setattr(_MOD.subprocess, "run", run)

    assert _MOD._bootstrap_lib_dirs("chromium") == [str(cached_dir)]
    run.assert_not_called()


def test_bootstrap_rejects_unmapped_library_missing_from_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "browser-libs"
    cache.mkdir()
    _cache_lib_dir(cache, "cached", "libasound.so.2")
    monkeypatch.setattr(_MOD, "_LIB_CACHE", cache)
    monkeypatch.setattr(
        _MOD,
        "_missing_shared_libs",
        lambda executable: ["libasound.so.2", "libnew.so.1"],
    )

    with pytest.raises(_MOD.VerifyError, match=r"libnew\.so\.1"):
        _MOD._bootstrap_lib_dirs("chromium")


def test_bootstrap_downloads_library_missing_from_incomplete_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "browser-libs"
    cache.mkdir()
    cached_dir = _cache_lib_dir(cache, "cached", "libasound.so.2")
    new_dir = cache / "libnew" / "usr" / "lib" / "x86_64-linux-gnu"
    missing = ["libasound.so.2", "libnew.so.1"]
    package_libs = {"libnew": "libnew.so.1"}
    commands: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        if command[:2] == ["apt-get", "download"]:
            (cache / f"{command[2]}_1.deb").touch()
        elif command[:2] == ["dpkg-deb", "-x"]:
            package = Path(command[2]).name.removesuffix("_1.deb")
            _cache_lib_dir(cache, package, package_libs[package])
        else:
            raise AssertionError(f"unexpected command: {command}")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(_MOD, "_LIB_CACHE", cache)
    monkeypatch.setattr(_MOD, "_missing_shared_libs", lambda executable: missing)
    monkeypatch.setitem(_MOD._LIB_DEB_PACKAGES, "libnew.so.1", ("libnew",))
    monkeypatch.setattr(_MOD.shutil, "which", lambda executable: f"/usr/bin/{executable}")
    monkeypatch.setattr(_MOD.subprocess, "run", fake_run)

    lib_dirs = _MOD._bootstrap_lib_dirs("chromium")

    assert lib_dirs == [str(cached_dir), str(new_dir)]
    assert not _MOD._unresolved_shared_libs(missing, lib_dirs)
    assert commands == [
        ["apt-get", "download", "libnew"],
        ["dpkg-deb", "-x", str(cache / "libnew_1.deb"), str(cache / "libnew")],
    ]


def test_bootstrap_accepts_unmapped_library_already_in_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "browser-libs"
    cache.mkdir()
    cached_dir = _cache_lib_dir(cache, "cached", "libfoo.so.1")
    new_dir = cache / "libnew" / "usr" / "lib" / "x86_64-linux-gnu"
    missing = ["libfoo.so.1", "libnew.so.1"]
    commands: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        if command[:2] == ["apt-get", "download"]:
            (cache / f"{command[2]}_1.deb").touch()
        elif command[:2] == ["dpkg-deb", "-x"]:
            _cache_lib_dir(cache, "libnew", "libnew.so.1")
        else:
            raise AssertionError(f"unexpected command: {command}")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(_MOD, "_LIB_CACHE", cache)
    monkeypatch.setattr(_MOD, "_missing_shared_libs", lambda executable: missing)
    monkeypatch.setitem(_MOD._LIB_DEB_PACKAGES, "libnew.so.1", ("libnew",))
    monkeypatch.setattr(_MOD.shutil, "which", lambda executable: f"/usr/bin/{executable}")
    monkeypatch.setattr(_MOD.subprocess, "run", fake_run)

    lib_dirs = _MOD._bootstrap_lib_dirs("chromium")

    assert set(lib_dirs) == {str(cached_dir), str(new_dir)}
    assert not _MOD._unresolved_shared_libs(missing, lib_dirs)
    assert [command[2] for command in commands if command[0] == "apt-get"] == ["libnew"]


def test_bootstrap_rejects_download_without_requested_soname(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "browser-libs"
    cache.mkdir()
    missing = ["libnew.so.1"]
    commands: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        if command[:2] == ["apt-get", "download"]:
            (cache / f"{command[2]}_1.deb").touch()
        elif command[:2] == ["dpkg-deb", "-x"]:
            _cache_lib_dir(cache, "libnew", "libwrong.so.1")
        else:
            raise AssertionError(f"unexpected command: {command}")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(_MOD, "_LIB_CACHE", cache)
    monkeypatch.setattr(_MOD, "_missing_shared_libs", lambda executable: missing)
    monkeypatch.setitem(_MOD._LIB_DEB_PACKAGES, "libnew.so.1", ("libnew",))
    monkeypatch.setattr(_MOD.shutil, "which", lambda executable: f"/usr/bin/{executable}")
    monkeypatch.setattr(_MOD.subprocess, "run", fake_run)

    with pytest.raises(_MOD.VerifyError, match=r"libnew\.so\.1"):
        _MOD._bootstrap_lib_dirs("chromium")

    assert commands == [
        ["apt-get", "download", "libnew"],
        ["dpkg-deb", "-x", str(cache / "libnew_1.deb"), str(cache / "libnew")],
    ]


@pytest.mark.browser
@pytest.mark.skipif(
    os.environ.get("OCGO_BROWSER") != "1",
    reason="console hydration check needs OCGO_BROWSER=1",
)
def test_console_hydration_matches():
    """Every rendered console row must equal our computed estimates on both plans."""
    assert _MOD.main([]) == 0

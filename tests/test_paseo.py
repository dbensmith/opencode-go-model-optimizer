"""Tests for the Paseo worktree setup hook and its Renovate-managed uv pin."""

import json
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def test_paseo_json_has_setup_and_scripts() -> None:
    config_path = _ROOT / "paseo.json"
    assert config_path.is_file()
    config = json.loads(config_path.read_text(encoding="utf-8"))

    setup = config["worktree"]["setup"]
    assert "set -e" in setup
    assert "command -v uv" in setup
    assert "uv sync --locked" in setup
    assert "uv run opencode-go-model-optimizer --version" in setup
    assert "uv run pytest --collect-only -qq" in setup
    # The installer must receive UV_NO_MODIFY_PATH on the sh side of the pipeline;
    # a var prefix on the curl side never reaches the installer (regression guard).
    assert "| env UV_NO_MODIFY_PATH=1 sh" in setup

    scripts = config["scripts"]
    assert scripts["test"]["command"] == "uv run pytest"
    assert scripts["test-live"]["command"] == "OCGO_LIVE=1 uv run pytest tests/live -m live"
    assert scripts["lint"]["command"] == "uv run ruff check"
    assert scripts["format"]["command"] == "uv run ruff format"
    assert "opencode-go-model-optimizer select --format table" in scripts["select"]["command"]

    # The docs preview must be a service that binds the assigned port, not a hard-coded one.
    docs_service = scripts["docs"]
    assert docs_service["type"] == "service"
    assert "$PASEO_PORT" in docs_service["command"]


def test_worktreeinclude_patterns_are_gitignored() -> None:
    include_path = _ROOT / ".worktreeinclude"
    assert include_path.is_file()
    include_text = include_path.read_text(encoding="utf-8")

    patterns = [
        line.strip()
        for line in include_text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert patterns, ".worktreeinclude declares no patterns"

    # .worktreeinclude only copies files that Git already ignores; a pattern that is
    # not gitignored silently copies nothing, so keep the two files consistent.
    gitignore_text = (_ROOT / ".gitignore").read_text(encoding="utf-8")
    for pattern in patterns:
        assert pattern in gitignore_text, (
            f".worktreeinclude pattern {pattern!r} is not in .gitignore and would never copy"
        )


def test_renovate_tracks_uv_pin_in_paseo_hook() -> None:
    renovate_path = _ROOT / ".github" / "renovate.json"
    paseo_path = _ROOT / "paseo.json"
    assert renovate_path.is_file()
    assert paseo_path.is_file()

    renovate_data = json.loads(renovate_path.read_text(encoding="utf-8"))
    paseo_text = paseo_path.read_text(encoding="utf-8")

    paseo_managers = [
        cm
        for cm in renovate_data.get("customManagers", [])
        if any("paseo" in p for p in cm.get("managerFilePatterns", []))
    ]
    assert paseo_managers, "Renovate custom manager for paseo.json not found"
    manager = paseo_managers[0]
    assert manager.get("depNameTemplate") == "astral-sh/uv"
    assert manager.get("datasourceTemplate") == "github-releases"

    # Convert JS named capture group (?<name>...) to Python (?P<name>...) for testing with re
    py_regex = re.compile(
        re.sub(r"\(\?<([a-zA-Z_][a-zA-Z0-9_]*)>", r"(?P<\1>", manager["matchStrings"][0])
    )
    versions = {m.group("currentValue") for m in py_regex.finditer(paseo_text)}
    assert versions, "paseo uv customManager regex did not match paseo.json!"
    assert len(versions) == 1, f"Expected a single pinned uv version, got {sorted(versions)}"


def test_paseo_uv_pin_matches_ci_pin() -> None:
    paseo_text = (_ROOT / "paseo.json").read_text(encoding="utf-8")
    action_path = _ROOT / ".github" / "actions" / "setup-env" / "action.yml"
    action_text = action_path.read_text(encoding="utf-8")

    paseo_versions = set(re.findall(r"astral\.sh/uv/([^/\"'\s]+)/install\.sh", paseo_text))
    ci_versions = set(re.findall(r'version:\s*"([^"]+)"', action_text))

    assert len(paseo_versions) == 1, (
        f"Expected one uv pin in paseo.json, got {sorted(paseo_versions)}"
    )
    assert len(ci_versions) == 1, f"Expected one uv pin in action.yml, got {sorted(ci_versions)}"
    assert paseo_versions == ci_versions, (
        "paseo.json hook and setup-uv CI action pin different uv versions; "
        "update them together (Renovate's 'uv toolchain' group normally does this)"
    )

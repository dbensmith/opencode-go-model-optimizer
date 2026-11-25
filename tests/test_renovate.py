"""Repo-wide checks that Renovate's customManagers still find every pin.

A regex manager whose matchStrings stop matching its file makes Renovate
silently stop updating that dependency, and the same dependency can be pinned
in several files (e.g. the uv version in CI and in paseo.json) that Renovate
groups are supposed to move together. Both properties are checked here for
every customManager in .github/renovate.json.
"""

import json
import re
import subprocess
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def _tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=_ROOT, check=True, capture_output=True, text=True
    ).stdout
    return [line for line in out.splitlines() if line]


def _file_pattern_regex(pattern: str) -> re.Pattern[str]:
    # Renovate wraps managerFilePatterns in JS-style /.../ delimiters.
    assert pattern.startswith("/") and pattern.endswith("/"), (
        f"managerFilePatterns entry is not /-delimited: {pattern!r}"
    )
    return re.compile(pattern[1:-1])


def _match_string_regex(js_regex: str) -> re.Pattern[str]:
    # Convert JS named capture groups (?<name>...) to Python (?P<name>...).
    return re.compile(re.sub(r"\(\?<([a-zA-Z_][a-zA-Z0-9_]*)>", r"(?P<\1>", js_regex))


def test_renovate_managers_pin_one_version_per_dependency() -> None:
    renovate = json.loads((_ROOT / ".github" / "renovate.json").read_text(encoding="utf-8"))
    managers = renovate.get("customManagers", [])
    assert managers, "renovate.json lost its customManagers"

    tracked = _tracked_files()
    pins: dict[str, set[str]] = {}

    for manager in managers:
        file_patterns = [_file_pattern_regex(p) for p in manager["managerFilePatterns"]]
        matchers = [_match_string_regex(m) for m in manager["matchStrings"]]

        # A manager must know the dependency's name from the regex or a template.
        assert "depNameTemplate" in manager or any(
            "(?<depName>" in m for m in manager["matchStrings"]
        ), f"Manager cannot name its dependency: {manager['description']}"

        matched_files = [f for f in tracked if any(p.search(f) for p in file_patterns)]
        assert matched_files, f"managerFilePatterns match no tracked file: {manager['description']}"

        matches = 0
        for name in matched_files:
            text = (_ROOT / name).read_text(encoding="utf-8")
            for matcher in matchers:
                for found in matcher.finditer(text):
                    matches += 1
                    dep = found.groupdict().get("depName") or manager["depNameTemplate"]
                    pins.setdefault(dep, set()).add(found.group("currentValue"))

        assert matches > 0, (
            "matchStrings no longer match their file(s); Renovate would silently "
            f"stop updating this dependency: {manager['description']}"
        )

    divergent = {dep: sorted(versions) for dep, versions in pins.items() if len(versions) > 1}
    assert not divergent, (
        "Dependencies pinned to multiple versions repo-wide; Renovate groups "
        f"should move them together: {divergent}"
    )

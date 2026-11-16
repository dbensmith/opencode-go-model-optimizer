"""Tests for Docsify site files, CI workflow, and Renovate custom managers regex matching."""

import json
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def test_docsify_files_exist() -> None:
    docs_dir = _ROOT / "docs"
    assert (docs_dir / ".nojekyll").is_file()
    assert (docs_dir / "_sidebar.md").is_file()
    assert (docs_dir / "README.md").is_file()
    assert (docs_dir / "index.html").is_file()

    index_content = (docs_dir / "index.html").read_text(encoding="utf-8")
    sidebar_content = (docs_dir / "_sidebar.md").read_text(encoding="utf-8")
    assert "window.$docsify" in index_content
    assert "name: 'OpenCode Go Optimizer'" in index_content
    assert "{{REPO_URL}}" in index_content
    assert "{{REPO_URL}}" in sidebar_content
    assert "{{ISSUES_URL}}" in sidebar_content
    assert "loadSidebar: true" in index_content
    assert "auto2top: true" in index_content
    assert "maxLevel: 4" in index_content
    assert "subMaxLevel: 3" in index_content
    assert "search:" in index_content
    assert "copyCode:" in index_content
    assert "pagination:" in index_content
    assert "docsify@5.1.0" in index_content
    assert "docsify-copy-code@3.0.2" in index_content
    assert 'name="referrer" content="strict-origin-when-cross-origin"' in index_content
    assert 'crossorigin="anonymous"' in index_content


def test_renovate_custom_manager_matches_cdn_urls_and_setup_uv() -> None:
    renovate_path = _ROOT / ".github" / "renovate.json"
    assert renovate_path.is_file()
    renovate_text = renovate_path.read_text(encoding="utf-8")
    renovate_data = json.loads(renovate_text)

    # Ensure customManager uses standard (?<depName>...) and (?<currentValue>...) syntax (not python ?P<)
    assert "(?<depName>" in renovate_text
    assert "(?<currentValue>" in renovate_text
    assert "(?P<depName>" not in renovate_text
    assert "(?P<currentValue>" not in renovate_text

    custom_managers = renovate_data.get("customManagers", [])
    assert len(custom_managers) >= 2

    cdn_manager = None
    uv_manager = None
    for cm in custom_managers:
        if cm.get("customType") == "regex":
            patterns = cm.get("managerFilePatterns", [])
            for p in patterns:
                if "index.html" in p or "index" in p:
                    cdn_manager = cm
                elif "action.yml" in p or "yaml" in p or "setup-env" in p:
                    uv_manager = cm

    assert cdn_manager is not None, "Renovate custom manager for docs/index.html not found"
    assert uv_manager is not None, "Renovate custom manager for setup-uv not found"

    # Test uv action customManager regex against action.yml
    uv_match_strings = uv_manager.get("matchStrings", [])
    assert len(uv_match_strings) > 0
    py_uv_regex = re.compile(
        re.sub(r"\(\?<([a-zA-Z_][a-zA-Z0-9_]*)>", r"(?P<\1>", uv_match_strings[0])
    )

    action_path = _ROOT / ".github" / "actions" / "setup-env" / "action.yml"
    assert action_path.is_file()
    action_text = action_path.read_text(encoding="utf-8")
    m = py_uv_regex.search(action_text)
    assert m is not None, "setup-uv customManager regex did not match action.yml!"
    assert m.group("currentValue") == "0.13.7"

    match_strings = cdn_manager.get("matchStrings", [])
    assert len(match_strings) > 0

    index_html_path = _ROOT / "docs" / "index.html"
    index_content = index_html_path.read_text(encoding="utf-8")

    # Find all jsDelivr npm URLs in docs/index.html
    jsdelivr_urls = re.findall(r"https://cdn\.jsdelivr\.net/npm/[^\s\"']+", index_content)
    assert len(jsdelivr_urls) >= 10, f"Expected at least 10 jsDelivr URLs, got {len(jsdelivr_urls)}"

    # Convert JS named capture group (?<name>...) to Python (?P<name>...) for testing with re
    py_match_strings = [
        re.sub(r"\(\?<([a-zA-Z_][a-zA-Z0-9_]*)>", r"(?P<\1>", ms) for ms in match_strings
    ]
    regexes = [re.compile(ms) for ms in py_match_strings]

    for url in jsdelivr_urls:
        matched = False
        dep_name = None
        current_value = None
        for rx in regexes:
            m = rx.search(url)
            if m:
                matched = True
                dep_name = m.group("depName")
                current_value = m.group("currentValue")
                break
        assert matched, (
            f"CDN URL {url} did not match any customManager regex in .github/renovate.json!"
        )
        assert dep_name, f"Matched depName is empty for {url}"
        assert current_value, f"Matched currentValue is empty for {url}"
        # Ensure version looks like semver (e.g. 5.1.0, 3.0.2, etc.)
        assert re.match(r"^\d+\.\d+\.\d+", current_value), (
            f"Version {current_value} for {dep_name} does not look like semver"
        )


def test_workflow_has_pages_and_pr_comment() -> None:
    workflow_path = _ROOT / ".github" / "workflows" / "run-optimizer.yml"
    assert workflow_path.is_file()
    workflow_text = workflow_path.read_text(encoding="utf-8")

    ci_path = _ROOT / ".github" / "workflows" / "ci.yml"
    assert ci_path.is_file()
    ci_text = ci_path.read_text(encoding="utf-8")

    # Permissions
    assert "pull-requests: write" in workflow_text
    assert "pull-requests: write" in ci_text

    # Triggers and jobs structure
    assert "pull_request:" in workflow_text
    assert "push:" in workflow_text
    assert "uses: ./.github/workflows/ci.yml" in workflow_text
    assert "needs: test" in workflow_text
    assert "if: github.event_name != 'pull_request'" in workflow_text

    # PR comment in ci.yml
    assert "Render CI results comment" in ci_text
    assert "if: always()" in ci_text
    assert "scripts/render_optimizer_issue.py" in ci_text

    assert "pages: write" in workflow_text
    assert "id-token: write" in workflow_text
    assert "issues: write" in workflow_text
    assert "contents: read" in workflow_text

    assert "github-pages" in workflow_text
    assert "${{ steps.deployment.outputs.page_url }}" in workflow_text

    assert "Configure GitHub Pages" in workflow_text
    assert "Upload Pages artifact" in workflow_text
    assert "Deploy to GitHub Pages" in workflow_text

    assert "actions/configure-pages@v6" in workflow_text
    assert "actions/upload-pages-artifact@v5" in workflow_text
    assert "actions/deploy-pages@v5" in workflow_text

    # Check DRY staging commands and sed replacement
    assert "out/site" in workflow_text
    assert "sed -i" in workflow_text
    assert "{{REPO_URL}}" in workflow_text


def test_composite_action_and_hardening() -> None:
    action_path = _ROOT / ".github" / "actions" / "setup-env" / "action.yml"
    assert action_path.is_file()
    action_text = action_path.read_text(encoding="utf-8")
    assert "astral-sh/setup-uv" in action_text
    assert "uv sync --locked" in action_text
    assert "cache-python: true" in action_text
    assert "actions/cache@v6" in action_text

    ci_path = _ROOT / ".github" / "workflows" / "ci.yml"
    optimizer_path = _ROOT / ".github" / "workflows" / "run-optimizer.yml"
    publish_path = _ROOT / ".github" / "workflows" / "publish-results.yml"

    for path in (ci_path, optimizer_path, publish_path):
        assert path.is_file()
        text = path.read_text(encoding="utf-8")
        assert "./.github/actions/setup-env" in text

    # Verify least-privilege permissions in run-optimizer.yml
    optimizer_text = optimizer_path.read_text(encoding="utf-8")
    assert "pull-requests: write" in optimizer_text
    assert "issues: write" in optimizer_text

    # Verify continue-on-error in ci.yml
    ci_text = ci_path.read_text(encoding="utf-8")
    assert "continue-on-error: true" in ci_text
    assert "Fail if any CI check failed" in ci_text


def test_no_hardcoded_personal_or_repo_identifiers() -> None:
    dirs_to_check = ["docs", "scripts", ".github", "src", "tests"]
    files_to_check = ["pyproject.toml", "README.md"]

    forbidden_patterns = [
        re.compile(
            r"github\.com/(?!example/repo)[a-zA-Z0-9_-]+/opencode-go-model-optimizer",
            re.IGNORECASE,
        ),
        re.compile(
            r"raw\.githubusercontent\.com/(?!<owner>/|anomalyco/)[a-zA-Z0-9_-]+/", re.IGNORECASE
        ),
        re.compile(r"authors\s*=\s*\[\s*\{\s*name\s*=", re.IGNORECASE),
    ]

    all_paths = []
    for d in dirs_to_check:
        d_path = _ROOT / d
        if d_path.is_dir():
            all_paths.extend(
                [
                    f
                    for f in d_path.rglob("*")
                    if f.is_file() and f.suffix in (".py", ".yml", ".json", ".md", ".html", "")
                ]
            )
    for f_name in files_to_check:
        f_path = _ROOT / f_name
        if f_path.is_file():
            all_paths.append(f_path)

    for f in all_paths:
        content = f.read_text(encoding="utf-8", errors="ignore")
        for pat in forbidden_patterns:
            m = pat.search(content)
            assert m is None, (
                f"Found hardcoded personal/repo identifier matching pattern {pat.pattern} in {f}"
            )


def test_no_interpolation_in_run_blocks() -> None:
    ci_path = _ROOT / ".github" / "workflows" / "ci.yml"
    optimizer_path = _ROOT / ".github" / "workflows" / "run-optimizer.yml"
    publish_path = _ROOT / ".github" / "workflows" / "publish-results.yml"
    action_path = _ROOT / ".github" / "actions" / "setup-env" / "action.yml"

    for path in (ci_path, optimizer_path, publish_path, action_path):
        assert path.is_file()
        text = path.read_text(encoding="utf-8")
        in_run_block = False
        for line in text.splitlines():
            stripped = line.strip()
            if (
                stripped.startswith("env:")
                or stripped.startswith("with:")
                or stripped.startswith("if:")
            ):
                in_run_block = False
                continue
            if stripped.startswith("run:"):
                in_run_block = True
                continue
            if in_run_block:
                if stripped and not line.startswith(" ") and not line.startswith("\t"):
                    in_run_block = False
                else:
                    assert "${{" not in line, f"Found ${{ }} in run block of {path.name}: {line}"

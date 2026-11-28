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
    assert (docs_dir / "analysis.md").is_file()
    assert (docs_dir / "data.md").is_file()
    assert (docs_dir / "index.html").is_file()

    index_content = (docs_dir / "index.html").read_text(encoding="utf-8")
    sidebar_content = (docs_dir / "_sidebar.md").read_text(encoding="utf-8")
    data_content = (docs_dir / "data.md").read_text(encoding="utf-8")
    readme_content = (docs_dir / "README.md").read_text(encoding="utf-8")
    assert "window.$docsify" in index_content
    assert "name: 'OpenCode Go Optimizer'" in index_content
    assert "{{REPO_URL}}" in index_content
    assert "{{REPO_URL}}" in sidebar_content
    assert "{{ISSUES_URL}}" not in index_content + sidebar_content + data_content
    assert "GitHub Issue" not in readme_content
    assert "select.json" in sidebar_content
    assert "loadSidebar: true" in index_content
    assert "auto2top: true" in index_content
    assert "maxLevel: 4" in index_content
    assert "subMaxLevel: 3" in index_content
    assert "search:" in index_content
    assert "copyCode:" in index_content
    assert "pagination:" in index_content
    assert "docsify@5.1.0" in index_content
    assert "docsify-copy-code@3.0.2" in index_content
    assert "mermaid@" in index_content
    assert "window.mermaid.run" in index_content
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

    pr_ci_path = _ROOT / ".github" / "workflows" / "pr-ci.yml"
    assert pr_ci_path.is_file()
    pr_ci_text = pr_ci_path.read_text(encoding="utf-8")

    ci_path = _ROOT / ".github" / "workflows" / "ci.yml"
    assert ci_path.is_file()
    ci_text = ci_path.read_text(encoding="utf-8")

    # Permissions
    assert "pull-requests: write" in pr_ci_text
    assert "pull-requests: write" in ci_text

    # Triggers and jobs structure
    # A PR run must only contain jobs that can actually run on a PR: the PR
    # trigger lives in pr-ci.yml, and run-optimizer.yml must not carry
    # PR-triggered paths that would render as skipped jobs on every PR. The
    # optimizer-output job is PR-safe by design: it degrades to a
    # no-benchmarks run when repository secrets (1Password) are unavailable,
    # e.g. on fork PRs.
    assert "pull_request:" in pr_ci_text
    assert "push:" in workflow_text
    assert "schedule:" in workflow_text
    assert "pull_request:" not in workflow_text
    assert "if: github.event_name != 'pull_request'" not in workflow_text
    assert "uses: ./.github/workflows/ci.yml" in pr_ci_text
    assert "uses: ./.github/workflows/ci.yml" in workflow_text
    assert "needs: test" in workflow_text
    assert "live-tests:" in workflow_text
    live_block = workflow_text.split("live-tests:", 1)[1].split("optimize:", 1)[0]
    assert (
        "github.event_name == 'schedule' || github.event_name == 'workflow_dispatch'" in live_block
    )
    assert "continue-on-error: true" in live_block
    assert "uv run pytest tests/live -m live" in live_block
    # Reusable workflows fail at startup when the caller does not grant every
    # permission the called workflow declares. ci.yml declares this for its
    # PR-comment step, which is skipped for push/schedule runs.
    assert "pull-requests: write" in workflow_text

    # The optimizer runs BOTH methods without fallback on PRs and posts both outputs.
    assert "optimizer-output:" in pr_ci_text
    assert "needs: test" in pr_ci_text
    assert "Detect 1Password configuration" in pr_ci_text
    assert "Load AA API key from 1Password" in pr_ci_text
    assert "1password/load-secrets-action@v5" in pr_ci_text
    assert "Restore Artificial Analysis cache" in pr_ci_text
    assert "Save Artificial Analysis cache" in pr_ci_text
    assert "Restore Artificial Analysis cooldown state" in pr_ci_text
    assert "Save Artificial Analysis cooldown state" in pr_ci_text
    assert "aa-benchmarks-v2-" in pr_ci_text
    assert "aa-ratelimit-v1-" in pr_ci_text
    assert 'OPENCODE_GO_AA_CACHE_TTL: "86400"' in pr_ci_text
    assert "--allow-fallback" not in pr_ci_text
    assert "--format json --explain --allow-global-region --allow-free-training" in pr_ci_text
    assert "continue-on-error: true" in pr_ci_text
    assert "Render optimizer output comment" in pr_ci_text
    assert "scripts/render_optimizer_issue.py" in pr_ci_text
    assert "--pareto-json out/pareto.json" in pr_ci_text
    assert "--pareto-stderr out/pareto.err" in pr_ci_text
    assert "--allowance-json out/allowance.json" in pr_ci_text
    assert "--allowance-stderr out/allowance.err" in pr_ci_text
    assert "_combined.txt" not in pr_ci_text
    assert "optimizer-output-comment:commit=" in pr_ci_text
    assert "Post or update PR comment with optimizer output" in pr_ci_text
    assert "Fail if the optimizer run failed" in pr_ci_text

    # PR comment in ci.yml
    assert "Render CI results comment" in ci_text
    assert "if: always()" in ci_text
    assert "scripts/render_optimizer_issue.py" in ci_text

    assert "pages: read" in workflow_text
    # The explanatory comment names the permissions held by publish-pages.yml;
    # only active workflow configuration must omit the OIDC token grant here.
    workflow_without_comments = "\n".join(
        line for line in workflow_text.splitlines() if not line.lstrip().startswith("#")
    )
    assert "id-token: write" not in workflow_without_comments
    assert "issues: write" not in workflow_text
    assert "contents: read" in workflow_text

    assert "github-pages" not in workflow_text
    assert "steps.deployment" not in workflow_text

    assert "Configure GitHub Pages" in workflow_text
    assert "Upload Docsify site artifact" in workflow_text

    # Both methods run without fallback against one shared snapshot, and both are
    # staged and published before the final failure check.
    assert "id: pages" in workflow_text
    assert "SITE_URL: ${{ steps.pages.outputs.base_url }}" in workflow_text
    assert "--site-url" in workflow_text
    assert "--data-output out/data.md" in workflow_text
    assert "uv run opencode-go-model-optimizer fetch" in workflow_text
    assert "Restore Artificial Analysis cache" in workflow_text
    assert "Save Artificial Analysis cache" in workflow_text
    assert "Restore Artificial Analysis cooldown state" in workflow_text
    assert "Save Artificial Analysis cooldown state" in workflow_text
    assert "aa-benchmarks-v2-" in workflow_text
    assert "aa-ratelimit-v1-" in workflow_text
    assert "refresh_aa:" in workflow_text
    assert '"79200"' in workflow_text
    assert '"86400"' in workflow_text
    assert "--mode auto --allow-fallback" in workflow_text
    assert "out/site/select.json" in workflow_text
    method_block = workflow_text.split("run_method() {", 1)[1].split("run_method pareto", 1)[0]
    assert "--allow-fallback" not in method_block
    assert "out/snapshot.json" in workflow_text
    assert "out/site/snapshot.json" in workflow_text
    assert "cp -a out/pareto out/site/pareto" in workflow_text
    assert "cp -a out/allowance out/site/allowance" in workflow_text
    assert "cp out/free-matrix.json out/site/free-matrix.json" in workflow_text
    assert "out/site/full-output.txt" in workflow_text
    assert "out/site/fetch-log.txt" in workflow_text
    assert "out/site/data.md" in workflow_text
    assert workflow_text.index("Configure GitHub Pages") < workflow_text.index(
        "Run optimizer (Pareto & Allowance, no fallback)"
    )
    assert "Create or update GitHub issue" not in workflow_text
    assert 'cat out/issue.md >> "$GITHUB_STEP_SUMMARY"' in workflow_text
    assert "cp out/issue.md out/site/README.md" in workflow_text
    assert workflow_text.index("Upload Docsify site artifact") < workflow_text.index(
        "Fail if an optimizer calculation failed"
    )

    assert "actions/configure-pages@v6" in workflow_text

    pages_path = _ROOT / ".github" / "workflows" / "publish-pages.yml"
    assert pages_path.is_file()
    pages_text = pages_path.read_text(encoding="utf-8")
    assert 'workflows: ["Run Optimizer"]' in pages_text
    assert "types: [completed]" in pages_text
    assert "workflow_dispatch:" in pages_text
    assert "github.event.workflow_run.conclusion == 'success'" in pages_text
    assert "github.event.workflow_run.conclusion == 'failure'" in pages_text
    assert "has_artifact" in pages_text
    assert "pages: write" in pages_text
    assert "id-token: write" in pages_text
    assert "name: github-pages" in pages_text
    assert "${{ steps.deployment.outputs.page_url }}" in pages_text
    assert "name: docs-site" in pages_text
    assert "actions/download-artifact@v8" in pages_text
    assert "actions/upload-pages-artifact@v5" in pages_text
    assert "actions/deploy-pages@v5" in pages_text
    assert "Deploy to GitHub Pages" in pages_text

    # Check generation of analysis.md and dual-method rank/plan JSONs
    assert "out/site/analysis.md" in workflow_text
    assert "out/pareto/rank.json" in workflow_text
    assert "out/allowance/rank.json" in workflow_text
    assert "--pareto-rank-json out/pareto/rank.json" in workflow_text
    assert "--allowance-rank-json out/allowance/rank.json" in workflow_text
    assert "--pareto-plan-json" in workflow_text
    assert "--allowance-plan-json" in workflow_text
    assert "--pareto-error out/pareto/error.txt" in workflow_text
    assert "--free-matrix-json" in workflow_text
    assert "--charts-dir out/charts" in workflow_text
    assert "out/site/charts" in workflow_text

    # Check DRY staging commands and repository-only placeholder replacement.
    assert "out/site" in workflow_text
    assert "sed -i" in workflow_text
    assert "{{REPO_URL}}" in workflow_text
    assert "{{ISSUES_URL}}" not in workflow_text


def test_docsify_analysis_page_and_sidebar() -> None:
    docs_dir = _ROOT / "docs"
    analysis_md = docs_dir / "analysis.md"
    assert analysis_md.is_file()
    text = analysis_md.read_text(encoding="utf-8")
    assert "Live Pareto Analysis & Catalog" in text

    sidebar_md = docs_dir / "_sidebar.md"
    assert sidebar_md.is_file()
    sidebar_text = sidebar_md.read_text(encoding="utf-8")
    assert "analysis.md" in sidebar_text
    assert "data.md" in sidebar_text
    assert "select.json" in sidebar_text
    assert "pareto/select.json" in sidebar_text
    assert "pareto/rank.json" in sidebar_text
    assert "pareto/plans/go.json" in sidebar_text
    assert "allowance/select.json" in sidebar_text
    assert "allowance/rank.json" in sidebar_text
    assert "allowance/plans/go.json" in sidebar_text
    assert "snapshot.json" in sidebar_text
    assert "free-matrix.json" in sidebar_text


def test_readme_clean_hardcoded_examples() -> None:
    readme_text = (_ROOT / "README.md").read_text(encoding="utf-8")
    assert "2026-11-16" not in readme_text
    assert "Gemini 4 Argon" not in readme_text
    assert "Worked example (live" not in readme_text
    assert "analysis.md" not in readme_text
    assert "```mermaid" in readme_text
    assert "flowchart LR" in readme_text
    assert "flowchart TD" in readme_text
    assert "xychart-beta" in readme_text


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
    pr_ci_path = _ROOT / ".github" / "workflows" / "pr-ci.yml"

    for path in (ci_path, optimizer_path, publish_path, pr_ci_path):
        assert path.is_file()
        text = path.read_text(encoding="utf-8")
        assert "./.github/actions/setup-env" in text

    # Verify permissions in run-optimizer.yml. Its optimize job never runs on a PR,
    # but its test job calls ci.yml, which declares pull-requests: write for the
    # PR-comment step; reusable workflows fail at startup without that grant.
    optimizer_text = optimizer_path.read_text(encoding="utf-8")
    assert "pull-requests: write" in optimizer_text
    assert "issues: write" not in optimizer_text

    pr_ci_text = (_ROOT / ".github" / "workflows" / "pr-ci.yml").read_text(encoding="utf-8")
    assert "pull-requests: write" in pr_ci_text

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


def test_no_interpolation_in_run_blocks_and_valid_multiline_indent() -> None:
    ci_path = _ROOT / ".github" / "workflows" / "ci.yml"
    optimizer_path = _ROOT / ".github" / "workflows" / "run-optimizer.yml"
    publish_path = _ROOT / ".github" / "workflows" / "publish-results.yml"
    pages_path = _ROOT / ".github" / "workflows" / "publish-pages.yml"
    action_path = _ROOT / ".github" / "actions" / "setup-env" / "action.yml"
    pr_ci_path = _ROOT / ".github" / "workflows" / "pr-ci.yml"

    for path in (ci_path, optimizer_path, publish_path, pages_path, action_path, pr_ci_path):
        assert path.is_file()
        text = path.read_text(encoding="utf-8")
        run_indent: int | None = None
        for lineno, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            indent = len(line) - len(line.lstrip(" "))
            if run_indent is not None:
                if not stripped:
                    continue
                # Any non-empty line in a YAML block scalar must stay indented past `run: |`,
                # unless the YAML block scalar has ended at a sibling/parent key.
                if indent <= run_indent:
                    assert (
                        stripped.startswith("- ")
                        or stripped.startswith("#")
                        or stripped.endswith(":")
                        or re.match(r"^[A-Za-z0-9_-]+:\s*", stripped) is not None
                    ), f"Unindented line inside `run: |` block of {path.name}:{lineno}: {line!r}"
                    run_indent = None
                else:
                    assert "${{" not in line, (
                        f"Found ${{ }} in run block of {path.name}:{lineno}: {line}"
                    )
                    continue
            if stripped.startswith("run: |"):
                run_indent = indent
            elif stripped.startswith("run:"):
                assert "${{" not in line, (
                    f"Found ${{ }} in run line of {path.name}:{lineno}: {line}"
                )

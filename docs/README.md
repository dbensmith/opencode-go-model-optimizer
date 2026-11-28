# OpenCode Go Model Optimizer — Latest Tier Selections

This file is populated in CI by `scripts/render_optimizer_issue.py` from paired Pareto and Allowance JSON outputs (`--pareto-json` / `--allowance-json`) during the scheduled optimizer workflow run.

```mermaid
flowchart LR
    CI["Scheduled CI (run-optimizer.yml)"] --> FETCH["Ingest Catalog & AA Benchmarks"]
    FETCH --> OPT["Evaluate Pareto & Allowance Methods"]
    OPT --> RENDER["scripts/render_optimizer_issue.py"]
    RENDER --> TIERS["docs/README.md (Latest Tier Selections)"]
    RENDER --> CHARTS["docs/analysis.md (Live Pareto & Catalog)"]
    RENDER --> ARTIFACT["Upload docs-site Artifact"]
    ARTIFACT --> DOWNLOAD["publish-pages.yml: Download docs-site Artifact"]
    DOWNLOAD --> PAGES["Deploy to Docsify GitHub Pages"]
```

# OpenCode Go Model Optimizer — All Data & Other Choices

This file is populated in CI by `scripts/render_optimizer_issue.py` from paired Pareto and Allowance JSON outputs (`--pareto-json` / `--allowance-json`) during each optimizer run.

```mermaid
flowchart LR
    FETCH["Fetch Snapshot (catalogue, docs, models.dev, AA)"] --> DATA["data.md (All Data & Other Choices)"]
    DATA --> RAW["pareto/select.json / allowance/select.json / snapshot.json"]
    DATA --> RANK["pareto/rank.json / allowance/rank.json"]
    DATA --> PLANS["pareto/plans/*.json / allowance/plans/*.json"]
    DATA --> FREE["free-matrix.json"]
    DATA --> OUTPUT["full-output.txt / fetch-log.txt"]
    DATA --> CHARTS["charts/*.svg"]
    RAW --> PAGES["Deploy to Docsify GitHub Pages"]
    RANK --> PAGES
    PLANS --> PAGES
    FREE --> PAGES
    OUTPUT --> PAGES
    CHARTS --> PAGES
```

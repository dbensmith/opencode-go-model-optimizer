# OpenCode Go Model Optimizer

## What This Is

`opencode-go-model-optimizer` is a decoupled CLI engine that evaluates the active OpenCode Go catalogue—pricing, monthly allowances, privacy constraints, and optional Artificial Analysis benchmarks—and assigns models to the `heavy`, `balanced`, `light`, and `free` tiers without being bound to any specific consumer framework. It provides consumer-agnostic, machine-readable tier selections through a stable JSON contract that downstream tools, such as OpenCode or Paseo profile tooling, can consume. It also publishes the findings transparently through Docsify GitHub Pages with analysis and data pages, SVG/Mermaid charts, and raw JSON datasets; the GitHub issue and step summary; per-method PR comments; and the scheduled machine-readable report on the orphan `results` branch.

## Core Value

Accurate tier selections exposed as a stable, consumer-agnostic machine-readable contract and published openly so humans can audit why each model was chosen.

## Requirements

### Validated

- ✓ Evaluate OpenCode Go catalogue pricing and monthly allowances into `heavy`, `balanced`, `light`, and `free` tier selections — existing
- ✓ Provide Pareto selection using Artificial Analysis benchmarks and an independent allowance-based selection mode — existing
- ✓ Require benchmarks for Pareto and permit fallback to allowance only with `--allow-fallback` — existing
- ✓ Provide `select`, `rank`, `diff`, `fetch`, and `report` CLI commands — existing
- ✓ Emit machine-readable JSON from `select`, `rank`, and `diff`, plus shell and GitHub Actions output formats from `select` — existing
- ✓ Normalize timestamped snapshots with local snapshot caching, stale-cache recovery, provenance, and Artificial Analysis benchmark caching — existing
- ✓ Apply privacy, region, training, zero-data-retention, and retention-day filters — existing
- ✓ Account for time-aware price multipliers, workload profiles, token mixes, and quota-burn estimates — existing
- ✓ Run paired Pareto and Allowance methods in CI — existing
- ✓ Render optimizer findings, selection explanations, run settings, and Mermaid diagrams for the GitHub issue, step summary, and Docsify analysis/data pages — existing
- ✓ Generate SVG Pareto-frontier, catalog-ranking, and multi-plan comparison charts for the Docsify analysis/data pages — existing
- ✓ Deploy Docsify pages with raw JSON datasets (`select.json`, `rank.json`, `plans/*.json`, the normalized snapshot, and supporting artifacts) to GitHub Pages — existing
- ✓ Post per-method optimizer results and a separate CI status comment to pull requests — existing
- ✓ Publish a twice-daily machine-readable `results.json` report and human-readable README to a single-commit orphan `results` branch — existing

### Active

(None — define next milestone)

### Out of Scope

- Consumer-specific integrations (e.g. generating OpenCode or Paseo profile files) — the engine stays consumer-agnostic; consumers build on the JSON contract.
- Silent fallback from Pareto to allowance without `--allow-fallback` — PR #65 made fallback explicit so unavailable or failed benchmarks cannot produce silently mislabeled selections.
- Legacy single-method issue/page rendering and backward-compatibility re-export facades — PRs #67 and #70 removed them; CI consumes the paired Pareto/Allowance flow.

## Context

The repository is an existing Python package that ingests the OpenCode API and docs, models.dev metadata, and optional Artificial Analysis benchmarks into a normalized snapshot. Selection applies eligibility and privacy filters before running Pareto or allowance ranking and rendering CLI output. The same run data feeds transparent publishing through the optimizer issue and step summary, per-method and CI-status PR comments, Docsify analysis/data pages with SVG/Mermaid charts and raw JSON datasets, and the scheduled orphan `results` branch report. Offline tests use synthetic fixtures; live contract and browser verification remain opt-in.

## Constraints

- **Compatibility — JSON output is a public contract**: The CLI `--format json` output of `select`, `rank`, and `diff` exists so consumers can parse it easily (downstream consumers, e.g. profile tooling, depend on it). Additive fields are fine; renaming or removing keys requires explicit owner sign-off plus migration notes. Internal script inputs (e.g. `scripts/render_optimizer_issue.py` flags) are not part of the contract. Published artifacts are consumer-facing too: Pages raw JSON (including `rank.json`, `select.json`, `plans/*.json`, and `snapshot.json`) and `results.json` on the `results` branch follow the same additive-only rule, with owner sign-off and migration notes required for removed or renamed keys.
- **Tech stack**: Python `>=3.15` (`requires-python`), `uv`, `ruff`, and `pytest` — declared in `pyproject.toml` and used by the documented development workflow.
- **Privacy defaults**: Global-region models and training endpoints are excluded unless explicitly allowed; ZDR and retention filters fail closed when requested — selection must not silently weaken privacy criteria.

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| JSON output treated as a stable consumer contract | consumers parse it to build profiles | ✓ Good (verified 2026-11-28: a before/after schema diff across PRs #59–#70 showed only additive keys) |
| Keep the engine consumer-agnostic | consumer integrations live in consumers, which parse the JSON contract | ✓ Good |
| Pareto requires benchmarks; fallback only with `--allow-fallback` (PR #65) | avoid silently mislabelled selections | — Pending |
| Removed single-method render paths and legacy snapshot compatibility (PRs #67, #70) | dual-method flow is the only one CI uses | ✓ Good |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd-transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd-complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-11-28 after DRY refactor rounds (PRs #59–#70)*

# OpenCode Go Model Optimizer

## What This Is

`opencode-go-model-optimizer` is a CLI that evaluates the active OpenCode Go catalogue—pricing, monthly allowances, privacy constraints, and optional Artificial Analysis benchmarks—and assigns models to the `heavy`, `balanced`, `light`, and `free` tiers. Its purpose is to populate OpenCode and Paseo agent profiles per tier from accurate, machine-readable selections.

## Core Value

Accurate, machine-readable tier selections that downstream tooling can consume to configure OpenCode and Paseo profiles.

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
- ✓ Run paired Pareto and Allowance methods in CI and publish optimizer results to GitHub issues and pull-request comments — existing
- ✓ Render and deploy Docsify pages and publish scheduled machine-readable reports to the `results` branch — existing

### Active

- [ ] Populate OpenCode and Paseo profiles directly from tier output (e.g. a select output format or consumer script that emits profile entries per tier)

### Out of Scope

- Silent fallback from Pareto to allowance without `--allow-fallback` — PR #65 made fallback explicit so unavailable or failed benchmarks cannot produce silently mislabeled selections.
- Legacy single-method issue/page rendering and backward-compatibility re-export facades — PRs #67 and #70 removed them; CI consumes the paired Pareto/Allowance flow.

## Context

The repository is an existing Python package that ingests the OpenCode API and docs, models.dev metadata, and optional Artificial Analysis benchmarks into a normalized snapshot. Selection applies eligibility and privacy filters before running Pareto or allowance ranking, then renders CLI, CI, report, and Docsify outputs. Offline tests use synthetic fixtures; live contract and browser verification remain opt-in.

## Constraints

- **Compatibility — JSON output is a public contract**: The CLI `--format json` output of `select`, `rank`, and `diff` exists so consumers can parse it easily (it feeds OpenCode/Paseo profile population). Additive fields are fine; renaming or removing keys requires explicit owner sign-off plus migration notes. Internal script inputs (e.g. `scripts/render_optimizer_issue.py` flags) are not part of the contract.
- **Tech stack**: Python `>=3.15` (`requires-python`), `uv`, `ruff`, and `pytest` — declared in `pyproject.toml` and used by the documented development workflow.
- **Data policy**: No catalogue, docs, pricing, or benchmark snapshots are stored in the repository; caches are consumer-maintained and Artificial Analysis data is never republished — required by the README data policy and Artificial Analysis terms.
- **Privacy defaults**: Global-region models and training endpoints are excluded unless explicitly allowed; ZDR and retention filters fail closed when requested — selection must not silently weaken privacy criteria.

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| JSON output treated as a stable consumer contract | consumers parse it to build profiles | ✓ Good (verified 2026-11-28: a before/after schema diff across PRs #59–#70 showed only additive keys) |
| Pareto requires benchmarks; fallback only with `--allow-fallback` (PR #65) | avoid silently mislabelled selections | — Pending |
| Removed single-method render paths and legacy snapshot compatibility (PRs #67, #70) | dual-method flow is the only one CI uses | ✓ Good |

## Evolution

PROJECT.md evolves throughout the project lifecycle.
These rules are embedded in the generated PROJECT.md (## Evolution section)
and implemented by workflows/transition.md and workflows/complete-milestone.md.

**After each phase transition:**

1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone:**

1. Full review of all sections
2. Core Value check — still the right priority?
3. Business Context check (if present) — customer, revenue model, success metric still accurate?
4. Audit Out of Scope — reasons still valid?
5. Update Context with current state (users, feedback, metrics)

---
*Last updated: 2026-11-28 after DRY refactor rounds (PRs #59–#70)*

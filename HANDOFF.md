# Agent Handoff: OpenCode Go Model Optimizer (`opencode-go-model-optimizer`)

**Date:** 2026-11-03  
**Project:** `opencode-go-model-optimizer`  
**Paseo Project ID:** `prj_189905bfab125795`  
**Working Directory:** `/home/code/repos/opencode-go-model-optimizer`  
**Remote Git:** `https://github.com/dbensmith/opencode-go-model-optimizer.git` (branch `main`)  
**Package / Binary:** `opencode-go-model-optimizer` (designed for PyPI, `pipx`, and `uvx`)  

---

## 1. Context & Origin

This project extracts and unifies two disparate OpenCode Go model selection implementations:

1. **`ansible-control-node`**:
   - *References:* `docs/plans/20261025-opencode-go-ranker.md`, `docs/evidence/opencode-go-ranker-20261025/original-Opencode_Go_Ranker.py`, and Issue `#371`.
   - *Contribution:* Mathematical Pareto frontier modeling (Quality vs. Quota Burn), Fisher-Jenks natural clustering, and a decoupled 2-stage `fetch` &rarr; `rank` architecture.
2. **`dotfiles`**:
   - *References:* `dot_local/bin/executable_gsd-model-refresh`, `dot_gsd/opencode-go-model.json`.
   - *Contribution:* Operational heuristic selection (monthly allowance ceiling &rarr; output token price &rarr; release recency), preview/experimental filtering, and offline test fixtures.

---

## 2. Agreed Architectural Constraints & Design

1. **Pure Engine & CLI (Consumer Decoupled):**
   - The tool is strictly an optimizer and selector. It **must not** contain local process lifecycle logic (no `/proc` PID scans, no `paseo daemon restart`, no `chezmoi apply`, no flock locking). Consumer environments (Chezmoi, Ansible, CI) invoke this tool and handle their own lifecycle actions.
2. **Industry-Standard Model Tiers:**
   - Do **not** hardcode Claude-specific tier names (Opus, Sonnet, Haiku).
   - Use industry-standard capability tiers:
     - **`flagship`**: Top-frontier reasoning, architecture, and complex coding.
     - **`balanced`**: High-capability, cost-effective daily driver.
     - **`efficient`**: Low-latency, high-throughput, lightweight tasks.
     - **`free`**: Active zero-cost models (promos, contributor tier, unmetered).
3. **Dual-Mode Engine:**
   - **`--mode allowance` (Heuristic, zero external keys):** Ranks paid models by published monthly allowance &rarr; cheapest output &rarr; recency &rarr; context window. Picks free models by recency.
   - **`--mode pareto` (Benchmark-driven):** Requires `AA_API_KEY`. Evaluates Artificial Analysis benchmarks against normalized turn burn (750 in / 60k cached / 250 out) using Bidirectional Pareto Efficiency (BPE) and Fisher-Jenks clustering.
4. **Output Formats:**
   - `--format json` (generic structured JSON schema with `tiers`, `evaluated_at`, `mode`, and metadata).
   - `--format table` (human-readable CLI table).
   - `--format compact` (space/newline-delimited for shell scripting).

---

## 3. Current Workspace State

- **Language / Runtime:** Python `>=3.10`, managed strictly with `uv` (`uv sync`, `uv run`).
- **Dependencies:** `httpx`, `pydantic` (dev: `pytest`, `ruff`).
- **Paseo Integration:** `paseo.json` configured with scripts `test`, `lint`, and `format`.
- **Git State:** Clean on `main`, initial signed commits in place:
  - `src/opencode_go_model_optimizer/cli.py` (CLI entrypoint stub)
  - `tests/test_cli.py` (passing unit test suite)
  - `README.md`, `LICENSE` (MIT), `pyproject.toml`, `paseo.json`

---

## 4. Work Plan for This Session

### Step 1: Ingestion Modules (`src/opencode_go_model_optimizer/ingest/`)
- **Catalogue Ingestor:**
  - Parse `opencode models --verbose opencode-go` CLI output (or direct HTTP `/inference/go/v1/models`).
  - Extract ID, name, status (`active`/`deprecated`), costs, limits, context length, release date.
- **Go Docs MDX Parser:**
  - Fetch/parse OpenCode Go docs from raw GitHub: `https://raw.githubusercontent.com/anomalyco/opencode/dev/services/www/src/docs/content/console/go.mdx`.
  - Extract `<PlanTabs id="go-pricing">` base `<div slot="go">` markdown table (model names, prices, monthly limits like `$15`, `$30`, `$60`, `$80`, `Unlimited`).
- **Snapshot Generator:**
  - Support saving an immutable normalized `snapshot.json` (`fetch` command).

### Step 2: Selection & Ranking Engines (`src/opencode_go_model_optimizer/engine/`)
- **Allowance Engine:** Implement deterministic sorting matching the proven dotfiles heuristic.
- **Pareto Engine:** Implement BPE and Fisher-Jenks natural breaks over Intelligence Index vs. Quota Burn.
- **Tier Allocator:** Map ranked models into `flagship`, `balanced`, `efficient`, `free`.

### Step 3: Tests & Offline Fixtures (`tests/`)
- Add offline fixtures for catalogue and MDX docs (do not hit external networks during `pytest`).
- Assert deterministic tie-breaking and tier assignments.
- Ensure 100% pass on `uv run pytest` and clean `uv run ruff check`.

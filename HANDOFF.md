# OpenCode Go Model Optimizer (`opencode-go-model-optimizer`)

Dynamic model tiering, Pareto efficiency ranker, and capability tier selector for OpenCode Go subscriptions.

---

## 1. Context & Architecture

`opencode-go-model-optimizer` evaluates the active OpenCode Go catalog and computes optimal model assignments across standard capability tiers based on pricing regimes, monthly allowances, and benchmark intelligence metrics.

Key capabilities:
1. **Mathematical Pareto Frontier Modeling:** Evaluates Artificial Analysis benchmarks (Intelligence & Coding Indices) against normalized monthly turn burn (750 in / 60k cached / 250 out) using Bidirectional Pareto Efficiency (BPE) and Fisher-Jenks natural clustering.
2. **Deterministic Heuristic Selection:** Ranks paid models by published monthly allowance ceiling, output token pricing, context length, and release recency without requiring external API keys.

---

## 2. Architectural Design & Standards

1. **Pure Engine & CLI (Decoupled & Consumer-Agnostic):**
   - The tool is strictly a model optimizer and selector. It is decoupled from any specific consumer framework, agent runner, or local process lifecycle manager. Consumer environments (CLI tools, automation workflows, CI pipelines) invoke this tool and consume its standard structured outputs.
2. **Standard Capability Tiers:**
   - Rather than proprietary or vendor-specific labels, models are classified into standard capability tiers:
     - **`heavy`**: Top reasoning, architecture, and complex coding tasks.
     - **`balanced`**: High-capability, cost-effective daily driver.
     - **`light`**: Low-latency, high-throughput, lightweight tasks.
     - **`free`**: Active zero-cost models (promos, contributor tiers, unmetered).
3. **Dual Operating Modes:**
   - **`--mode allowance` (Heuristic, zero external keys):** Ranks paid models by published monthly allowance &rarr; cheapest output &rarr; recency &rarr; context window. Picks free models by recency.
   - **`--mode pareto` (Benchmark-driven):** Requires `AA_API_KEY` or uses reference benchmarks. Evaluates Artificial Analysis benchmarks against normalized turn burn using Bidirectional Pareto Efficiency (BPE) and Fisher-Jenks clustering.
4. **Standard Output Formats:**
   - `--format json`: Structured JSON schema with `tiers`, `evaluated_at`, `mode`, and metadata.
   - `--format table`: Human-readable formatted ASCII table.
   - `--format compact`: Space-delimited key-value lines for scripting.

---

## 3. Workspace & Tooling

- **Language / Runtime:** Python `>=3.10`, managed with `uv`.
- **Dependencies:** `httpx`, `pydantic` (dev: `pytest`, `pytest-cov`, `ruff`).
- **Commands:**
  - `uv run pytest --cov=opencode_go_model_optimizer`
  - `uv run ruff check`
  - `uv run ruff format --check`

# OpenCode Go Model Optimizer (`opencode-go-model-optimizer`)

Dynamic model tiering and Pareto efficiency optimizer for OpenCode Go ($10/mo subscription).

Evaluates the active OpenCode Go model catalog and computes optimal model assignments across industry-standard capability tiers based on live pricing regimes, monthly allowances, and benchmark intelligence metrics.

---

## Overview & Architecture

`opencode-go-model-optimizer` provides a clean, decoupled engine that evaluates the OpenCode Go catalog without being bound to any specific consumer framework or agent runner.

```text
┌────────────────────────────────────────────────────────────────────────┐
│                      opencode-go-model-optimizer                       │
│                                                                        │
│  ┌──────────────────────┐               ┌───────────────────────────┐  │
│  │    Data Ingestors    │               │      Ranking Engine       │  │
│  │ - OpenCode API / CLI │               │ - Dual-Mode Selector:     │  │
│  │ - OpenCode MDX Docs  ├──────────────►│   • Pareto + AA Benchmark │  │
│  │ - models.dev         │               │   • Allowance Heuristic   │  │
│  │ - ArtificialAnalysis │               │ - Natural Fisher-Jenks    │  │
│  └──────────────────────┘               └─────────────┬─────────────┘  │
│                                                       │                │
│                                                       ▼                │
│                                         ┌───────────────────────────┐  │
│                                         │      Standard Outputs     │  │
│                                         │ - Structured JSON Schema  │  │
│                                         │ - Markdown / ASCII Table  │  │
│                                         │ - Raw Ingested Snapshot   │  │
│                                         └───────────────────────────┘  │
└────────────────────────────────────────────────────────────────────────┘
```

### Industry-Standard Model Tiers

Rather than proprietary or vendor-specific labels, models are classified into standard capability tiers:

- **`heavy`**: High-compute, complex tasks and deep reasoning.
- **`balanced`**: High-capability, cost-effective daily workhorse.
- **`light`**: High-throughput, low-latency, lightweight models.
- **`free`**: Active zero-cost models (promos, contributor tiers, or unmetered).

Consumers (such as agent orchestrators, developer tools, CI workflows, or configuration managers) map these standard tiers to their own internal configs.

### Operating Modes

- **Pareto Mode (`--mode pareto`):** Evaluates Artificial Analysis benchmarks (Intelligence & Coding Indices) against monthly quota burn using Bidirectional Pareto Efficiency (BPE) and Fisher-Jenks natural breaks.
- **Allowance Mode (`--mode allowance`):** Zero-API-key heuristic ranking models by published monthly plan allowance, output token costs, and release recency.

---

## Installation & Usage

### Via `pipx`
```bash
pipx install opencode-go-model-optimizer
opencode-go-model-optimizer --help
```

### Via `uvx` (Ephemeral run)
```bash
uvx opencode-go-model-optimizer select --format json
```

### Commands

- **`select`**: Selects and outputs optimal models for each capability tier:
  ```bash
  opencode-go-model-optimizer select --format json
  ```
  Example JSON output:
  ```json
  {
    "tiers": {
      "heavy": "opencode-go/qwen3.8-max",
      "balanced": "opencode-go/deepseek-v4.1-flash",
      "light": "opencode-go/glm-5.3-flash",
      "free": "opencode-go/iceman-contributor-free"
    },
    "evaluated_at": "2026-11-03T23:50:00Z",
    "mode": "allowance"
  }
  ```

- **`rank`**: Produces a full comparison table across all models in the catalog:
  ```bash
  opencode-go-model-optimizer rank --table
  ```

- **`fetch`**: Captures a normalized, timestamped `snapshot.json` from upstream sources:
  ```bash
  opencode-go-model-optimizer fetch --out snapshot.json
  ```

### CLI Flags & Options

The `select` and `rank` subcommands support filtering, optimization, and export flags:

- **`--profile {general,coding,reasoning,fast}`**: Workload profile adjusting token mix and benchmark weights (e.g. `coding` weights AA Coding Index higher, `reasoning` weights Intelligence Index higher, `fast` rewards high TPS).
- **`--skip <model_id>`**: Exclude specific model IDs from selection or ranking (can be specified multiple times).
- **`--prefer <tier>=<model_id>`**: Pin a model to a tier (e.g. `--prefer heavy=kimi-k3.1-pro`).
- **`--min-context <tokens>`**: Enforce a minimum context window threshold (default: 0).
- **`--min-output <tokens>`**: Enforce a minimum output token limit threshold (default: 0).
- **`--max-output-cost <usd>`**: Enforce a maximum output token cost cap in USD per 1M tokens (e.g. `--max-output-cost 15.0`).
- **`--top-n <count>`**: Number of ordered candidate models per tier for fallback chains (default: 1). When `top_n > 1`, includes `"fallbacks"` in JSON, `OPENCODE_MODEL_<TIER>_FALLBACKS` in env, and `<tier>_fallbacks` in github-output.
- **`--no-contributor`**: Exclude contributor-tier models from free tier allocation.
- **`--cache-ttl <seconds>`**: Cache snapshot fetches locally for TTL seconds (or via `OPENCODE_GO_CACHE_TTL`).
- **`--explain`**: Include detailed per-tier metrics (`turn_cost_usd`, `quota_burn_pct`, `bpe`, `aa_iq`, etc.) in table and JSON output.
- **`--format {json,table,compact,env,github-output}`**: Output format:
  - `json`: Structured JSON schema.
  - `table`: Formatted ASCII table (with extra metric columns when `--explain` is enabled).
  - `compact`: Clean space-delimited `<tier> <model_id>` lines.
  - `env`: Shell environment variable assignments (`OPENCODE_MODEL_HEAVY="..."`, etc.).
  - `github-output`: GitHub Actions `$GITHUB_OUTPUT` key=value lines (`heavy=...`, etc.).

---

## Development

```bash
# Clone repository
git clone https://github.com/dbensmith/opencode-go-model-optimizer.git
cd opencode-go-model-optimizer

# Sync dependencies and run tests
uv sync
uv run pytest
uv run ruff check
```

---

## License

MIT License. See [LICENSE](LICENSE) for details.

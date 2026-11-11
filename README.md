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

Requires Python 3.14+.

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
  Example output against the bundled reference snapshot:
  ```bash
  opencode-go-model-optimizer select --snapshot src/opencode_go_model_optimizer/data/reference_snapshot.json --format json
  ```
  ```json
  {
    "plan": "go",
    "tiers": {
      "heavy": "opencode-go/gemini-4-argon",
      "balanced": "opencode-go/deepseek-v4.1-flash",
      "light": "opencode-go/mimo-v2.6-flash",
      "free": "opencode-go/longcat-2.5-free"
    },
    "evaluated_at": "2026-11-11T02:47:40+00:00",
    "mode": "pareto",
    "snapshot": {
      "source": "file",
      "fetched_at": "2026-11-10T21:45:55+00:00"
    }
  }
  ```
  The `snapshot` block (JSON only) records provenance: `source` is `file` (`--snapshot`),
  `cache`, `live`, or `reference` (bundled fallback after a live fetch failure).
  `--format table` (the default for `rank`) and `--format json` are also available.

- **`rank`**: Produces a ranked comparison of every model in the catalog. Table output is the default:
  ```bash
  opencode-go-model-optimizer rank --format table
  opencode-go-model-optimizer rank --format json
  ```
  Excerpt from `rank --snapshot src/opencode_go_model_optimizer/data/reference_snapshot.json`:
  ```text
  +------+----------------------------+----------+-------+------+--------+-----------+-------------+------------+
  | Rank | Model ID                   | Tier     | BPE   | IQ   | Burn % | Allowance | Output Cost | Release    |
  +------+----------------------------+----------+-------+------+--------+-----------+-------------+------------+
  | 1    | kimi-k3.1-pro              | -        | 1.000 | 55.0 | 106.7% | $15.00    | $10.00/1M   | 2026-10-23 |
  | 2    | gemini-4-argon             | heavy    | 1.000 | 53.0 | 33.3%  | $30.00    | $10.00/1M   | 2026-10-13 |
  | 3    | mimo-v2.6-pro              | -        | 1.000 | 46.0 | 5.1%   | $15.00    | $0.87/1M    | 2026-09-22 |
  ```
  The legacy `rank --table` flag is still accepted, but `--format table` is the default.

- **`diff`**: Compares catalog, pricing, status, capability/benchmark (context, max output, AA IQ/coding, speed), and tier assignments between two snapshots:
  ```bash
  opencode-go-model-optimizer diff --old previous.json --new snapshot.json
  ```
  Defaults to the bundled reference snapshot for `--old` and a live (or cached) fetch for
  `--new`. Use `--format json` for machine-readable drift reports and `--fail-on-change`
  (alias `--exit-code`) to exit `2` when anything changed, which is ideal for scheduled CI jobs.

- **`fetch`**: Captures a normalized, timestamped `snapshot.json` from upstream sources:
  ```bash
  opencode-go-model-optimizer fetch --out snapshot.json
  ```

### Offline Usage

- `--snapshot <file>` reads a local snapshot JSON and makes no network calls, so it works fully offline. Use it with `fetch` output or the bundled reference snapshot.
- When a live fetch fails, the tool falls back to the bundled reference snapshot and prints a warning to stderr. The JSON `snapshot.source` field reports `reference` in that case.
- `--cache-ttl <seconds>` (or `OPENCODE_GO_CACHE_TTL`) caches live fetches locally for that many seconds. The default is `0` (no caching).

### Shell / CI Usage

Load the tier assignments into the current shell:

```bash
eval "$(opencode-go-model-optimizer select --format env)"
echo "$OPENCODE_MODEL_HEAVY"
```

In GitHub Actions, append the outputs to `$GITHUB_OUTPUT` and reference them by step id:

```yaml
- name: Select models
  id: models
  run: opencode-go-model-optimizer select --format github-output >> "$GITHUB_OUTPUT"

- name: Show balanced tier
  run: echo "Balanced: ${{ steps.models.outputs.balanced }}"
```

Add `--strict` to fail the job (exit `3`) if any tier comes back empty.

### CLI Flags & Options

`--version` (top-level) prints `opencode-go-model-optimizer <version>`.

The filter and optimization flags below apply to both `select` and `rank`. Flags marked *(`select` only)* are rejected by `rank`:

- **`--plan {go,go-plus,go-teams}`**: OpenCode plan tier (default: `go`).
- **`--mode {auto,pareto,allowance}`**: Ranking mode (default: `auto`).
- **`--snapshot <file>`**: Use a local snapshot JSON instead of fetching (see [Offline Usage](#offline-usage)).
- **`-o, --output <file>`**: Write output to a file instead of stdout.
- **`--profile {general,coding,reasoning,fast}`**: Workload profile adjusting token mix and benchmark weights (e.g. `coding` weights AA Coding Index higher, `reasoning` weights Intelligence Index higher, `fast` rewards high TPS).
- **`--skip <model_id>`**: Exclude specific model IDs from selection or ranking (can be specified multiple times).
- **`--min-context <tokens>`**: Enforce a minimum context window threshold (default: 0).
- **`--min-output <tokens>`**: Enforce a minimum output token limit threshold (default: 0).
- **`--max-output-cost <usd>`**: Enforce a maximum output token cost cap in USD per 1M tokens (e.g. `--max-output-cost 15.0`).
- **`--max-burn <pct>`**: Enforce a maximum monthly quota burn percentage per 1,000 standardized turns (e.g. `--max-burn 2.5`). Useful because raw token price does not account for each model's monthly allowance.
- **`--tokens <in,cache,out>`**: Override the workload profile with an exact per-turn token mix (e.g. `--tokens 2000,120000,1000`).
- **`--weights <iq,coding>`**: Override profile benchmark weighting with explicit AA Intelligence and Coding weights (e.g. `--weights 0.5,0.5`).
- **`--no-contributor`**: Exclude contributor-tier models from free tier allocation.
- **`--cache-ttl <seconds>`**: Cache snapshot fetches locally for TTL seconds (default: 0, or `OPENCODE_GO_CACHE_TTL`).
- **`--prefer <tier>=<model_id>`** *(`select` only)*: Pin a model to a tier (e.g. `--prefer heavy=kimi-k3.1-pro`).
- **`--top-n <count>`** *(`select` only)*: Number of ordered candidate models per tier for fallback chains (default: 1). When `top_n > 1`, includes `"fallbacks"` in JSON, `OPENCODE_MODEL_<TIER>_FALLBACKS` in env, and `<tier>_fallbacks` in github-output.
- **`--explain`** *(`select` only)*: Include detailed per-tier metrics (`turn_cost_usd`, `quota_burn_pct`, `bpe`, `aa_iq`, etc.) and selection rationale in table and JSON output.
- **`--strict`** *(`select` only)*: Still writes output, but exits `3` with an stderr error when any tier is empty.
- **`--format`**: Output format. `select` accepts `{json,table,compact,env,github-output}` (default: `json`); `rank` accepts `{table,json}` (default: `table`).
  - `json`: Structured JSON schema.
  - `table`: Formatted ASCII table (with extra metric columns when `--explain` is enabled).
  - `compact`: Clean space-delimited `<tier> <model_id>` lines.
  - `env`: Shell environment variable assignments (`OPENCODE_MODEL_HEAVY="..."`, etc.).
  - `github-output`: GitHub Actions `$GITHUB_OUTPUT` key=value lines (`heavy=...`, etc.).

### Exit Codes

| Code | Meaning |
| ---- | ------- |
| 0 | Success |
| 1 | Runtime error |
| 2 | Usage error, or `diff --fail-on-change` detected changes |
| 3 | `select --strict` found an empty tier |
| 130 | Interrupted |

---

## Development

Requires Python 3.14+ (see `.python-version`; `uv` will fetch it automatically).

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

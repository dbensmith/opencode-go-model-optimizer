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

Requires Python 3.15+.

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
      "fetched_at": "2026-11-10T21:45:55+00:00",
      "benchmark_source": "reference:2026-11-10T21:45:55+00:00"
    }
  }
  ```
  The `snapshot` block (JSON only) records provenance: `source` is `file` (`--snapshot`),
  `cache`, `live`, or `reference` (bundled fallback after a live fetch failure).
  `snapshot.benchmark_source` says where the AA benchmark values (`aa_iq`, `aa_coding`,
  `speed_tps`) came from: `aa_live` (fetched with `AA_API_KEY`), `reference:<fetched_at>`
  (copied from the bundled reference snapshot, so possibly stale), or `none`.
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
  Pricing rows are matched to catalogue models by model ID through the docs "Endpoints" table
  (falling back to name matching for models it does not list). The catalogue comes from the HTTP
  endpoint, with the `opencode` CLI as a fallback; models.dev metadata is read from its
  `opencode-go` provider only. Ingest problems (unmatched models or pricing rows, an empty
  pricing table, benchmarks copied from the bundled reference) are stored in the snapshot's
  `warnings` list and printed to stderr; they never fail the fetch.

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
- **`--price-basis {now,blended,worst,list}`**: How catalogue price multipliers (peak-hour surcharges, expiring promos) are applied to input/output/cache prices before ranking, clustering, `--max-output-cost`, and burn calculations (default: `blended`).
  - `now`: the multiplier in effect at `--at`.
  - `blended`: the time-weighted average multiplier over the 7 days starting at `--at`. This is the default because a selection typically persists for days, and it accounts for imminent promo expiry (e.g. a 50%-off promo ending in 3 days counts as roughly 1.57x of the promo price).
  - `worst`: the highest multiplier in effect at any point in the next 7 days.
  - `list`: ignore multipliers and use the catalogue's base prices (the behaviour before time-aware pricing).

  Models without multipliers are unaffected by every basis. Supported conditions are `utc_days`, `utc_hours`, `date` (`gte`/`gt`/`lte`/`lt`) and `all`/`any` combinators; a multiplier with unrecognised conditions is ignored and reported as a warning when the snapshot is built.
- **`--at <ISO8601>`**: Reference time for `--price-basis` (UTC when no timezone is given; default: now). With `--explain`, each tier reports `price_multiplier` and `next_price_change` (time and new multiplier) when its model has multipliers; `rank` output includes the effective multiplier. `diff` compares list prices and multiplier definitions, so a newly added or removed promo is reported as a pricing change.
- **`--skip <model_id>`**: Exclude specific model IDs from selection or ranking (can be specified multiple times).
- **`--min-context <tokens>`**: Enforce a minimum context window threshold (default: 0).
- **`--min-output <tokens>`**: Enforce a minimum output token limit threshold (default: 0).
- **`--max-output-cost <usd>`**: Enforce a maximum output token cost cap in USD per 1M tokens (e.g. `--max-output-cost 15.0`).
- **`--max-burn <pct>`**: Enforce a maximum monthly quota burn percentage per 1,000 standardized turns (e.g. `--max-burn 2.5`). Useful because raw token price does not account for each model's monthly allowance.
- **`--burn-basis {auto,tokens,estimate}`**: Where quota burn (% of the monthly allowance per 1,000 requests) comes from; it drives `--max-burn`, Pareto/BPE scoring, `--explain` and `rank` (default: `auto`).
  - `tokens`: synthesise it from a token mix (750 in / 60k cached / 250 out, or the `--profile`/`--tokens` mix) times model price, divided by the allowance.
  - `estimate`: use the request estimates the Go docs publish per plan (`Estimated requests`: per 5 hours, week and month). Burn is `1000 / requests_per_month * 100`; unlimited models burn 0. Published estimates are at list price, so the value is scaled by the model's effective price multiplier (`--price-basis`). A model with no published estimate falls back to `tokens`.
  - `auto`: `estimate` unless you pass `--tokens` or a non-`general` `--profile`; those imply you want your own synthetic workload, so `tokens` is used.

  Why: the docs estimates reflect each model's real token profile (cache-heavy models differ a lot from the one-size synthetic mix). They also expose the 5-hour window (20% of monthly), which is the limit users usually hit first: `select --explain` reports `requests_per_5h`, `requests_per_month` and `burn_basis_used`; `rank` shows `requests_per_month` in JSON and a `Req/month` table column (only when estimates are present). Estimates are stored per plan (`request_estimates`: `go`, `go-plus`, `go-teams`), old snapshots without them still load, and `diff` reports estimate changes under pricing changes.
- **`--tokens <in,cache,out>`**: Override the workload profile with an exact per-turn token mix (e.g. `--tokens 2000,120000,1000`).
- **`--weights <iq,coding>`**: Override profile benchmark weighting with explicit AA Intelligence and Coding weights (e.g. `--weights 0.5,0.5`).
- **`--no-contributor`**: Exclude contributor-tier models from free tier allocation.
- **`--allow-global-region`**: Allow models that the docs say require **Global** regions in workspace privacy settings (e.g. `longcat-2.5-free`, hosted in China). Excluded by default, because default workspace settings block these models and selecting one would fail at request time.
- **`--no-training`**: Exclude models whose provider trains on your prompts and completions (e.g. contributor-tier models).
- **`--max-retention-days <N>`**: Exclude models that retain data longer than N days. Models with unknown retention (`Not ZDR`, or no privacy data) fail.
- **`--require-zdr`**: Only allow zero-data-retention models. Models with no privacy data fail.

  These four filters apply to every tier (including `free`), to `rank`, and to `--prefer` pins: a pinned model that fails them is ignored with an `excluded by privacy filters; ignoring` warning. Models without privacy data (older snapshots) pass unless `--max-retention-days` or `--require-zdr` is set. Privacy data comes from the `## Privacy` table and notes in the Go docs and is stored per model as `privacy`: `trains_on_data`, `retention_days` (null for `Not ZDR`/unknown), `zdr` (true when retention is `0 days`), `requires_global_region`, `note` (raw docs note), and `agreement_valid_until` (ISO date from notes such as "valid through November 30, 2026"; a warning is recorded when it is in the past). `select --explain` and `rank --format json` include these fields; the `rank` table adds a `Privacy` column (`ZDR`, `30d`, `train`, `global`), and `diff` reports privacy changes in their own section.
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

Requires Python 3.15+ (see `.python-version`; `uv` will fetch it automatically).

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

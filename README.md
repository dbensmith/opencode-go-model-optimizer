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
- **Auto (`--mode auto`, the default):** Uses Pareto when benchmark data is present, otherwise allowance. Without `AA_API_KEY` no benchmarks exist, so `auto` always resolves to allowance mode.

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
  Example output (allowance mode, no API key; model IDs are illustrative):
  ```json
  {
    "plan": "go",
    "tiers": {
      "heavy": "opencode-go/model-a",
      "balanced": "opencode-go/model-b",
      "light": "opencode-go/model-c",
      "free": "opencode-go/model-d"
    },
    "evaluated_at": "2026-11-11T02:47:40+00:00",
    "mode": "allowance",
    "snapshot": {
      "source": "live",
      "fetched_at": "2026-11-11T02:47:38+00:00",
      "benchmark_source": "none"
    }
  }
  ```
  The `snapshot` block (JSON only) records provenance: `source` is `file` (`--snapshot`),
  `cache`, `live`, or `stale-cache` (your local cache, used after a live fetch failure).
  `snapshot.benchmark_source` says where the AA benchmark values (`aa_iq`, `aa_coding`,
  `speed_tps`) came from: `aa_live` (fetched with your own `AA_API_KEY`) or `none`.
  `--format table` (the default for `rank`) and `--format json` are also available.

- **`rank`**: Produces a ranked comparison of every model in the catalog. Table output is the default:
  ```bash
  opencode-go-model-optimizer rank --format table
  opencode-go-model-optimizer rank --format json
  ```
  Example table (illustrative values):
  ```text
  +------+----------+-------+-----------+-------------+------------+
  | Rank | Model ID | Tier  | Allowance | Output Cost | Release    |
  +------+----------+-------+-----------+-------------+------------+
  | 1    | model-a  | heavy | $30.00    | $10.00/1M   | 2026-10-13 |
  | 2    | model-b  | -     | $15.00    | $0.87/1M    | 2026-09-22 |
  ```
  The legacy `rank --table` flag is still accepted, but `--format table` is the default.

- **`diff`**: Compares catalog, pricing, status, capability/benchmark (context, max output, AA IQ/coding, speed), and tier assignments between two snapshots:
  ```bash
  opencode-go-model-optimizer diff --old previous.json --new snapshot.json
  ```
  `--old` is required (save one earlier with `fetch`); `--new` defaults to a live fetch, falling
  back to your local cache. Use `--format json` for machine-readable drift reports and `--fail-on-change`
  (alias `--exit-code`) to exit `2` when anything changed, which is ideal for scheduled CI jobs.

- **`fetch`**: Captures a normalized, timestamped `snapshot.json` from upstream sources:
  ```bash
  opencode-go-model-optimizer fetch --out snapshot.json
  ```
  Pricing rows are matched to catalogue models by model ID through the docs "Endpoints" table
  (falling back to name matching for models it does not list). The catalogue comes from the HTTP
  endpoint, with the `opencode` CLI as a fallback; models.dev metadata is read from its
  `opencode-go` provider only. Ingest problems (unmatched models or pricing rows, an empty
  pricing table, an AA key that returned no benchmarks) are stored in the snapshot's
  `warnings` list and printed to stderr; they never fail the fetch.

- **`report`**: Fetches live (never from the cache) and writes `results.json` and `README.md` to `--out-dir`, covering every plan (`go`, `go-plus`, `go-teams`) in allowance mode with default criteria and `--top-n 3`. It records the tier picks and fallbacks, why each was chosen (eligibility filters, ranking keys and their values, allowance, effective price multiplier, burn and the basis used, privacy fields), every excluded model with its reason, warnings, and source provenance (URL, HTTP status, fetch time, sha256 and byte length of each raw response, never the bodies). Pass `--tests-junit PATH` (repeatable) to embed pass/fail/skip counts and failing test names. It exits `0` even with warnings; `--fail-on-warnings` exits `2` after writing. No Artificial Analysis data ever enters the report.
  ```bash
  opencode-go-model-optimizer report --out-dir out --tests-junit offline.xml --tests-junit live.xml
  ```

### Published results

A scheduled workflow ([`publish-results.yml`](.github/workflows/publish-results.yml)) runs twice daily (05:17 and 17:17 UTC), executes the offline and live contract tests, runs `report`, and force-pushes a single orphan commit to the `results` branch, so the branch has no history. It is published whatever the test outcome, and the job then fails if a test run failed.

- Machine-readable: <https://raw.githubusercontent.com/dbensmith/opencode-go-model-optimizer/results/results.json>
- Human-readable (tables, how it was decided, warnings, provenance, test results): <https://raw.githubusercontent.com/dbensmith/opencode-go-model-optimizer/results/README.md>

### Data policy

- **No source data is stored in this repository.** The repo ships no model catalogue, docs, pricing or benchmark snapshots, and the tests use synthetic fixtures with fictional models.
- **Caches are consumer-maintained.** The tool writes a snapshot cache under `$XDG_CACHE_HOME/opencode-go-model-optimizer` (default `~/.cache/opencode-go-model-optimizer`) after each successful live fetch. Nothing is bundled or pre-populated; if you never fetch, there is no cache.
- **Do it yourself.** Everything is fetched live from the public OpenCode catalogue, the OpenCode docs and models.dev on your machine. Use `fetch --out snapshot.json` to keep your own snapshot.
- **Artificial Analysis** terms forbid republishing their raw data, so none is committed or published by CI. Benchmarks are used only when you supply your own key.

### API keys

None are required. Allowance mode (and therefore `auto` without a key) needs no key. For Pareto mode you can set a free Artificial Analysis key in `AA_API_KEY`; it stays on your machine and only your own runs use it. When you display AA-derived values (`aa_iq`, `aa_coding`, `speed_tps`), attribute them: "Source: Artificial Analysis (artificialanalysis.ai)".

### Offline Usage

- `--snapshot <file>` reads a local snapshot JSON and makes no network calls, so it works fully offline. Use it with your own `fetch` output.
- When a live fetch fails, the tool uses your local cache (of any age, ignoring the TTL) and prints a warning with the cache's `fetched_at` and age to stderr. The JSON `snapshot.source` field reports `stale-cache` in that case. If there is no cache it fails with an error suggesting `--snapshot` or `fetch`.
- `--cache-ttl <seconds>` (or `OPENCODE_GO_CACHE_TTL`) serves a fresh-enough cache instead of fetching, for that many seconds. The default is `0` (always fetch).

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
- **`--allow-global-region`**: Allow models that the docs say require **Global** regions in workspace privacy settings (e.g. a free model hosted outside your allowed regions). Excluded by default, because default workspace settings block these models and selecting one would fail at request time.
- **`--no-training`**: Exclude models whose provider trains on your prompts and completions (e.g. contributor-tier models).
- **`--max-retention-days <N>`**: Exclude models that retain data longer than N days. Models with unknown retention (`Not ZDR`, or no privacy data) fail.
- **`--require-zdr`**: Only allow zero-data-retention models. Models with no privacy data fail.

  These four filters apply to every tier (including `free`), to `rank`, and to `--prefer` pins: a pinned model that fails them is ignored with an `excluded by privacy filters; ignoring` warning. Models without privacy data (older snapshots) pass unless `--max-retention-days` or `--require-zdr` is set. Privacy data comes from the `## Privacy` table and notes in the Go docs and is stored per model as `privacy`: `trains_on_data`, `retention_days` (null for `Not ZDR`/unknown), `zdr` (true when retention is `0 days`), `requires_global_region`, `note` (raw docs note), and `agreement_valid_until` (ISO date from notes such as "valid through November 30, 2026"; a warning is recorded when it is in the past). `select --explain` and `rank --format json` include these fields; the `rank` table adds a `Privacy` column (`ZDR`, `30d`, `train`, `global`), and `diff` reports privacy changes in their own section.
- **`--cache-ttl <seconds>`**: Cache snapshot fetches locally for TTL seconds (default: 0, or `OPENCODE_GO_CACHE_TTL`).
- **`--prefer <tier>=<model_id>`** *(`select` only)*: Pin a model to a tier (e.g. `--prefer heavy=model-a`).
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
| 2 | Usage error, `diff --fail-on-change` detected changes, or `report --fail-on-warnings` found warnings |
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

The default `pytest` run is offline and uses only synthetic fixtures. Live contract tests (`tests/live`, marked `live`) fetch the real sources and assert structural invariants only (tables parse, every active model is matched to pricing/privacy/estimates, multipliers are understood, paid tiers are non-empty). They are skipped unless you opt in:

```bash
OCGO_LIVE=1 uv run pytest tests/live -m live
```

---

## License

MIT License. See [LICENSE](LICENSE) for details.

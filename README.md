# OpenCode Go Model Optimizer (`opencode-go-model-optimizer`)

Dynamic model tiering, Pareto efficiency ranker, and GSD selector for OpenCode Go ($10/mo tier).

Available as a standalone Python CLI via `pipx` / `uvx` and consumable by configuration frameworks (Open GSD, Chezmoi dotfiles, Paseo agent profiles, and Ansible control nodes).

---

## Overview & Architecture

`opencode-go-model-optimizer` unifies two complementary capabilities:
1. **Mathematical Pareto Efficiency & Quality Ranking:** Multi-objective optimization (Quality vs. Monthly Burn) with natural clustering across 4 tiers (Opus, Sonnet, Haiku, Free).
2. **Operational Tier Selection:** Fast, deterministic model selection for Open GSD and agent profile overrides based on monthly allowance ceilings and release recency.

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
│                                         │      Output Adapters      │  │
│                                         │ - Full Report (Markdown)  │  │
│                                         │ - GSD opencode-go-model   │  │
│                                         │ - Raw JSON Snapshot       │  │
│                                         └───────────────────────────┘  │
└────────────────────────────────────────────────────────────────────────┘
```

### Key Principles

- **Separation of Concerns:** Core model evaluation is cleanly isolated from local process supervisors or dotfiles tools. Downstream hooks (e.g. `chezmoi apply`, `paseo daemon restart`) consume output via standard formats.
- **Dual-Mode Operation:**
  - **Pareto Mode (`--mode pareto`):** Evaluates Artificial Analysis benchmarks (Intelligence & Coding Indices) against monthly turn burn using Bidirectional Pareto Efficiency (BPE) and Fisher-Jenks breaks into 4 tiers.
  - **Allowance Mode (`--mode allowance`):** Zero-API-key mode ranking paid models by maximum monthly allowance from the published Go plan tables and free models by recency.
- **Rigor & Determinism:** Offline fixtures for all network inputs, zero silent degradation, and deterministic tie-breaking (release date &rarr; context window &rarr; model ID).

---

## Installation & Usage

### Via `pipx`
```bash
pipx install opencode-go-model-optimizer
opencode-go-model-optimizer --help
```

### Via `uvx` (Ephemerally)
```bash
uvx opencode-go-model-optimizer select --format gsd
```

### Commands

- **`select`**: Selects operational models for tier mappings.
  ```bash
  opencode-go-model-optimizer select --format gsd --output dot_gsd/opencode-go-model.json
  ```
- **`rank`**: Generates full catalog ranking and tier assignments.
  ```bash
  opencode-go-model-optimizer rank --table
  ```
- **`fetch`**: Captures an immutable normalized `snapshot.json` from live endpoints.
  ```bash
  opencode-go-model-optimizer fetch --out snapshot.json
  ```

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

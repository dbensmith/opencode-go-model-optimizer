# OpenCode Go model tiers

Generated 2026-11-14T12:14:21+00:00 by opencode-go-model-optimizer 0.1.0 (commit `4e168c926771a10b4c54ab5ecac2b3165cd10625`). Allowance mode, default criteria, top 3 per tier.

## Plan: go

| Tier | Pick | Allowance | Out $/1M | Price x | Burn %/1k turns | Basis | Fallbacks |
| --- | --- | --- | --- | --- | --- | --- | --- |
| heavy | `opencode-go/gemini-4-argon` | 30.0 | $10 | 1.0 | 47.17 | estimate | `opencode-go/kimi-k3.1-pro`, `opencode-go/kimi-k3` |
| balanced | `opencode-go/hy4` | 60.0 | $2.501 | 1 | 7.41 | estimate | `opencode-go/kimi-k2.7-code`, `opencode-go/glm-5.2` |
| light | `opencode-go/deepseek-v4.1-flash` | 100.0 | $0.725 | 1.2083 | 0.56 | estimate | `opencode-go/mimo-v2.6-flash`, `opencode-go/glm-5.3-flash` |
| free | `opencode-go/iceman-contributor-free` | unlimited | $0 | 1 | 0.0 | estimate | `opencode-go/beam-free` |

Why:

- **heavy**: opencode-go/gemini-4-argon has the highest allowance among eligible models in the heavy price cluster, with ties broken by lower output price, newer release, larger context
  - cluster of 3 model(s), effective output $10-$15/1M
  - privacy: trains_on_data=False, retention_days=0, zdr=True, global_region_required=False
- **balanced**: opencode-go/hy4 has the highest allowance among eligible models in the balanced price cluster, with ties broken by lower output price, newer release, larger context
  - cluster of 7 model(s), effective output $2.3925-$6/1M
  - privacy: trains_on_data=False, retention_days=0, zdr=True, global_region_required=False
- **light**: opencode-go/deepseek-v4.1-flash has the highest allowance among eligible models in the light price cluster, with ties broken by lower output price, newer release, larger context
  - cluster of 13 model(s), effective output $0.28-$1.6/1M
  - privacy: trains_on_data=False, retention_days=0, zdr=True, global_region_required=False
- **free**: opencode-go/iceman-contributor-free is the newest eligible zero-cost model (then larger context, then id)
  - privacy: trains_on_data=True, retention_days=None, zdr=False, global_region_required=False

<details><summary>Excluded models</summary>

| Model | Reason |
| --- | --- |
| `opencode-go/hy4-preview` | preview/experimental model |
| `opencode-go/longcat-2.5-free` | requires Global workspace region (excluded by default) |
| `opencode-go/muse-spark-1.2-contributor` | contributor model with a finite allowance |
| `opencode-go/muse-spark-1.3-contributor` | contributor model with a finite allowance |
| `opencode-go/step-5-preview` | preview/experimental model |

</details>

## Plan: go-plus

| Tier | Pick | Allowance | Out $/1M | Price x | Burn %/1k turns | Basis | Fallbacks |
| --- | --- | --- | --- | --- | --- | --- | --- |
| heavy | `opencode-go/gemini-4-argon` | 120.0 | $10 | 1.0 | 11.79 | estimate | `opencode-go/kimi-k3.1-pro`, `opencode-go/kimi-k3` |
| balanced | `opencode-go/hy4` | 240.0 | $2.501 | 1 | 1.85 | estimate | `opencode-go/kimi-k2.7-code`, `opencode-go/glm-5.2` |
| light | `opencode-go/hy3` | 240.0 | $0.58 | 1 | 1.16 | estimate | `opencode-go/longcat-2.0`, `opencode-go/deepseek-v4.1-flash` |
| free | `opencode-go/iceman-contributor-free` | unlimited | $0 | 1 | 0.0 | estimate | `opencode-go/beam-free` |

Why:

- **heavy**: opencode-go/gemini-4-argon has the highest allowance among eligible models in the heavy price cluster, with ties broken by lower output price, newer release, larger context
  - cluster of 3 model(s), effective output $10-$15/1M
  - privacy: trains_on_data=False, retention_days=0, zdr=True, global_region_required=False
- **balanced**: opencode-go/hy4 has the highest allowance among eligible models in the balanced price cluster, with ties broken by lower output price, newer release, larger context
  - cluster of 7 model(s), effective output $2.3925-$6/1M
  - privacy: trains_on_data=False, retention_days=0, zdr=True, global_region_required=False
- **light**: opencode-go/hy3 has the highest allowance among eligible models in the light price cluster, with ties broken by lower output price, newer release, larger context
  - cluster of 13 model(s), effective output $0.28-$1.6/1M
  - privacy: trains_on_data=False, retention_days=0, zdr=True, global_region_required=False
- **free**: opencode-go/iceman-contributor-free is the newest eligible zero-cost model (then larger context, then id)
  - privacy: trains_on_data=True, retention_days=None, zdr=False, global_region_required=False

<details><summary>Excluded models</summary>

| Model | Reason |
| --- | --- |
| `opencode-go/hy4-preview` | preview/experimental model |
| `opencode-go/longcat-2.5-free` | requires Global workspace region (excluded by default) |
| `opencode-go/muse-spark-1.2-contributor` | contributor model with a finite allowance |
| `opencode-go/muse-spark-1.3-contributor` | contributor model with a finite allowance |
| `opencode-go/step-5-preview` | preview/experimental model |

</details>

## Plan: go-teams

| Tier | Pick | Allowance | Out $/1M | Price x | Burn %/1k turns | Basis | Fallbacks |
| --- | --- | --- | --- | --- | --- | --- | --- |
| heavy | `opencode-go/gemini-4-argon` | 35.0 | $10 | 1.0 | 40.49 | estimate | `opencode-go/kimi-k3.1-pro`, `opencode-go/kimi-k3` |
| balanced | `opencode-go/hy4` | 65.0 | $2.501 | 1 | 6.84 | estimate | `opencode-go/kimi-k2.7-code`, `opencode-go/glm-5.2` |
| light | `opencode-go/deepseek-v4.1-flash` | 105.0 | $0.725 | 1.2083 | 0.53 | estimate | `opencode-go/mimo-v2.6-flash`, `opencode-go/glm-5.3-flash` |
| free | `opencode-go/iceman-contributor-free` | unlimited | $0 | 1 | 0.0 | estimate | `opencode-go/beam-free` |

Why:

- **heavy**: opencode-go/gemini-4-argon has the highest allowance among eligible models in the heavy price cluster, with ties broken by lower output price, newer release, larger context
  - cluster of 3 model(s), effective output $10-$15/1M
  - privacy: trains_on_data=False, retention_days=0, zdr=True, global_region_required=False
- **balanced**: opencode-go/hy4 has the highest allowance among eligible models in the balanced price cluster, with ties broken by lower output price, newer release, larger context
  - cluster of 7 model(s), effective output $2.3925-$6/1M
  - privacy: trains_on_data=False, retention_days=0, zdr=True, global_region_required=False
- **light**: opencode-go/deepseek-v4.1-flash has the highest allowance among eligible models in the light price cluster, with ties broken by lower output price, newer release, larger context
  - cluster of 13 model(s), effective output $0.28-$1.6/1M
  - privacy: trains_on_data=False, retention_days=0, zdr=True, global_region_required=False
- **free**: opencode-go/iceman-contributor-free is the newest eligible zero-cost model (then larger context, then id)
  - privacy: trains_on_data=True, retention_days=None, zdr=False, global_region_required=False

<details><summary>Excluded models</summary>

| Model | Reason |
| --- | --- |
| `opencode-go/hy4-preview` | preview/experimental model |
| `opencode-go/longcat-2.5-free` | requires Global workspace region (excluded by default) |
| `opencode-go/muse-spark-1.2-contributor` | contributor model with a finite allowance |
| `opencode-go/muse-spark-1.3-contributor` | contributor model with a finite allowance |
| `opencode-go/step-5-preview` | preview/experimental model |

</details>

## How this was decided

- Mode: allowance. No benchmark data is used; models are ranked by published plan allowance and price only.
- Eligibility: only active models are considered. Preview/experimental models, models requiring a Global workspace region, and models without a positive finite allowance are excluded from the paid tiers (the privacy filter here is the default: Global-region models excluded, training and retention not restricted).
- Price: catalogue token prices are scaled by each model's price multiplier (peak hours, promo expiry) using the 7-day time-weighted average starting at generation time.
- Tiers: eligible paid models are split into three clusters by log effective output price (natural breaks): the dearest cluster is heavy, the middle balanced, the cheapest light.
- Ranking inside a cluster: highest monthly allowance first, then lowest effective output price, then newest release date, then largest context window, then model id.
- Free tier: zero-cost (or contributor/unlimited) models, newest release first, then largest context window, then model id.
- Quota burn: percent of the monthly allowance consumed per 1,000 turns, from the docs request estimates when published (scaled by the price multiplier), otherwise from a synthetic token mix. Shown for context; it is not a ranking key in allowance mode.
- Fallbacks: the next 2 candidates per tier, drawn from the same cluster and then from the general paid ranking.

## Warnings

- 2 docs pricing row(s) matched no catalogue model: MiMo-V2.5, MiMo-V2.5-Pro

## Provenance

| Source | URL | HTTP | Fetched | sha256 | Bytes |
| --- | --- | --- | --- | --- | --- |
| docs | https://raw.githubusercontent.com/anomalyco/opencode/dev/services/site/src/docs/content/console/go.mdx | 200 | 2026-11-14T12:14:21+00:00 | `a782a38e295074ba...` | 56625 |
| catalogue | https://opencode.ai/inference/go/v1/models | 200 | 2026-11-14T12:14:21+00:00 | `36f183dd5c8af056...` | 35793 |
| models_dev | https://models.dev/api.json | 200 | 2026-11-14T12:14:21+00:00 | `021f44edf970945e...` | 6125012 |

sha256 and byte length of each raw response body; bodies are not published. Full digests are in results.json.

## Test results

Overall: **pass**

| Run | Passed | Failed | Skipped |
| --- | --- | --- | --- |
| offline | 255 | 0 | 0 |
| live | 14 | 0 | 0 |

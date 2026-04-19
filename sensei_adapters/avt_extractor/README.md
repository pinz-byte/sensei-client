# AVT Extractor — SENSEI Adapter v1

Adapter for the AVT Extractor pipeline: vehicle-listing scraping across LATAM sources, feeding the canonical listings table consumed by Subastop, CarMatch, and downstream valuation models.

This directory is the reference-artifact location on M1. In production the three files live inside the AVT Extractor repo and are installed alongside the scraper runtime. They are duplicated here only so M1 can archive the second-venture-adoption artifacts that validated contract v0.3 as agnostic.

## What is gated

Every scrape run's output envelope — before the batch commits to the canonical listings table. The Worker is the scraper + normalizer agent; the output is the per-run extraction envelope. SENSEI's decision answers one question: **admit this batch to canonical, or hold it for human review?**

## The four materiality factors

| factor | weight | what it catches |
|--------|--------|-----------------|
| `volume_anomaly` | 0.30 | Scraper ran but returned far too few (silently broken) or far too many (pagination leak, wrong URL) rows. |
| `schema_failure` | 0.25 | Rows failed normalization — site structure shifted, field types changed, required fields missing. |
| `price_outlier_ratio` | 0.30 | Too many prices fall outside the make/model/year baseline — currency misparse, junk listings, regex drift. |
| `source_novelty` | 0.15 | Source has fewer than 5 historical successful runs — short baseline, can't trust the above three metrics. |

Weights sum to 1.0. Weighted-sum composition, clipped to `[0.0, 1.0]`.

Threshold: `0.40`. Above threshold → escalate to Advisor (Claude Opus 4.7) before commit.

## The seven hard-task signals

Signal patterns fire server-side against the envelope text and contribute independently to the effective score (per contract v0.3 §2.2):

1. `extraction_volume_collapse` (0.85 base / 0.30 boost) — phrases like "volume collapse", "extraction truncated"
2. `schema_drift_detected` (0.80 / 0.25) — "schema drift", "schema_failures", "missing required fields"
3. `price_outlier_spike` (0.75 / 0.25) — price distribution irregularity
4. `antibot_escalation` (0.65 / 0.20) — captcha / rate-limit / fallback path
5. `novel_source` (0.60 / 0.20) — `source_runs_completed: 0–4`
6. `canonical_write` (0.90 / 0.35) — the irreversibility gate
7. `cross_market_write` (0.85 / 0.30) — market/region boundary violation

## The Worker envelope shape

```
AVT_EXTRACT source_id=mercadolibre_pe.auto rows_extracted=1847
expected_rows=2000 schema_failures=23 price_outliers=45
source_runs_completed=12 antibot_challenges=0
destination=canonical_listings notes=["captcha evaded once"]
```

The envelope is plain text so SENSEI's keyword/regex matchers can still fire. The materiality function parses the numeric fields out via regex.

## Three-line integration

```python
from sensei_client import SenseiConfig, SenseiClient, check_and_escalate
from sensei_client.types import WorkerTask
from avt_extractor.avt_materiality import compute_avt_materiality_value

config = SenseiConfig.from_spec_file(
    "avt_extractor.v1.json",
    api_url=os.environ["SENSEI_API_URL"],
)

with SenseiClient(config) as client:
    client.register_from_config()
    result = check_and_escalate(
        task,
        materiality_fn=compute_avt_materiality_value,
        config=config,
        client=client,
    )
    if result.should_ship:
        commit_batch_to_canonical(batch)
    else:
        hold_batch_for_review(batch, reason=result.advisor_result)
```

No contract changes were required. Same `check_and_escalate` entry point, same `SenseiConfig.from_spec_file` loader, same JSON registration path.

## Worth calling out

- The adapter uses **materiality_value** (JSON path), not the Python plugin path. This means SENSEI sees only the final float; the four-factor decomposition is logged inside AVT's observability. Plugin path is overkill here — AVT's materiality logic is pure, self-contained, and trivial to port across stations.
- `canonical_write` is the AVT analog of HERALD's `destructive_operation` signal. It exists because AVT's irreversibility is structurally different from HERALD's: HERALD worries about SQL-style destruction, AVT worries about promotion-to-consumers. Same underlying invariant (reversibility matters), different domain surface.
- The `advisor_per_session_cap` is `6` (vs HERALD's `4`). AVT runs scrape sessions of many sources back-to-back; a slightly higher cap is appropriate.
- `source_novelty` has the lowest weight (0.15) because it's an uncertainty multiplier, not a stakes signal. Its job is to raise scrutiny on early runs, not to claim early runs are "high-stakes" in themselves. Tuning this dial tells the system how paranoid to be about new sources.

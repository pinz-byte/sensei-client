"""
AVT Extractor materiality computation — reference implementation.

Belongs in the AVT Extractor's repo, not sensei_adapters/. Placed here
as a reference artifact for the second-venture adoption test of the
v0.3 JSON-registration path.

Under the JSON-registration path (contract v0.3), materiality is
computed by the caller and passed as `materiality_value` in the
POST /decide body. SENSEI never sees the decomposition, only the final
float.

AVT's Worker emits a structured extraction envelope per scrape run.
This function parses the envelope and scores operational stakes —
"how bad is it if we admit this batch to canonical and the decision
turns out to be wrong."

Never calls an LLM. Never returns None. Returns value=0.0 when no
signal is present. Pure, offline, deterministic.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Envelope field parsers
# ---------------------------------------------------------------------------
# AVT Worker emits envelopes shaped roughly as:
#
#   AVT_EXTRACT source_id=mercadolibre_pe.auto rows_extracted=1847
#   expected_rows=2000 schema_failures=23 price_outliers=45
#   source_runs_completed=12 antibot_challenges=0 destination=canonical_listings
#
# The envelope is plain text so SENSEI's signal matchers can still fire
# against it. We pull structured numbers out by regex.
_FIELD = lambda name: re.compile(  # noqa: E731 — concise factory
    rf"\b{name}\s*[:=]\s*(\d+)", re.IGNORECASE
)

_ROWS_EXTRACTED = _FIELD("rows_extracted")
_EXPECTED_ROWS = _FIELD("expected_rows")
_SCHEMA_FAILURES = _FIELD("schema_failures")
_PRICE_OUTLIERS = _FIELD("price_outliers")
_SOURCE_RUNS_COMPLETED = _FIELD("source_runs_completed")

# ---------------------------------------------------------------------------
# Component weights (sum = 1.0)
# ---------------------------------------------------------------------------
# Rationale:
#   - Volume anomaly is the loudest signal of silent breakage: catches
#     the "scraper ran, returned zero" class of failure that no other
#     metric catches. High weight.
#   - Schema drift breaks normalization and cascades immediately.
#   - Price outlier ratio is the data-quality proxy for the consumer.
#   - Source novelty is the epistemic humility factor: short history =
#     can't trust the other three factors' baselines. Lower weight
#     because it's a multiplier on uncertainty, not a stakes signal.
_W_VOLUME_ANOMALY = 0.30
_W_SCHEMA_FAILURE = 0.25
_W_PRICE_OUTLIER = 0.30
_W_SOURCE_NOVELTY = 0.15

# Source is "novel" when it has fewer than this many successful runs.
_NOVEL_SOURCE_RUN_THRESHOLD = 5


@dataclass
class MaterialityBreakdown:
    """Internal AVT observability object. Not sent to SENSEI."""
    value: float
    volume_anomaly_factor: float
    schema_failure_factor: float
    price_outlier_factor: float
    source_novelty_factor: float
    rows_extracted: int
    expected_rows: int
    schema_failures: int
    price_outliers: int
    source_runs_completed: int


def _parse_int(pattern: re.Pattern[str], text: str, default: int = 0) -> int:
    """Pull a single int out of the envelope. Returns default if absent."""
    m = pattern.search(text or "")
    if not m:
        return default
    try:
        return int(m.group(1))
    except (ValueError, IndexError):
        return default


def compute_avt_materiality(worker_output: str) -> MaterialityBreakdown:
    """Compute AVT Extractor's stakes score for a single extraction run.

    Returns a MaterialityBreakdown so AVT can log the decomposition for
    its own observability. Only the `value` field crosses the wire to
    SENSEI.

    Never calls an LLM. Never returns None. Returns value=0.0 when the
    envelope is empty or unparseable (which itself is a signal — but
    the hard-task registry's keyword matchers handle that surface).
    """
    text = worker_output or ""

    rows_extracted = _parse_int(_ROWS_EXTRACTED, text)
    expected_rows = _parse_int(_EXPECTED_ROWS, text)
    schema_failures = _parse_int(_SCHEMA_FAILURES, text)
    price_outliers = _parse_int(_PRICE_OUTLIERS, text)
    source_runs_completed = _parse_int(_SOURCE_RUNS_COMPLETED, text)

    # --- volume anomaly ----------------------------------------------------
    # Normalized absolute delta from expectation. Symmetric: too few rows
    # (scraper broke) and too many rows (pagination leak / wrong URL)
    # are both stakes-raising.
    if expected_rows > 0 and rows_extracted >= 0:
        denom = max(expected_rows, rows_extracted, 1)
        volume_anomaly_factor = min(
            1.0, abs(rows_extracted - expected_rows) / denom
        )
    else:
        volume_anomaly_factor = 0.0

    # --- schema failure rate ----------------------------------------------
    # Fraction of extracted rows that failed schema validation.
    if rows_extracted > 0:
        schema_failure_factor = min(1.0, schema_failures / rows_extracted)
    else:
        # rows_extracted == 0 is its own signal, handled by volume anomaly.
        schema_failure_factor = 0.0

    # --- price outlier ratio ----------------------------------------------
    if rows_extracted > 0:
        price_outlier_factor = min(1.0, price_outliers / rows_extracted)
    else:
        price_outlier_factor = 0.0

    # --- source novelty ----------------------------------------------------
    # Binary: either we have history on this source or we don't. The
    # threshold is empirical; first five runs are the window where
    # selector fragility and site-specific edge cases surface.
    source_novelty_factor = (
        1.0 if source_runs_completed < _NOVEL_SOURCE_RUN_THRESHOLD else 0.0
    )

    raw = (
        _W_VOLUME_ANOMALY * volume_anomaly_factor
        + _W_SCHEMA_FAILURE * schema_failure_factor
        + _W_PRICE_OUTLIER * price_outlier_factor
        + _W_SOURCE_NOVELTY * source_novelty_factor
    )
    value = max(0.0, min(1.0, raw))

    return MaterialityBreakdown(
        value=value,
        volume_anomaly_factor=volume_anomaly_factor,
        schema_failure_factor=schema_failure_factor,
        price_outlier_factor=price_outlier_factor,
        source_novelty_factor=source_novelty_factor,
        rows_extracted=rows_extracted,
        expected_rows=expected_rows,
        schema_failures=schema_failures,
        price_outliers=price_outliers,
        source_runs_completed=source_runs_completed,
    )


def compute_avt_materiality_value(worker_output: str) -> float:
    """Convenience: return just the materiality float for SENSEI.

    This is the signature that plugs directly into
    `sensei_client.guard.check_and_escalate` as the `materiality_fn`.
    """
    return compute_avt_materiality(worker_output).value


# ---------------------------------------------------------------------------
# Example wire-up — what an AVT /decide call looks like via sensei_client
# ---------------------------------------------------------------------------
#
# from sensei_client import SenseiConfig, SenseiClient, check_and_escalate
# from sensei_client.types import WorkerTask
# from avt_extractor.sensei_wiring import compute_avt_materiality_value
#
# config = SenseiConfig.from_spec_file(
#     "sensei_adapters/avt_extractor/avt_extractor.v1.json",
#     api_url=os.environ["SENSEI_API_URL"],
# )
#
# with SenseiClient(config) as client:
#     client.register_from_config()
#
#     task = WorkerTask(
#         task_id="avt-run-0042",
#         worker_output=extraction_envelope,   # the AVT_EXTRACT ... string
#         worker_model="avt-scraper-v3",
#         session_id="avt-batch-2026-04-17",
#         turn_index=1,
#         confidence_score=0.91,
#     )
#
#     result = check_and_escalate(
#         task,
#         materiality_fn=compute_avt_materiality_value,
#         config=config,
#         client=client,
#     )
#
#     if result.should_ship:
#         commit_batch_to_canonical(batch)
#     else:
#         hold_batch_for_review(batch, reason=result.advisor_result)

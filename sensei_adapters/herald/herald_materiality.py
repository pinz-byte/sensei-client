"""
HERALD-side materiality computation — reference implementation.

Belongs in HERALD's repo, not sensei_adapters/. Placed here temporarily
as a reference artifact for the JSON-registration migration.

Under the JSON-registration path (contract v0.3), materiality is computed
by the caller and passed as `materiality_value` in the POST /decide body.
SENSEI never sees the decomposition, only the final float.

This is the same four-factor model that lived in the old Python adapter's
_compute_materiality, with one shape change: returns a float in [0.0, 1.0]
instead of a Materiality dataclass. HERALD is free to log the components
internally for its own observability.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# [A4] destructive-op surface — SQL + stream
_DESTRUCTIVE_PATTERN = re.compile(
    r"\b(?:drop|truncate|purge|overwrite)\b|"
    r"\bdelete\s+from\b|"
    r"\brm\s+-rf\b|"
    r"\b(?:tombstone|compact|expire)\b|"
    r"\bdead[-\s]?letter\b",
    re.IGNORECASE,
)
# [A6] regulated-data class surface — includes MNPI
_CLASSIFIED_PATTERN = re.compile(
    r"\b(?:PII|PCI|PHI|MNPI|SSN|HIPAA|GDPR|CCPA)\b|"
    r"\bcredit\s*card\b|"
    r"\b(?:sensitive|regulated)\s+(?:data|field|column)\b",
    re.IGNORECASE,
)
# [A5] cross-boundary + fan-out vocabulary
_CROSS_BOUNDARY_PATTERN = re.compile(
    r"\bexternal\s+(?:sink|destination|system)\b|"
    r"\bthird[-\s]party\b|"
    r"\bcross[-\s]tenant\b|"
    r"\begress\b|"
    r"\bexport\s+to\b|"
    r"\bpublish\s+to\s+downstream\b|"
    r"\bfan[-\s]?out\b|"
    r"\bbroadcast\b",
    re.IGNORECASE,
)
# [A8] record/hub volume nouns
_VOLUME_PATTERN = re.compile(
    r"(\d{1,12})\s*(?:rows?|records?|entries|documents?|"
    r"events?|messages?|alerts?|transactions?)",
    re.IGNORECASE,
)

# [A11] volume inflection point — HERALD empirical p90
_VOLUME_REFERENCE = 500_000

# [A10] component weights (sum = 1.0)
_W_VOLUME = 0.20
_W_DESTRUCTIVE = 0.30
_W_CLASSIFIED = 0.30
_W_CROSS_BOUNDARY = 0.20


@dataclass
class MaterialityBreakdown:
    """Internal HERALD observability object. Not sent to SENSEI."""
    value: float
    volume_factor: float
    destructive_factor: float
    classified_factor: float
    cross_boundary_factor: float
    volume_n: int
    output_length: int


def compute_herald_materiality(worker_output: str) -> MaterialityBreakdown:
    """Compute HERALD's stakes score for a Worker output.

    Returns a MaterialityBreakdown so HERALD can log the decomposition
    for its own observability. Only the `value` field crosses the wire
    to SENSEI.

    Never calls an LLM. Never returns None. Returns value=0.0 when no
    signal is present.
    """
    text = worker_output or ""

    volume_matches = _VOLUME_PATTERN.findall(text)
    volume_n = max((int(n) for n in volume_matches), default=0)
    volume_factor = (
        min(1.0, volume_n / _VOLUME_REFERENCE) if volume_n else 0.0
    )

    destructive_factor = 1.0 if _DESTRUCTIVE_PATTERN.search(text) else 0.0
    classified_factor = 1.0 if _CLASSIFIED_PATTERN.search(text) else 0.0
    cross_boundary_factor = (
        1.0 if _CROSS_BOUNDARY_PATTERN.search(text) else 0.0
    )

    raw = (
        _W_VOLUME * volume_factor
        + _W_DESTRUCTIVE * destructive_factor
        + _W_CLASSIFIED * classified_factor
        + _W_CROSS_BOUNDARY * cross_boundary_factor
    )
    value = max(0.0, min(1.0, raw))

    return MaterialityBreakdown(
        value=value,
        volume_factor=volume_factor,
        destructive_factor=destructive_factor,
        classified_factor=classified_factor,
        cross_boundary_factor=cross_boundary_factor,
        volume_n=volume_n,
        output_length=len(text),
    )


# ---------------------------------------------------------------------------
# Example wire-up — what a HERALD /decide call now looks like
# ---------------------------------------------------------------------------
#
# import httpx
#
# SENSEI_API_URL = os.environ["SENSEI_API_URL"]
#
# def escalate_check(task_id, worker_output, worker_model, session_id,
#                    turn_index, confidence_score,
#                    confidence_coverage, confidence_grounding,
#                    confidence_novelty):
#     breakdown = compute_herald_materiality(worker_output)
#
#     # Log the decomposition to HERALD's own observability
#     herald_log_materiality(task_id, breakdown)
#
#     # Send only the final float to SENSEI
#     resp = httpx.post(
#         f"{SENSEI_API_URL}/adapters/herald.v1/decide",
#         json={
#             "task_id": task_id,
#             "worker_output": worker_output,
#             "worker_model": worker_model,
#             "session_id": session_id,
#             "turn_index": turn_index,
#             "confidence_score": confidence_score,
#             "confidence_coverage": confidence_coverage,
#             "confidence_grounding": confidence_grounding,
#             "confidence_novelty": confidence_novelty,
#             "materiality_value": breakdown.value,
#         },
#         timeout=5.0,
#     )
#
#     # Handle 404: adapter spec was lost on SENSEI restart — re-register
#     if resp.status_code == 404:
#         register_herald_adapter()  # POST /adapters with herald.v1.json
#         resp = httpx.post(...)  # retry once
#
#     return resp.json()

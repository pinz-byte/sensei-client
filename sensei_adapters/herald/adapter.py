"""
herald.v1 — SENSEI adapter for the HERALD data-hub workload.

STATUS: v1 — all 17 domain assumptions resolved with M3. Ready for
registry commit via sensei_api/registry.py.

Contract pin: SENSEI Contract v0.2
Adapter ID:   herald.v1
Authored by:  M1 (HERALD integration)
Registrar:    M3 (owns sensei_api/registry.py)

Invariants respected:
  I4  — _compute_materiality never returns None
  I11 — materiality scores stakes, never output quality
  NRC — no LLM calls anywhere in this module

=============================================================================
ASSUMPTION LEDGER — resolved state
=============================================================================

[A1]  RESOLVED — Free text is the primary Worker output modality. A subset
      of Workers (post-Pusher-migration) append a structured tail envelope
      with {operation, confidence, warnings, target}. Not universal, not
      contractually enforced. Patterns 1–5 stay as Keyword/Regex matchers.

[A2]  PROVISIONAL — Upstream-trust phrase family accepted. Validate against
      real Worker output samples in v1.1.

[A3]  PROVISIONAL — Anomaly/drift phrase family accepted. Validate against
      real Worker output samples in v1.1.

[A4]  EXTENDED — Destructive-op surface is SQL-flavored BASE + stream-level
      ops (tombstone, compact, expire, dead-letter). Filesystem and HTTP
      DELETE surfaces intentionally omitted — HERALD's operation surface
      is stream-and-hub, not filesystem.

[A5]  EXTENDED — Cross-boundary vocabulary now includes fan-out language:
      "publish to downstream", "fan out to consumers", "broadcast",
      "fanout/fan-out".

[A6]  EXTENDED — Regulated-data class regex now includes MNPI (auction
      reserve prices and pre-auction signals are a live concern for
      Subastop-family ventures). FERPA/ITAR intentionally omitted.

[A7]  RESOLVED — Structured-envelope pattern KEPT. Envelope-emitting
      Worker subset is growing with the migration; removing the pattern
      now would require re-adding it in v2. Classification cost is trivial.

[A8]  EXTENDED — Volume nouns now cover both batch-ETL vocabulary (rows,
      records, entries, documents) AND hub-native vocabulary (events,
      messages, alerts, transactions). HERALD ingests from both stream
      and batch sources.

[A9]  RESOLVED — HERALD's routing metadata contains downstream_consumer_count
      but it is NOT propagated into the Worker task payload. v1 uses the
      heuristic decomposition below. v2 roadmap: extend TaskPayload shape
      to carry the canonical signal, then materiality switches to direct
      lookup with heuristic fallback. Not blocking v1.

[A10] CONFIRMED — Component weights 0.20/0.30/0.30/0.20.

[A11] UPDATED — _VOLUME_REFERENCE raised from 100k to 500k, centered on
      HERALD's empirical p90 per-task volume.

[A12] CONFIRMED — trigger_threshold = 0.35 (strict end of high-stakes
      band; data-hub errors propagate to every downstream consumer).

[A13] CONFIRMED — composition_strategy = "weighted_sum".

[A14] UPDATED — advisor_per_session_cap lowered from 10 to 4. HERALD
      sessions are predominantly batch (migration runs, backfills, topic
      cutovers); 10 would bind immediately. Named constant for easy
      future tuning. Interactive-operator workflows, if needed, belong
      in a sibling adapter (herald.interactive.v1) rather than bending
      this cap.

[A15] CONFIRMED — advisor_model = "claude-opus-4-7".

[A16] CONFIRMED — Advisor priority: reversibility > trust-boundary
      integrity > regulated-data handling > upstream source reliability.

[A17] CONFIRMED — trigger_budget_tokens = 512 (soft ceiling against
      future trigger-path instrumentation).
=============================================================================
"""

from __future__ import annotations

import re
from typing import Tuple

from sensei_trigger.types import (
    AdapterTriggerConfig,
    ContextReference,
    HardTaskPattern,
    KeywordMatcher,
    Materiality,
    RegexMatcher,
    StructuralMatcher,
    StructuralSchema,
    TaskPayload,
)

ADAPTER_ID = "herald.v1"
CONTRACT_VERSION = "0.2"


# ---------------------------------------------------------------------------
# Pattern registry
# ---------------------------------------------------------------------------

_REGISTRY: Tuple[HardTaskPattern, ...] = (

    # [A2] upstream-trust vocabulary
    HardTaskPattern(
        id="source_reliability_uncertain",
        description=(
            "Worker flagged upstream source as unreliable, unverified, or "
            "of unknown provenance. Every downstream consumer inherits the "
            "uncertainty, so this is load-bearing in a data-hub."
        ),
        matcher=KeywordMatcher(any_of=(
            "source reliability uncertain",
            "source reliability unknown",
            "upstream unreliable",
            "provenance unclear",
            "provenance unknown",
            "unverified source",
            "source not canonical",
        )),
        base_weight=0.80,
        materiality_boost=0.25,
    ),

    # [A3] anomaly / drift vocabulary
    HardTaskPattern(
        id="pipeline_anomaly",
        description=(
            "Ingestion or transformation anomaly: unexpected schema, "
            "field mismatch, null-rate spike, outlier distribution, "
            "or contract drift."
        ),
        matcher=KeywordMatcher(any_of=(
            "anomaly detected",
            "schema drift",
            "unexpected schema",
            "field mismatch",
            "contract violation",
            "null rate spike",
            "distribution shift",
            "outlier detected",
        )),
        base_weight=0.75,
        materiality_boost=0.20,
    ),

    # [A4] destructive operations — SQL base + stream-level ops
    HardTaskPattern(
        id="destructive_operation",
        description=(
            "Worker output references a destructive verb. Covers SQL-style "
            "(DROP, TRUNCATE, DELETE FROM, PURGE, OVERWRITE) and "
            "stream-level (TOMBSTONE, COMPACT, EXPIRE, DEAD-LETTER) "
            "operations. Irreversible operations are hard-tasks regardless "
            "of Worker confidence."
        ),
        matcher=RegexMatcher(patterns=(
            r"\b(?:drop|truncate|purge|overwrite)\b",
            r"\bdelete\s+from\b",
            r"\brm\s+-rf\b",
            r"\b(?:tombstone|compact|expire)\b",
            r"\bdead[-\s]?letter\b",
        )),
        base_weight=0.95,
        materiality_boost=0.40,
    ),

    # [A5] cross-trust-boundary + fan-out vocabulary
    HardTaskPattern(
        id="cross_boundary_write",
        description=(
            "Data moves outside the hub's trust boundary: external sink, "
            "third-party destination, cross-tenant write, egress, or "
            "fan-out to downstream consumers (broadcast, fanout, publish)."
        ),
        matcher=KeywordMatcher(any_of=(
            "external sink",
            "third-party destination",
            "cross-tenant",
            "egress to",
            "export to external",
            "push to vendor",
            "publish to downstream",
            "fan out to consumers",
            "broadcast",
            "fanout",
            "fan-out",
        )),
        base_weight=0.85,
        materiality_boost=0.35,
    ),

    # [A6] regulated-data class surface + MNPI
    HardTaskPattern(
        id="classified_data_touched",
        description=(
            "Worker output references PII, PCI, PHI, MNPI, or other "
            "regulated data classes. Stakes rise sharply: downstream "
            "harm includes legal exposure, not just data correctness."
        ),
        matcher=RegexMatcher(patterns=(
            r"\b(?:PII|PCI|PHI|MNPI|SSN|HIPAA|GDPR|CCPA)\b",
            r"\bcredit\s*card\s*(?:number|data)\b",
            r"\b(?:sensitive|regulated)\s+(?:data|field|column)\b",
        )),
        base_weight=0.90,
        materiality_boost=0.40,
    ),

    # [A7] low-confidence structured envelope (post-migration Worker subset)
    HardTaskPattern(
        id="low_confidence_structured_output",
        description=(
            "Worker emitted a structured envelope whose self-reported "
            "confidence is low and/or whose warnings list is non-empty. "
            "Only fires for post-Pusher-migration Workers that emit "
            "{operation, confidence, warnings, target} envelopes."
        ),
        matcher=StructuralMatcher(schema=StructuralSchema(
            required_fields=("confidence", "warnings"),
        )),
        base_weight=0.70,
        materiality_boost=0.15,
    ),

    # [A8] high-volume — batch-ETL nouns + hub-native nouns
    HardTaskPattern(
        id="high_volume_operation",
        description=(
            "Worker references operating on a large item count "
            "(six digits or more). Covers both batch-ETL vocabulary "
            "(rows, records, entries, documents) and hub-native "
            "vocabulary (events, messages, alerts, transactions). "
            "Volume alone is not hard-task, but combined with other "
            "signals it raises the cost of a wrong decision meaningfully."
        ),
        matcher=RegexMatcher(patterns=(
            r"\b\d{6,}\s*(?:rows?|records?|entries|documents?|"
            r"events?|messages?|alerts?|transactions?)\b",
        )),
        base_weight=0.55,
        materiality_boost=0.30,
    ),
)


# ---------------------------------------------------------------------------
# Materiality function
# ---------------------------------------------------------------------------

# [A8] record/hub volume nouns — reused by materiality extraction
_VOLUME_PATTERN = re.compile(
    r"(\d{1,12})\s*(?:rows?|records?|entries|documents?|"
    r"events?|messages?|alerts?|transactions?)",
    re.IGNORECASE,
)
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

# [A11] volume inflection point — HERALD empirical p90
_VOLUME_REFERENCE = 500_000

# [A10] component weights (sum = 1.0)
_W_VOLUME = 0.20
_W_DESTRUCTIVE = 0.30
_W_CLASSIFIED = 0.30
_W_CROSS_BOUNDARY = 0.20

# [A14] per-session advisor cap — batch-session-tuned
_ADVISOR_PER_SESSION_CAP = 4


def _compute_materiality(
    task: TaskPayload,
    ctx: ContextReference,
) -> Materiality:
    """Compute a stakes score in [0.0, 1.0] for a HERALD task.

    [A9] Heuristic decomposition. v2 switches to direct lookup against a
    canonical downstream_consumer_count signal once the TaskPayload shape
    is extended to carry it.

    Never calls an LLM. Never returns None. Returns 0.0 when no signal
    is present — zero is a valid answer.
    """
    text = task.worker_output or ""

    # Volume factor — take the largest numeric item-count mentioned
    volume_matches = _VOLUME_PATTERN.findall(text)
    volume_n = max((int(n) for n in volume_matches), default=0)
    volume_factor = min(1.0, volume_n / _VOLUME_REFERENCE) if volume_n else 0.0

    # Binary factors
    destructive_factor = 1.0 if _DESTRUCTIVE_PATTERN.search(text) else 0.0
    classified_factor = 1.0 if _CLASSIFIED_PATTERN.search(text) else 0.0
    cross_boundary_factor = 1.0 if _CROSS_BOUNDARY_PATTERN.search(text) else 0.0

    raw = (
        _W_VOLUME * volume_factor
        + _W_DESTRUCTIVE * destructive_factor
        + _W_CLASSIFIED * classified_factor
        + _W_CROSS_BOUNDARY * cross_boundary_factor
    )
    value = max(0.0, min(1.0, raw))

    return Materiality(
        value=value,
        components={
            "volume_factor": float(volume_factor),
            "destructive_factor": float(destructive_factor),
            "classified_factor": float(classified_factor),
            "cross_boundary_factor": float(cross_boundary_factor),
            "volume_n": float(volume_n),
            "output_length": float(len(text)),
        },
        computed_by=ADAPTER_ID,
    )


# ---------------------------------------------------------------------------
# Advisor prompt
# ---------------------------------------------------------------------------

# [A16] priority order in the advisor's reasoning frame
_ADVISOR_PROMPT_TEMPLATE = (
    "You are a senior data integrity specialist reviewing a task that "
    "HERALD's Worker model has escalated. HERALD is a data-hub: it ingests, "
    "transforms, and distributes data across many downstream consumers, "
    "so a wrong call here is hard to unwind.\n"
    "\n"
    "You will receive the Worker's output and the signals that fired. "
    "Decide whether the proposed operation is safe to proceed with, needs "
    "modification, or must be stopped. Prioritize: (1) reversibility, "
    "(2) trust-boundary integrity, (3) regulated-data handling, "
    "(4) upstream source reliability — in that order.\n"
    "\n"
    "End your response with exactly one verdict token on its own line — "
    "APPROVE, REJECT, or ESCALATE — followed by a reasoning paragraph. "
    "ESCALATE means 'a human must look at this before it ships.'"
)


# ---------------------------------------------------------------------------
# build_config — the single exported surface
# ---------------------------------------------------------------------------

def build_config() -> AdapterTriggerConfig:
    """Return the adapter trigger configuration for herald.v1.

    M3 calls this from sensei_api/registry.py at server startup.
    """
    return AdapterTriggerConfig(
        adapter_id=ADAPTER_ID,
        contract_version=CONTRACT_VERSION,
        hard_task_registry=_REGISTRY,
        compute_materiality=_compute_materiality,

        # [A12] strict end of high-stakes band
        trigger_threshold=0.35,

        # [A17] soft ceiling for future trigger-path instrumentation
        trigger_budget_tokens=512,

        # [A13] mixed workload → signal accumulation
        composition_strategy="weighted_sum",

        materiality_floor=0.0,

        # [A15]
        advisor_model="claude-opus-4-7",

        # [A14] batch-session-tuned
        advisor_per_session_cap=_ADVISOR_PER_SESSION_CAP,

        advisor_prompt_template=_ADVISOR_PROMPT_TEMPLATE,
    )

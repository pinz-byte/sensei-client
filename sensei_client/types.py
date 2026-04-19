"""
sensei_client types.

Venture-neutral wire types. Mirrors the v0.3 contract payload shapes
plus a GuardResult that composes SENSEI's decision and (optionally) an
Advisor verdict into a single object the caller can branch on.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, Tuple


Verdict = Literal["PROCEED", "APPROVE", "REJECT", "ESCALATE"]
"""
Final verdict surface for the caller:

- PROCEED  — SENSEI did not escalate; ship the Worker output.
- APPROVE  — Advisor reviewed and approved.
- REJECT   — Advisor reviewed and rejected; stop.
- ESCALATE — Advisor said a human must look at this. Also the
             fail-safe default when the Advisor response cannot be
             parsed.
"""


@dataclass(frozen=True)
class WorkerTask:
    """Generic Worker-output envelope passed to the guard.

    Field set mirrors TaskPayload in SENSEI contract v0.3 §3. Ventures
    MAY extend this via subclassing; the guard only reads the fields
    below.

    All four confidence_* fields are REQUIRED per contract v0.3 §3
    (TaskPayload schema, lines 120–123). If a Worker emits only a scalar
    confidence_score, the venture's wiring layer is responsible for
    computing, imputing, or explicitly defaulting the three decomposed
    fields before constructing WorkerTask. Semantic authority lives in
    the venture, not in the transport — the client does not silently
    default.
    """

    task_id: str
    worker_output: str
    worker_model: str
    session_id: str
    turn_index: int
    confidence_score: float
    confidence_coverage: float
    confidence_grounding: float
    confidence_novelty: float

    def to_decide_payload(self, materiality_value: Optional[float]) -> Dict[str, Any]:
        """Serialize to the POST /decide body shape."""
        body: Dict[str, Any] = {
            "task_id": self.task_id,
            "worker_output": self.worker_output,
            "worker_model": self.worker_model,
            "session_id": self.session_id,
            "turn_index": self.turn_index,
            "confidence_score": self.confidence_score,
            "confidence_coverage": self.confidence_coverage,
            "confidence_grounding": self.confidence_grounding,
            "confidence_novelty": self.confidence_novelty,
        }
        if materiality_value is not None:
            body["materiality_value"] = materiality_value
        return body


@dataclass(frozen=True)
class SenseiDecision:
    """Parsed /decide response.

    Mirrors v0.3 contract §4.1 response envelope:

        { "decision": { "escalate": bool, "confidence": float },
          "reasoning": { "signals_fired": [...], "composition_strategy": str,
                         "threshold_applied": float, "effective_score": float,
                         "override_applied": bool },
          "trigger_cost": { "tokens_spent": int, "wall_time_ms": int,
                            "budget_ceiling": int },
          "context_ref":  { "memory_version": str, "summary_hash": str,
                            "window_end_turn": int, "adapter_id": str },
          "advisor_prompt_mods": {...} | null }

    This dataclass flattens the above into the fields most callers branch
    on. The full reasoning block is preserved verbatim in decision_trace
    for observability and Advisor-prompt construction. trigger_cost,
    context_ref (minus adapter_id), and advisor_prompt_mods are stashed
    in `extra` so additions remain forward-compatible.
    """

    adapter_id: str
    escalate: bool
    trigger_score: float
    fired_patterns: Tuple[str, ...]
    composition_strategy: str
    decision_trace: Dict[str, Any]
    extra: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_response(cls, data: Dict[str, Any]) -> "SenseiDecision":
        """Decode the nested /decide response envelope per contract v0.3 §4.1.

        Tolerant of missing optional sub-blocks (server-side contract
        evolution, legacy proxies, etc.) but strict on the structural
        contract: `decision`, `reasoning`, and `context_ref` MUST be
        present as dicts.
        """
        decision_block = data.get("decision") or {}
        reasoning_block = data.get("reasoning") or {}
        context_ref = data.get("context_ref") or {}

        signals_fired = reasoning_block.get("signals_fired") or []
        fired_patterns = tuple(
            s.get("signal_name", "") for s in signals_fired if isinstance(s, dict)
        )

        extra = {
            k: v
            for k, v in data.items()
            if k not in ("decision", "reasoning", "context_ref")
        }
        # Preserve context_ref bits other than adapter_id for observability.
        context_ref_extra = {
            k: v for k, v in context_ref.items() if k != "adapter_id"
        }
        if context_ref_extra:
            extra["context_ref"] = context_ref_extra

        return cls(
            adapter_id=str(context_ref.get("adapter_id", "")),
            escalate=bool(decision_block.get("escalate", False)),
            trigger_score=float(reasoning_block.get("effective_score", 0.0)),
            fired_patterns=fired_patterns,
            composition_strategy=str(reasoning_block.get("composition_strategy", "")),
            decision_trace=dict(reasoning_block),
            extra=extra,
        )


@dataclass(frozen=True)
class AdvisorResult:
    """Advisor model's review output."""

    verdict: Literal["APPROVE", "REJECT", "ESCALATE"]
    reasoning_text: str
    model: str
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None


@dataclass(frozen=True)
class GuardResult:
    """Composed result of one check_and_escalate call.

    `verdict` is the single field most callers will branch on. The rest
    is for observability and audit — log it, don't parse it in hot paths.
    """

    verdict: Verdict
    sensei_reachable: bool
    decision: Optional[SenseiDecision]
    advisor: Optional[AdvisorResult]
    materiality_value: Optional[float]
    error: Optional[str] = None
    fail_open: bool = False

    @property
    def escalated(self) -> bool:
        return self.verdict != "PROCEED"

    @property
    def should_ship(self) -> bool:
        """True iff the Worker output is cleared to ship.

        PROCEED = SENSEI didn't flag it. APPROVE = Advisor approved.
        Every other verdict = hold.
        """
        return self.verdict in ("PROCEED", "APPROVE")

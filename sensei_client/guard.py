"""
The guard layer — single entry point a venture wraps around its
Worker outputs.

Flow:

    worker_output
        ↓
    materiality_fn(worker_output) → float  [venture-supplied]
        ↓
    POST /adapters/{adapter_id}/decide
        ↓
    ┌──────────── 404 ───────────┐
    │                            │
    re-register, retry once      │
        ↓                        │
    decision                     │
        ├── escalate=False → PROCEED
        └── escalate=True  → invoke_advisor() → {APPROVE, REJECT, ESCALATE}

SENSEI unreachable → fail-open (PROCEED with a warning flag).
Advisor invocation fails → fail-safe (ESCALATE).

The guard is sync. An async variant is a v1.1 candidate.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

from .advisor import invoke_advisor
from .client import SenseiClient
from .config import SenseiConfig
from .exceptions import (
    AdapterNotRegistered,
    SenseiBadRequest,
    SenseiConflict,
    SenseiError,
    SenseiUnreachable,
)
from .types import AdvisorResult, GuardResult, SenseiDecision, WorkerTask


logger = logging.getLogger("sensei_client.guard")


MaterialityFn = Callable[[str], float]
"""
Venture-supplied materiality function.

Takes the Worker output text. Returns a stakes score in [0.0, 1.0].
MUST be pure, offline, synchronous. MUST NOT call an LLM or any network
service. Per invariant I11, materiality is independent of confidence.
"""


def check_and_escalate(
    task: WorkerTask,
    materiality_fn: MaterialityFn,
    *,
    config: SenseiConfig,
    client: SenseiClient,
    advisor_client: Optional[Any] = None,
    focus_directives: Optional[List[str]] = None,
    retrieval_hints: Optional[List[str]] = None,
    additional_system_instructions: Optional[str] = None,
    fail_open_on_unreachable: bool = True,
    fail_safe_on_advisor_error: bool = True,
) -> GuardResult:
    """Wrap one Worker output with SENSEI + Advisor.

    Returns a GuardResult. Branch on `.verdict` or `.should_ship` for
    the happy path; read `.decision` / `.advisor` for audit data.

    Keyword-only parameters by design — the call site is always
    explicit about which policy knobs it's overriding.
    """
    # --- Step 1: materiality ------------------------------------------------
    try:
        materiality_value = float(materiality_fn(task.worker_output))
    except Exception as e:
        logger.exception(
            "materiality_fn raised — failing safe with ESCALATE verdict"
        )
        return GuardResult(
            verdict="ESCALATE",
            sensei_reachable=False,
            decision=None,
            advisor=None,
            materiality_value=None,
            error=f"materiality_fn raised: {e!r}",
            fail_open=False,
        )

    # Clamp — some ventures' first-cut materiality functions drift past
    # the bounds during tuning. Better to clamp than to have SENSEI
    # reject the payload mid-production.
    materiality_value = max(0.0, min(1.0, materiality_value))

    payload = task.to_decide_payload(materiality_value)

    # --- Step 2: /decide with 404 re-register + retry -----------------------
    try:
        raw_response = _decide_with_reregister(client, payload)
    except SenseiUnreachable as e:
        if fail_open_on_unreachable:
            logger.warning(
                "SENSEI unreachable (%s) — failing open, Worker output "
                "proceeds with audit flag",
                e,
            )
            return GuardResult(
                verdict="PROCEED",
                sensei_reachable=False,
                decision=None,
                advisor=None,
                materiality_value=materiality_value,
                error=str(e),
                fail_open=True,
            )
        raise
    except (SenseiBadRequest, SenseiConflict, SenseiError) as e:
        # Programmer errors — do not fail-open. Fail-safe: ESCALATE.
        logger.error("SENSEI returned a hard error: %s", e)
        return GuardResult(
            verdict="ESCALATE",
            sensei_reachable=True,
            decision=None,
            advisor=None,
            materiality_value=materiality_value,
            error=str(e),
            fail_open=False,
        )

    decision = SenseiDecision.from_response(raw_response)

    # --- Step 3: if SENSEI said proceed, ship -------------------------------
    if not decision.escalate:
        return GuardResult(
            verdict="PROCEED",
            sensei_reachable=True,
            decision=decision,
            advisor=None,
            materiality_value=materiality_value,
        )

    # --- Step 4: escalated — invoke Advisor --------------------------------
    effective_config = config
    if additional_system_instructions is not None:
        # Create a shallow override without mutating the shared config.
        effective_config = SenseiConfig(
            api_url=config.api_url,
            adapter_spec=config.adapter_spec,
            http_timeout_s=config.http_timeout_s,
            http_connect_timeout_s=config.http_connect_timeout_s,
            additional_system_instructions=additional_system_instructions,
        )

    try:
        advisor_result = invoke_advisor(
            effective_config,
            worker_output=task.worker_output,
            fired_patterns=decision.fired_patterns,
            decision_trace=decision.decision_trace,
            focus_directives=focus_directives,
            retrieval_hints=retrieval_hints,
            client=advisor_client,
        )
    except Exception as e:
        if fail_safe_on_advisor_error:
            logger.exception(
                "Advisor invocation failed — failing safe with ESCALATE"
            )
            return GuardResult(
                verdict="ESCALATE",
                sensei_reachable=True,
                decision=decision,
                advisor=None,
                materiality_value=materiality_value,
                error=f"advisor_error: {e!r}",
                fail_open=False,
            )
        raise

    return GuardResult(
        verdict=advisor_result.verdict,
        sensei_reachable=True,
        decision=decision,
        advisor=advisor_result,
        materiality_value=materiality_value,
    )


# ----------------------------------------------------------------------
# Internal
# ----------------------------------------------------------------------

def _decide_with_reregister(
    client: SenseiClient,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    """POST /decide with at most one re-register + retry on 404.

    Per v0.3 contract §4.3, a 404 from /decide means the adapter spec
    was lost (SENSEI restart, in-memory-only registry). The venture
    re-registers and retries once. If the second attempt also 404s, we
    surface AdapterNotRegistered — something is structurally wrong.
    """
    try:
        return client.decide(payload)
    except AdapterNotRegistered:
        logger.info(
            "adapter %r returned 404 — re-registering and retrying once",
            client.adapter_id,
        )
        client.register_from_config()
        return client.decide(payload)

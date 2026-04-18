"""
Advisor invocation.

When SENSEI escalates, the venture's guard calls the Advisor model
with the Worker output, the fired signals, and the spec's prompt
template. The Advisor returns free-form reasoning followed by one
verdict token on its own line: APPROVE | REJECT | ESCALATE.

Parser is fail-safe: if no token is found, default to ESCALATE. A
human looking at it is always a safer outcome than silently
auto-approving ambiguous Advisor output.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Literal, Optional, Tuple

from .config import SenseiConfig
from .types import AdvisorResult


AdvisorVerdict = Literal["APPROVE", "REJECT", "ESCALATE"]

# Match verdict tokens as standalone uppercase words. We take the LAST
# occurrence so mid-reasoning mentions ("we could REJECT this, but...")
# don't override the final line.
_VERDICT_RE = re.compile(r"\b(APPROVE|REJECT|ESCALATE)\b")


def parse_verdict(text: str) -> AdvisorVerdict:
    """Extract the final verdict token from Advisor output.

    Returns ESCALATE if no token is found — fail-safe default.
    """
    if not text:
        return "ESCALATE"
    matches = _VERDICT_RE.findall(text)
    if not matches:
        return "ESCALATE"
    last = matches[-1].upper()
    if last not in ("APPROVE", "REJECT", "ESCALATE"):
        return "ESCALATE"
    return last  # type: ignore[return-value]


def invoke_advisor(
    config: SenseiConfig,
    worker_output: str,
    *,
    fired_patterns: Tuple[str, ...] = (),
    decision_trace: Optional[Dict[str, Any]] = None,
    focus_directives: Optional[List[str]] = None,
    retrieval_hints: Optional[List[str]] = None,
    model_override: Optional[str] = None,
    max_tokens: int = 1024,
    client: Optional[Any] = None,
) -> AdvisorResult:
    """Call the Advisor model with a composed prompt.

    Lazy-imports anthropic so sensei_client remains importable in
    environments that only need the client-side types (tests, tooling).

    `client` parameter allows dependency injection — pass a mock or a
    shared anthropic.Anthropic instance. If omitted, a fresh one is
    constructed (requires ANTHROPIC_API_KEY in the environment).
    """
    try:
        import anthropic  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "invoke_advisor requires the 'anthropic' package. "
            "Install with: pip install anthropic"
        ) from e

    if client is None:
        client = anthropic.Anthropic()

    model = model_override or config.advisor_model
    system_prompt = _compose_system_prompt(
        config.advisor_prompt_template,
        config.additional_system_instructions,
    )
    user_message = _compose_user_message(
        worker_output=worker_output,
        fired_patterns=fired_patterns,
        decision_trace=decision_trace,
        focus_directives=focus_directives,
        retrieval_hints=retrieval_hints,
    )

    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    )

    # Extract text from the content blocks. Anthropic SDK returns a
    # list of TextBlock | ToolUseBlock | etc. — we only expect text.
    text_parts = []
    for block in response.content:
        block_type = getattr(block, "type", None)
        if block_type == "text":
            text_parts.append(getattr(block, "text", ""))
    reasoning_text = "\n".join(text_parts).strip()

    verdict = parse_verdict(reasoning_text)

    usage = getattr(response, "usage", None)
    input_tokens = getattr(usage, "input_tokens", None) if usage else None
    output_tokens = getattr(usage, "output_tokens", None) if usage else None

    return AdvisorResult(
        verdict=verdict,
        reasoning_text=reasoning_text,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


# ----------------------------------------------------------------------
# Prompt composition
# ----------------------------------------------------------------------

def _compose_system_prompt(
    base_template: str,
    additional_instructions: Optional[str],
) -> str:
    parts = [base_template.strip()]
    if additional_instructions:
        parts.append(additional_instructions.strip())
    return "\n\n".join(parts)


def _compose_user_message(
    *,
    worker_output: str,
    fired_patterns: Tuple[str, ...],
    decision_trace: Optional[Dict[str, Any]],
    focus_directives: Optional[List[str]],
    retrieval_hints: Optional[List[str]],
) -> str:
    sections = []

    sections.append("## Worker Output\n\n" + worker_output.strip())

    if fired_patterns:
        pattern_list = "\n".join(f"- {p}" for p in fired_patterns)
        sections.append("## Signals That Fired\n\n" + pattern_list)

    if decision_trace:
        trace_lines = [
            f"- {k}: {v}" for k, v in decision_trace.items()
        ]
        sections.append("## Decision Trace\n\n" + "\n".join(trace_lines))

    if focus_directives:
        focus = "\n".join(f"- {d}" for d in focus_directives)
        sections.append("## Focus On\n\n" + focus)

    if retrieval_hints:
        hints = "\n".join(f"- {h}" for h in retrieval_hints)
        sections.append("## Retrieval Hints\n\n" + hints)

    sections.append(
        "## Instructions\n\n"
        "Review the Worker output above. End your response with "
        "exactly one verdict token on its own line — APPROVE, REJECT, "
        "or ESCALATE — followed by a reasoning paragraph."
    )

    return "\n\n".join(sections)

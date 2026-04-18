"""
HERALD ↔ SENSEI wiring.

This is the whole of HERALD-specific glue. Three things happen here:

  1. `bootstrap_herald_sensei()` — called once at HERALD startup.
     Loads the adapter spec, builds the config, opens the HTTP client,
     registers the adapter. Returns `(config, client)` so HERALD can
     pass them into the guard call.

  2. `guard_herald_output(task)` — one-liner HERALD wraps around every
     Worker output. Hides the materiality_fn + config + client wiring.

  3. `DEFAULT_SPEC_PATH` — where HERALD's adapter spec lives by
     convention. Can be overridden via `$HERALD_SENSEI_SPEC_PATH`.

The pattern is deliberately minimal. Copy this file for any new
venture, rename, swap the three wired-in constants (spec path,
materiality function, adapter name).
"""

from __future__ import annotations

import os
import pathlib
from typing import List, Optional, Tuple

from sensei_client import (
    GuardResult,
    SenseiClient,
    SenseiConfig,
    WorkerTask,
    check_and_escalate,
)

from .materiality import compute_herald_materiality


# ---------------------------------------------------------------------
# Where HERALD's adapter spec lives.
# ---------------------------------------------------------------------

DEFAULT_SPEC_PATH = str(
    pathlib.Path(__file__).resolve().parent.parent
    / "sensei_adapters"
    / "herald"
    / "herald.v1.json"
)


# ---------------------------------------------------------------------
# Module-level handles populated by bootstrap.
# Kept as module globals so HERALD Worker call sites can import them
# without threading them through every function signature.
# ---------------------------------------------------------------------

_CONFIG: Optional[SenseiConfig] = None
_CLIENT: Optional[SenseiClient] = None


def bootstrap_herald_sensei(
    spec_path: Optional[str] = None,
    *,
    api_url: Optional[str] = None,
    register: bool = True,
) -> Tuple[SenseiConfig, SenseiClient]:
    """Initialize HERALD's SENSEI integration.

    Call once at HERALD startup. Idempotent: calling again replaces
    the module-level handles without leaking the old HTTP client.

    Args:
        spec_path: Path to herald.v1.json. Defaults to
            $HERALD_SENSEI_SPEC_PATH if set, otherwise the
            convention path adjacent to sensei_adapters/herald/.
        api_url: SENSEI base URL. Defaults to $SENSEI_API_URL,
            then to http://localhost:8000.
        register: Whether to POST /adapters at bootstrap. Set False
            in tests where you want to construct handles without
            touching the network.

    Returns:
        `(config, client)` — stored as module globals; also returned
        for callers that prefer explicit handle management.
    """
    global _CONFIG, _CLIENT

    resolved_spec = (
        spec_path
        or os.environ.get("HERALD_SENSEI_SPEC_PATH")
        or DEFAULT_SPEC_PATH
    )
    config_kwargs = {}
    if api_url is not None:
        config_kwargs["api_url"] = api_url

    if api_url is not None:
        config = SenseiConfig.from_spec_file(resolved_spec, api_url=api_url)
    else:
        config = SenseiConfig.from_env(spec_path=resolved_spec)

    client = SenseiClient(config)

    if register:
        client.register_from_config()

    # Clean up any prior handles before swapping in new ones.
    if _CLIENT is not None and _CLIENT is not client:
        try:
            _CLIENT.close()
        except Exception:
            pass

    _CONFIG = config
    _CLIENT = client
    return config, client


def guard_herald_output(
    task: WorkerTask,
    *,
    focus_directives: Optional[List[str]] = None,
    retrieval_hints: Optional[List[str]] = None,
    additional_system_instructions: Optional[str] = None,
) -> GuardResult:
    """Wrap one HERALD Worker output with SENSEI + Advisor.

    Requires `bootstrap_herald_sensei()` to have been called. Raises
    RuntimeError otherwise — fail-fast on misconfiguration rather
    than silently degrading.
    """
    if _CONFIG is None or _CLIENT is None:
        raise RuntimeError(
            "herald_sensei is not bootstrapped. Call "
            "bootstrap_herald_sensei() at HERALD startup."
        )

    return check_and_escalate(
        task=task,
        materiality_fn=compute_herald_materiality,
        config=_CONFIG,
        client=_CLIENT,
        focus_directives=focus_directives,
        retrieval_hints=retrieval_hints,
        additional_system_instructions=additional_system_instructions,
    )

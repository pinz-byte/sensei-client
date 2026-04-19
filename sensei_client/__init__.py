"""
sensei_client — venture-agnostic Python client for the SENSEI service.

Usage (summary — see INTEGRATION.md for the full walk-through):

    from sensei_client import (
        SenseiConfig,
        SenseiClient,
        WorkerTask,
        check_and_escalate,
    )

    config = SenseiConfig.from_env(spec_path="path/to/adapter.v1.json")
    client = SenseiClient(config)
    client.register_from_config()  # at venture startup

    def my_materiality(worker_output: str) -> float:
        ...

    result = check_and_escalate(
        task=WorkerTask(...),
        materiality_fn=my_materiality,
        config=config,
        client=client,
    )

    if result.should_ship:
        ...
    else:
        handle(result.verdict)  # REJECT or ESCALATE

Conforms to SENSEI Contract v0.3. Every venture in M1 that uses SENSEI
imports this package — HERALD, CarMatch, AVT Extractor, Subastop, and
whatever we build next.
"""

from __future__ import annotations

from .advisor import AdvisorVerdict, invoke_advisor, parse_verdict
from .client import SenseiClient
from .config import (
    DEFAULT_API_URL,
    DEFAULT_HTTP_CONNECT_TIMEOUT_S,
    DEFAULT_HTTP_TIMEOUT_S,
    SenseiConfig,
)
from .exceptions import (
    AdapterNotRegistered,
    SenseiBadRequest,
    SenseiConflict,
    SenseiError,
    SenseiUnreachable,
)
from .guard import MaterialityFn, check_and_escalate
from .types import (
    AdvisorResult,
    GuardResult,
    SenseiDecision,
    Verdict,
    WorkerTask,
)

__version__ = "0.3.4"

__all__ = [
    "__version__",
    # Config
    "SenseiConfig",
    "DEFAULT_API_URL",
    "DEFAULT_HTTP_TIMEOUT_S",
    "DEFAULT_HTTP_CONNECT_TIMEOUT_S",
    # Client
    "SenseiClient",
    # Guard
    "check_and_escalate",
    "MaterialityFn",
    # Advisor
    "invoke_advisor",
    "parse_verdict",
    "AdvisorVerdict",
    # Types
    "WorkerTask",
    "SenseiDecision",
    "AdvisorResult",
    "GuardResult",
    "Verdict",
    # Exceptions
    "SenseiError",
    "SenseiUnreachable",
    "AdapterNotRegistered",
    "SenseiBadRequest",
    "SenseiConflict",
]

"""
herald_sensei — HERALD's adoption of sensei_client.

This package is the worked example the M1 integration guide points at.
Any new venture (CarMatch, AVT Extractor, Subastop, …) can copy this
directory, rename `herald_` → `your_venture_`, swap the materiality
function, point `_SPEC_PATH` at its own adapter JSON, and be running in
under an hour.

Public API:

    from herald_sensei import guard_herald_output, bootstrap_herald_sensei

    CONFIG, CLIENT = bootstrap_herald_sensei()  # at HERALD startup

    result = guard_herald_output(worker_task)
    if result.should_ship:
        ...

Everything else is in sensei_client. This module is ~50 lines of glue.
"""

from __future__ import annotations

from .wiring import (
    DEFAULT_SPEC_PATH,
    bootstrap_herald_sensei,
    guard_herald_output,
)

__all__ = [
    "bootstrap_herald_sensei",
    "guard_herald_output",
    "DEFAULT_SPEC_PATH",
]

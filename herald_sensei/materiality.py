"""
HERALD materiality function — the version that lives inside HERALD's
runtime and is passed into `check_and_escalate` as the `materiality_fn`.

This is a thin wrapper around the reference implementation in
`sensei_adapters/herald/herald_materiality.py`. The reference version
returns a `MaterialityBreakdown` (a dataclass) so HERALD can log the
decomposition for its own observability. This module exposes the
`(str) -> float` callable shape that `sensei_client` expects.

Keep the weights and regex patterns in ONE place. When HERALD tunes
materiality, edit the reference file; this wrapper pulls from it.
"""

from __future__ import annotations

# Import the reference implementation by path. Once herald_sensei is
# pip-installable alongside sensei_adapters.herald, replace with a
# normal import: `from sensei_adapters.herald.herald_materiality import ...`
import importlib.util
import pathlib
import sys

_MODULE_NAME = "_herald_materiality_ref"
_ADAPTER_MATERIALITY_PATH = (
    pathlib.Path(__file__).resolve().parent.parent
    / "sensei_adapters"
    / "herald"
    / "herald_materiality.py"
)

_spec = importlib.util.spec_from_file_location(
    _MODULE_NAME, _ADAPTER_MATERIALITY_PATH
)
if _spec is None or _spec.loader is None:
    raise ImportError(
        f"Could not load HERALD materiality reference from "
        f"{_ADAPTER_MATERIALITY_PATH}"
    )
_mod = importlib.util.module_from_spec(_spec)
# Register the module before exec so @dataclass can resolve
# its home module via sys.modules[cls.__module__].
sys.modules[_MODULE_NAME] = _mod
_spec.loader.exec_module(_mod)

compute_herald_materiality_breakdown = _mod.compute_herald_materiality


def compute_herald_materiality(worker_output: str) -> float:
    """Materiality function in the shape `sensei_client` expects.

    Delegates to the reference implementation and returns only the
    final float. HERALD code that wants the decomposition for its own
    audit log should call `compute_herald_materiality_breakdown`
    directly.
    """
    breakdown = compute_herald_materiality_breakdown(worker_output)
    return float(breakdown.value)

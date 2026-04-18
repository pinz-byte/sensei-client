"""
sensei_client exceptions.

Hierarchy is load-bearing — callers catch SenseiUnreachable to fail-open
(network or 5xx — SENSEI is down, let the Worker output proceed with a
warning), catch AdapterNotRegistered to re-register and retry, and catch
SenseiError as the base to handle everything else as a hard error.
"""

from __future__ import annotations


class SenseiError(Exception):
    """Base for all sensei_client errors."""


class SenseiUnreachable(SenseiError):
    """SENSEI service is not reachable or returned 5xx.

    Callers SHOULD fail-open on this — let the Worker output proceed
    with an audit warning. SENSEI being down MUST NOT block the pipeline.
    """


class AdapterNotRegistered(SenseiError):
    """POST /adapters/{adapter_id}/decide returned 404.

    Per v0.3 contract, the venture MUST re-register and retry once.
    The guard layer handles this automatically; surface only when
    re-registration also fails.
    """


class SenseiBadRequest(SenseiError):
    """POST /decide returned 400 — payload failed validation.

    Most common cause under v0.3 JSON-registration: materiality_value
    missing from a /decide call for a JSON-registered adapter.
    Programmer error, not an operational condition. Do not retry.
    """


class SenseiConflict(SenseiError):
    """POST /adapters returned 409 — adapter_id collides with a
    Python-plugin-registered adapter.

    Per v0.3 contract, plugin-path IDs win. Rename the JSON adapter
    (e.g., herald.v1 → herald.v1-dynamic) or unregister the plugin.
    """

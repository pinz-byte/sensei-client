"""
sensei_client HTTP client.

Thin wrapper around httpx. Responsibilities:

  - Register the adapter (POST /adapters).
  - Dispatch /decide calls (POST /adapters/{id}/decide).
  - Translate HTTP status codes into the typed exception hierarchy
    (AdapterNotRegistered for 404, SenseiBadRequest for 400, etc.).
  - Manage an httpx.Client lifecycle via context manager.

No retry logic here — retry policy is the guard's responsibility.
Keep this layer mechanical.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import httpx

from .config import SenseiConfig
from .exceptions import (
    AdapterNotRegistered,
    SenseiBadRequest,
    SenseiConflict,
    SenseiError,
    SenseiUnreachable,
)


class SenseiClient:
    """HTTP client for the SENSEI service.

    Usage:

        with SenseiClient(config) as client:
            client.register_from_config()
            response = client.decide(payload)

    Or manually:

        client = SenseiClient(config)
        try:
            ...
        finally:
            client.close()
    """

    def __init__(
        self,
        config: SenseiConfig,
        *,
        http_client: Optional[httpx.Client] = None,
    ) -> None:
        self._config = config
        self._owns_client = http_client is None
        if http_client is None:
            timeout = httpx.Timeout(
                config.http_timeout_s,
                connect=config.http_connect_timeout_s,
            )
            http_client = httpx.Client(
                base_url=config.api_url.rstrip("/"),
                timeout=timeout,
            )
        self._http = http_client

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> "SenseiClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_client:
            self._http.close()

    # ------------------------------------------------------------------
    # API methods
    # ------------------------------------------------------------------

    @property
    def config(self) -> SenseiConfig:
        return self._config

    @property
    def adapter_id(self) -> str:
        return self._config.adapter_id

    def register_from_config(self) -> Dict[str, Any]:
        """Register the adapter carried by `self.config`.

        Idempotent per v0.3 contract §4.1: re-registering the same
        adapter_id replaces the spec without losing the event log.
        """
        return self.register_adapter(self._config.adapter_spec)

    def register_adapter(self, spec: Dict[str, Any]) -> Dict[str, Any]:
        """POST /adapters with an explicit spec payload."""
        try:
            resp = self._http.post("/adapters", json=spec)
        except httpx.RequestError as e:
            raise SenseiUnreachable(
                f"POST /adapters failed: {e!r}"
            ) from e

        if resp.status_code == 409:
            raise SenseiConflict(
                f"adapter_id collides with a Python-plugin adapter: "
                f"{resp.text}"
            )
        if resp.status_code == 400:
            raise SenseiBadRequest(
                f"POST /adapters rejected spec: {resp.text}"
            )
        if 500 <= resp.status_code < 600:
            raise SenseiUnreachable(
                f"POST /adapters returned {resp.status_code}: {resp.text}"
            )
        if resp.status_code not in (200, 201):
            raise SenseiError(
                f"POST /adapters returned unexpected status "
                f"{resp.status_code}: {resp.text}"
            )

        return resp.json()

    def decide(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """POST /adapters/{adapter_id}/decide.

        Maps 404 → AdapterNotRegistered so the guard layer can
        re-register and retry. Maps 5xx → SenseiUnreachable so callers
        can fail-open. Maps 400 → SenseiBadRequest (programmer error).
        """
        path = f"/adapters/{self._config.adapter_id}/decide"
        try:
            resp = self._http.post(path, json=payload)
        except httpx.RequestError as e:
            raise SenseiUnreachable(
                f"POST {path} failed: {e!r}"
            ) from e

        if resp.status_code == 404:
            raise AdapterNotRegistered(
                f"adapter {self._config.adapter_id!r} not registered "
                f"on SENSEI (404). Re-register and retry."
            )
        if resp.status_code == 400:
            raise SenseiBadRequest(
                f"POST {path} rejected payload: {resp.text}"
            )
        if 500 <= resp.status_code < 600:
            raise SenseiUnreachable(
                f"POST {path} returned {resp.status_code}: {resp.text}"
            )
        if resp.status_code != 200:
            raise SenseiError(
                f"POST {path} returned unexpected status "
                f"{resp.status_code}: {resp.text}"
            )

        return resp.json()

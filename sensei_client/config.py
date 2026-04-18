"""
sensei_client configuration.

SenseiConfig is the handoff surface between a venture's codebase and
the SENSEI client. It carries three things:

  1. Where SENSEI lives (api_url).
  2. Which adapter this venture registers (adapter_spec, loaded from JSON).
  3. Transport knobs (timeouts, retries).

The adapter spec is loaded eagerly at construction so configuration
errors fail fast at startup rather than on the first /decide call.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional


DEFAULT_API_URL = "http://localhost:8000"
DEFAULT_HTTP_TIMEOUT_S = 5.0
DEFAULT_HTTP_CONNECT_TIMEOUT_S = 2.0

# Required fields for an AdapterSpec per SENSEI contract v0.3 §2.1.
_REQUIRED_SPEC_FIELDS = (
    "adapter_id",
    "hard_task_registry",
    "trigger_threshold",
    "composition_strategy",
    "advisor_model",
    "advisor_prompt_template",
)


@dataclass(frozen=True)
class SenseiConfig:
    """Immutable runtime configuration for a sensei_client instance.

    Use one of the constructors:
      - `from_env(spec_path=...)` — picks api_url from $SENSEI_API_URL,
        loads spec JSON from a path.
      - `from_spec_file(api_url=..., spec_path=...)` — explicit args.
      - `from_spec_dict(api_url=..., adapter_spec=...)` — for in-memory
        specs (tests, generated specs, multi-adapter harnesses).
    """

    api_url: str
    adapter_spec: Dict[str, Any]
    http_timeout_s: float = DEFAULT_HTTP_TIMEOUT_S
    http_connect_timeout_s: float = DEFAULT_HTTP_CONNECT_TIMEOUT_S
    additional_system_instructions: Optional[str] = None
    # Populated automatically in __post_init__.
    adapter_id: str = field(init=False)

    def __post_init__(self) -> None:
        _validate_spec(self.adapter_spec)
        # frozen=True blocks normal assignment; use object.__setattr__.
        object.__setattr__(self, "adapter_id", self.adapter_spec["adapter_id"])

    # ------------------------------------------------------------------
    # Constructors
    # ------------------------------------------------------------------

    @classmethod
    def from_env(
        cls,
        spec_path: Optional[str] = None,
        *,
        api_url_env: str = "SENSEI_API_URL",
        spec_path_env: str = "SENSEI_ADAPTER_SPEC",
        **overrides: Any,
    ) -> "SenseiConfig":
        api_url = os.environ.get(api_url_env, DEFAULT_API_URL)
        resolved_spec_path = spec_path or os.environ.get(spec_path_env)
        if not resolved_spec_path:
            raise ValueError(
                f"SenseiConfig.from_env: spec path not provided and "
                f"${spec_path_env} is not set"
            )
        spec = _load_spec_file(resolved_spec_path)
        return cls(api_url=api_url, adapter_spec=spec, **overrides)

    @classmethod
    def from_spec_file(
        cls,
        spec_path: str,
        *,
        api_url: str = DEFAULT_API_URL,
        **overrides: Any,
    ) -> "SenseiConfig":
        spec = _load_spec_file(spec_path)
        return cls(api_url=api_url, adapter_spec=spec, **overrides)

    @classmethod
    def from_spec_dict(
        cls,
        adapter_spec: Dict[str, Any],
        *,
        api_url: str = DEFAULT_API_URL,
        **overrides: Any,
    ) -> "SenseiConfig":
        return cls(api_url=api_url, adapter_spec=adapter_spec, **overrides)

    # ------------------------------------------------------------------
    # Convenience accessors (read-through into adapter_spec)
    # ------------------------------------------------------------------

    @property
    def advisor_model(self) -> str:
        return str(self.adapter_spec["advisor_model"])

    @property
    def advisor_prompt_template(self) -> str:
        return str(self.adapter_spec["advisor_prompt_template"])

    @property
    def advisor_per_session_cap(self) -> Optional[int]:
        val = self.adapter_spec.get("advisor_per_session_cap")
        return int(val) if val is not None else None

    @property
    def contract_version(self) -> Optional[str]:
        return self.adapter_spec.get("contract_version")


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def _load_spec_file(spec_path: str) -> Dict[str, Any]:
    path = Path(spec_path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"Adapter spec file not found: {path}")
    with path.open("r", encoding="utf-8") as fh:
        try:
            return json.load(fh)
        except json.JSONDecodeError as e:
            raise ValueError(
                f"Adapter spec at {path} is not valid JSON: {e}"
            ) from e


def _validate_spec(spec: Dict[str, Any]) -> None:
    missing = [f for f in _REQUIRED_SPEC_FIELDS if f not in spec]
    if missing:
        raise ValueError(
            f"Adapter spec is missing required fields: {missing}. "
            f"See SENSEI contract v0.3 §2.1 for the AdapterSpec schema."
        )
    if not isinstance(spec["hard_task_registry"], list):
        raise ValueError("hard_task_registry MUST be a list")
    if not 0.0 <= float(spec["trigger_threshold"]) <= 1.0:
        raise ValueError("trigger_threshold MUST be in [0.0, 1.0]")

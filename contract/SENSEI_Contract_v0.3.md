# SENSEI Contract v0.3

**Status:** Draft — pending ratification
**Supersedes:** v0.2
**Effective:** On merge into `main`; applies to all adapters registered from the effective commit forward. Adapters registered under v0.2 migrate per §8.

**Summary of changes from v0.2:**

1. `AdapterTriggerConfig` is split into two distinct types: `AdapterSpec` (pure data, JSON-serializable) and `AdapterPlugin` (extends `AdapterSpec`, adds a Python `Callable` for materiality).
2. A new JSON registration path (`POST /adapters`) is added. Adapters registered through this path are agnostic; they require no Python code on the SENSEI server and no coordination with SENSEI maintainers.
3. `materiality_value` is added as a caller-asserted field on the `/decide` request for adapters registered via the JSON path.
4. Registration lifecycle is formally specified. Adapters are in-memory only; ventures own the registration lifecycle.
5. Three new invariants (I12, I13, I14) are defined, covering type separation, agnosticism, and registration authority.

Key words "MUST", "MUST NOT", "SHOULD", "SHOULD NOT", and "MAY" in this document are to be interpreted as described in RFC 2119.

---

## 1. Terminology

**Adapter** — a venture-specific configuration bundle describing how SENSEI should identify and respond to hard tasks in that venture's domain. Every adapter is uniquely identified by an `adapter_id`.

**Venture** — any system that calls SENSEI. Ventures are opaque to SENSEI except through their registered adapters.

**Worker** — the venture's primary model. Produces outputs SENSEI evaluates.

**Advisor** — a second model invoked when SENSEI decides to escalate a Worker task.

**Session** — a logical grouping of Worker tasks, used for per-session budgeting and rate-limiting.

**Adapter spec** — the serializable, JSON-representable configuration of an adapter. See §2.3.

**Adapter plugin** — a Python-installed extension of an adapter spec that adds a server-side materiality callable. See §2.4.

---

## 2. Types

### 2.1 Matchers

Three matcher kinds identify hard-task patterns in Worker output. All matchers are pure data and JSON-serializable.

```
KeywordMatcher {
  type: "keyword"         # discriminator
  any_of: string[]        # match if any substring is present
}

RegexMatcher {
  type: "regex"
  patterns: string[]      # match if any pattern matches
}

StructuralSchema {
  required_fields: string[]
}

StructuralMatcher {
  type: "structural"
  schema: StructuralSchema  # match if Worker output is a JSON object containing all required_fields
}
```

The `type` field is a discriminator. Implementations MUST reject any matcher JSON without a recognized `type`.

### 2.2 HardTaskPattern

```
HardTaskPattern {
  id: string                              # pattern identifier, unique within an adapter
  description: string                     # human-readable rationale
  matcher: KeywordMatcher | RegexMatcher | StructuralMatcher
  base_weight: float                      # [0.0, 1.0]
  materiality_boost: float                # extra weight added when materiality is high
}
```

### 2.3 AdapterSpec

The pure-data adapter representation. Fully JSON-serializable. Contains **no** `Callable` fields.

```
AdapterSpec {
  adapter_id: string
  contract_version: "0.3"
  hard_task_registry: HardTaskPattern[]
  trigger_threshold: float                # [0.0, 1.0]
  trigger_budget_tokens: int
  composition_strategy: "weighted_sum" | "max_signal" | "threshold_gated"
  materiality_floor: float                # [0.0, 1.0]
  advisor_model: string
  advisor_per_session_cap: int
  advisor_prompt_template: string
}
```

Implementations MUST reject any attempt to construct an `AdapterSpec` with a `compute_materiality` field or any field containing a callable value.

### 2.4 AdapterPlugin

An `AdapterSpec` extended with a server-side materiality callable. `AdapterPlugin` instances can only be registered through the Python plugin path (§3.2). They cannot be transmitted over HTTP.

```
AdapterPlugin extends AdapterSpec {
  compute_materiality: Callable[[TaskPayload, ContextReference], Materiality]
}
```

The `compute_materiality` field is the sole structural difference between `AdapterSpec` and `AdapterPlugin`. It exists only in `AdapterPlugin`.

### 2.5 TaskPayload

```
TaskPayload {
  task_id: string
  worker_output: string
  worker_model: string
  session_id: string
  turn_index: int
  confidence_score: float               # [0.0, 1.0]
  confidence_coverage: float            # [0.0, 1.0]
  confidence_grounding: float           # [0.0, 1.0]
  confidence_novelty: float             # [0.0, 1.0]
  summary_hash?: string                 # default "api-supplied"
  window_end_turn?: int                 # default 1
  uncertainty_markers?: string[]        # default []
  materiality_value?: float             # [0.0, 1.0]; see §4.2
  override_direction?: "force_escalate" | "force_worker"
  override_source?: "user" | "upstream_system" | "test_fixture"
  override_reason?: string
}
```

`materiality_value` is new in v0.3. Its semantics are defined in §4.2.

### 2.6 Materiality

```
Materiality {
  value: float                          # [0.0, 1.0]; MUST NOT be None
  components: Record<string, float>
  computed_by: string                   # MUST equal the adapter_id that produced it
}
```

`Materiality` is the return type of `compute_materiality` in the plugin path. In the JSON path, only `value` crosses the wire, supplied as `TaskPayload.materiality_value`.

### 2.7 Decision Types

Unchanged from v0.2:

```
Decision { escalate: bool; confidence: float }

Reasoning {
  signals_fired: SignalFired[]
  composition_strategy: string
  threshold_applied: float
  effective_score: float
  override_applied: bool
}

TriggerCost { tokens_spent: int; wall_time_ms: int; budget_ceiling: int }

ContextReference {
  memory_version: string
  summary_hash: string
  window_end_turn: int
  adapter_id: string
}

AdvisorPromptMods {
  additional_system_instructions: string[]
  focus_directives: string[]
  retrieval_hints: string[]
}
```

---

## 3. Registration

### 3.1 JSON Registration Path

`POST /adapters` accepts an `AdapterSpec` JSON payload.

**Request body:** an `AdapterSpec` (§2.3).

**Response:** `200 OK`
```
{
  "adapter_id": string,
  "status": "registered" | "replaced",
  "registered_at": string       # ISO 8601 UTC with trailing Z
}
```

`status` MUST be `"registered"` on first registration of an `adapter_id` and `"replaced"` when an existing adapter with the same `adapter_id` is overwritten.

**Errors:**
- `400 Bad Request` — malformed spec, invalid field values, or unrecognized matcher `type`.
- `409 Conflict` — the `adapter_id` is already registered via the Python plugin path (§3.2). JSON registrations MUST NOT overwrite plugin registrations.

### 3.2 Python Plugin Registration Path

Adapters may be registered as `AdapterPlugin` instances at server startup via `sensei_api/registry.py`. This is the only path that accepts `Callable` fields and is the only path through which server-side materiality logic may be introduced.

Plugin registrations are static for the lifetime of the server process. Modifying them requires a server restart.

### 3.3 ID Collision and Precedence

When resolving an `adapter_id`, the server MUST look up in this order:

1. Python plugin registry.
2. JSON dynamic registry.
3. Otherwise, `404`.

An `adapter_id` registered in the plugin registry cannot be overwritten via the JSON path. A JSON attempt against a plugin-held `adapter_id` MUST return `409`.

### 3.4 Re-registration

A second `POST /adapters` with the same `adapter_id` replaces the prior spec. The server MUST:

1. Close the previous adapter's EventLogger cleanly before opening the new one.
2. Preserve the existing event log directory and append new events to it.
3. Preserve calibration history.
4. Return `200 OK` with `status: "replaced"`.

Spec drift between registrations is accepted silently. Ventures wanting a clean event stream MUST choose a new `adapter_id` (e.g., by incrementing a version suffix).

### 3.5 Lifecycle

Adapter specs registered via the JSON path are held in-memory only. They are lost when the SENSEI server restarts.

Event logs are disk-backed under `SENSEI_LOG_DIR` and survive restart. When a previously-registered `adapter_id` re-registers after a restart, the server MUST reconnect the new registration to the existing event log directory.

Ventures MUST register their adapter via `POST /adapters` on their own startup. A `404` response from `/decide` is a signal to re-register and retry, not an error state.

SENSEI makes no guarantee about adapter-spec persistence across server restarts.

---

## 4. Decision Endpoint

### 4.1 `POST /adapters/{adapter_id}/decide`

**Request body:** a `TaskPayload` (§2.5).

**Response:** `200 OK`
```
{
  "decision": Decision,
  "reasoning": Reasoning,
  "trigger_cost": TriggerCost,
  "context_ref": ContextReference,
  "advisor_prompt_mods": AdvisorPromptMods | null
}
```

`advisor_prompt_mods` MUST be `null` when `decision.escalate` is `false`.

### 4.2 Materiality Dispatch

The server MUST dispatch on the concrete type of the registered adapter:

**If the adapter is an `AdapterPlugin`:**
- The server MUST invoke `compute_materiality(task, ctx)` and use the returned `Materiality.value` in the decision.
- Any `materiality_value` supplied in the request body SHOULD be ignored. The server MAY log a warning but MUST NOT return an error.
- Rationale: server-side materiality is authoritative; caller-supplied values MUST NOT override it.

**If the adapter is an `AdapterSpec`:**
- The server MUST read `materiality_value` from the request body.
- If `materiality_value` is absent, the server MUST return `400 Bad Request` with an error body indicating the field is required for this adapter.
- If `materiality_value` is present but outside `[0.0, 1.0]`, the server MUST return `400`.

### 4.3 Error Cases

- `404 Not Found` — no adapter registered for the given `adapter_id`.
- `400 Bad Request` — missing or out-of-range `materiality_value` for an `AdapterSpec` adapter; invalid request payload.
- `422 Unprocessable Entity` — invalid override fields (`override_direction` without `override_source` or `override_reason`, or invalid enum values).

---

## 5. Observability

### 5.1 `GET /adapters`

Returns a list of all registered adapters across both registration paths. Each entry MUST include `adapter_id`, the registration path (`"plugin"` or `"json"`), and the tuning parameters. Plugin entries MUST NOT expose the `compute_materiality` callable.

### 5.2 `GET /adapters/{adapter_id}/events`

Query parameters: `start`, `end` (ISO 8601 UTC). Returns logged escalation events in the window.

### 5.3 `GET /adapters/{adapter_id}/rollup`

Same query parameters as `/events`. Returns aggregate statistics.

### 5.4 `POST /adapters/{adapter_id}/calibrate`

Request body: `{ start, end, prior_threshold, target_band_low?, target_band_high?, max_step? }`.

Returns a proposed `trigger_threshold` adjustment, or `null` if the observed escalation rate is already within the target band. The server MUST NOT apply the adjustment automatically; callers apply it by re-registering the adapter with an updated spec.

---

## 6. Invariants

### I4 — Loud Failure

`compute_materiality` MUST NOT return `None`. Implementations MUST raise an exception rather than silently returning a null result.

### I11 — Signal Independence

Materiality scores operational stakes, not Worker output quality. An implementation that correlates materiality with Worker correctness violates this invariant.

### NRC — No Recursive Cost

`compute_materiality` MUST NOT invoke an LLM, directly or transitively. Adapter modules MUST NOT `import anthropic` or any equivalent SDK.

### I12 — Type Separation (new in v0.3)

`AdapterSpec` MUST NOT contain any `Callable` field or any field whose value is a function, method, or closure. The presence of `compute_materiality` is the sole structural distinction between `AdapterSpec` and `AdapterPlugin`.

### I13 — Agnosticism (new in v0.3)

The core SENSEI surface — `sensei_api`, `sensei_trigger`, `sensei_observability`, and this contract — MUST NOT reference any specific `adapter_id`, venture name, or domain-specific vocabulary outside of registration fixtures and illustrative examples explicitly marked as such. Enforcement via CI grep check is RECOMMENDED.

### I14 — Registration Authority (new in v0.3)

Ventures own the registration lifecycle of their adapters. SENSEI provides the registration surface but does not persist adapter specs across server restarts. Ventures MUST register on their own startup and MUST treat `404` on `/decide` as a signal to re-register and retry.

---

## 7. Trust Boundary

For adapters registered via the JSON path, `materiality_value` is caller-asserted. SENSEI does not verify stakes.

This is acceptable for:
- Internal trusted-network deployments.
- Single-tenant deployments.

This is not acceptable without additional layers for:
- External or Internet-facing deployments.
- Multi-tenant deployments.

Operators deploying SENSEI outside the acceptable profiles MUST add a server-side materiality verification layer before accepting caller-asserted values. The shape of such a layer is out of scope for v0.3.

---

## 8. Migration from v0.2

### 8.1 AdapterTriggerConfig

`AdapterTriggerConfig` MAY be retained as an alias for `AdapterPlugin` during a transitional period. Callers constructing `AdapterTriggerConfig` will continue to work without modification, provided they supply `compute_materiality`.

The alias SHOULD be marked deprecated and removed no later than v0.4.

### 8.2 `_no_op_materiality`

The `_no_op_materiality` sentinel used in v0.2 as a placeholder for JSON-registered adapters MUST be removed. Its presence in the codebase is incompatible with I12.

Migration steps:
1. Replace all JSON-registration code paths to construct `AdapterSpec` directly.
2. Remove the `_no_op_materiality` definition.
3. Remove references from fixtures and tests.

### 8.3 Existing Python Adapters

Existing v0.2 adapters are re-typed as `AdapterPlugin` with no functional change. The Python import path (`sensei_adapters/<venture>/adapter.py`) and the `build_config()` entry point are unchanged.

---

## 9. Reserved / Out of Scope

The following are explicitly not covered by v0.3 and are deferred to future versions:

- Authentication and authorization.
- Disk persistence of JSON-registered adapter specs.
- Field-type validation in `StructuralMatcher` beyond `required_fields`.
- Adapter-internal semantic versioning (`adapter_id` is the version).
- Server-side materiality verification for external deployments (see §7).

---

## Appendix A — Minimal AdapterSpec (JSON)

```json
{
  "adapter_id": "example.v1",
  "contract_version": "0.3",
  "hard_task_registry": [
    {
      "id": "example_pattern",
      "description": "Fires when Worker output mentions a signal phrase.",
      "matcher": {
        "type": "keyword",
        "any_of": ["signal phrase"]
      },
      "base_weight": 0.80,
      "materiality_boost": 0.20
    }
  ],
  "trigger_threshold": 0.40,
  "trigger_budget_tokens": 512,
  "composition_strategy": "weighted_sum",
  "materiality_floor": 0.0,
  "advisor_model": "claude-opus-4-7",
  "advisor_per_session_cap": 10,
  "advisor_prompt_template": "You are a senior advisor. End with APPROVE | REJECT | ESCALATE."
}
```

## Appendix B — Matcher JSON Encoding

```json
{ "type": "keyword",    "any_of": ["phrase_a", "phrase_b"] }

{ "type": "regex",      "patterns": ["\\bpattern_a\\b", "pattern_b"] }

{ "type": "structural", "schema": { "required_fields": ["field_a", "field_b"] } }
```

## Appendix C — `/decide` Request and Response Examples (JSON path)

**Request:**
```json
{
  "task_id": "example-00001",
  "worker_output": "Signal phrase detected in routine processing.",
  "worker_model": "claude-haiku-4-5",
  "session_id": "sess-abc",
  "turn_index": 1,
  "confidence_score": 0.55,
  "confidence_coverage": 0.60,
  "confidence_grounding": 0.50,
  "confidence_novelty": 0.40,
  "materiality_value": 0.35
}
```

**Response:**
```json
{
  "decision": { "escalate": true, "confidence": 0.82 },
  "reasoning": {
    "signals_fired": [
      { "signal_name": "example_pattern", "raw_value": 0.80, "weight": 1.0, "contribution": 0.80, "notes": "pattern matched" }
    ],
    "composition_strategy": "weighted_sum",
    "threshold_applied": 0.40,
    "effective_score": 0.71,
    "override_applied": false
  },
  "trigger_cost": { "tokens_spent": 0, "wall_time_ms": 3, "budget_ceiling": 512 },
  "context_ref": {
    "memory_version": "api-supplied-v1",
    "summary_hash": "api-supplied",
    "window_end_turn": 1,
    "adapter_id": "example.v1"
  },
  "advisor_prompt_mods": {
    "additional_system_instructions": ["..."],
    "focus_directives": ["..."],
    "retrieval_hints": []
  }
}
```

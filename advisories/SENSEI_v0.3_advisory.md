# SENSEI Architectural Advisory — v0.3 Correction Before First Deployment

**Context:** SENSEI is currently pinned to a hard-coded adapter list in `sensei_api/registry.py`. Every new venture onboarding requires Python edits and a server restart coordinated through M3. This contradicts SENSEI's stated positioning as an agnostic primitive. The fix is surgical and should ship before first deployment. Post-deployment migration costs rise non-linearly.

This document is architectural advice, not a spec. The builder owns the implementation calls. Where judgment is required, that's flagged.

---

## What's right, and must be preserved

The v0.2 build is mostly correct. The fix is not a rewrite.

Pattern types are already pure data. `KeywordMatcher`, `RegexMatcher`, `StructuralMatcher`, and `StructuralSchema` are JSON-serializable by construction. No type changes needed.

Observability surface is adapter-keyed. `/events`, `/rollup`, and `/calibrate` work unchanged once adapters can register at runtime.

The trigger engine is adapter-agnostic. It consumes `AdapterTriggerConfig` and emits decisions without domain knowledge. Invariants I4, I11, NRC carry forward to v0.3 unchanged.

Decisions are stateless. No session state to migrate.

---

## Root cause

Contract v0.2 fuses two structurally different things into one type:

- **Pattern registry** — pure data, serializable, version-diffable, venture-agnostic in shape.
- **Materiality computation** — venture-specific Python, non-serializable.

Fusing them into a single `AdapterTriggerConfig` forced server-side registration because arbitrary Python cannot cross HTTP. Server-side registration forced a centralized registry. Centralized registry forced M3 coordination on every new venture. One early modeling choice cascaded into the agnosticism problem.

Separate the two types. The cascade unwinds.

---

## The correction — two registration paths, structurally distinct

**Path 1 — JSON registration (primary, agnostic).** `POST /adapters` accepts a pattern registry plus tuning params as JSON. Returns an `adapter_id`. Materiality is caller-supplied per `/decide` request as a `materiality_value: float`. Zero Python code on SENSEI's side. Zero M3 coordination. Any language client.

**Path 2 — Python plugin (power-user).** The existing `sensei_api/registry.py` stays for ventures that install as a Python library and want server-side materiality logic. This is the only path where `compute_materiality` as a `Callable` has meaning.

**Framing discipline.** The two paths have structurally different schemas. Do not describe the JSON path as "Python minus `compute_materiality`." That framing quietly privileges Python as the reference shape. Describe them as two distinct registration modes — one is the agnostic primitive, one is a plugin.

---

## Contract v0.3 amendments

Split `AdapterTriggerConfig` into two types:
- `AdapterSpec` — JSON-serializable, no `Callable` fields. The registration payload.
- `AdapterPlugin` — extends `AdapterSpec` with `compute_materiality: Callable`. The Python-only variant.

Add `POST /adapters` endpoint. In-memory only for v1 — ventures re-register on SENSEI restart. Event logs are already disk-backed per `SENSEI_LOG_DIR`; they reconnect to re-registered adapters by `adapter_id`.

Define the 404 protocol explicitly. `/decide` on an unregistered `adapter_id` returns 404. The contract clause must read: *Ventures MUST register on their own startup and treat 404 as a signal to re-register and retry.* Shifts registration lifecycle ownership to the venture, which is where it belongs.

Add `materiality_value: float` as an optional field on the `/decide` request body. Decide the failure mode when a JSON-registered adapter receives a `/decide` without `materiality_value`. Three options:

- Default to `materiality_floor` — forgiving.
- Default to `0.0` — literal.
- Return `400 materiality_value required` — strict.

Recommendation: strict. Silent defaults mask caller bugs and SENSEI's value proposition is deterministic decisions. Your call.

Add the trust-boundary clause to the contract: *`materiality_value` is caller-asserted. SENSEI does not verify stakes. Acceptable for internal trusted-network deployments. External or multi-tenant deployments require a verification layer.* Pin this before it becomes an audit finding.

---

## Three implementation details that will bite you

**Matcher polymorphism in JSON.** Use a discriminator field — `matcher.type ∈ {"keyword", "regex", "structural"}`. FastAPI + Pydantic handles this cleanly with discriminated unions. The standard pattern is standard for a reason. Don't invent a cute alternative.

**Re-registration semantics.** If the same `adapter_id` re-registers with a changed spec, treat it as the same adapter — append to the same event stream, keep calibration history. If a venture wants a clean slate, they pick a new `adapter_id` (e.g., `herald.v2`). Codify this so ventures don't accidentally fork their observability by editing a registration payload.

**Registry precedence in `/decide` resolution.** Lookup order: static Python registry first, then dynamic JSON registry, then 404. Two ventures cannot collide at the same `adapter_id` because Python-registered ventures own their ID space explicitly at server startup. Document the precedence in the code, not just in the advisory.

---

## Discipline to hold through implementation

**Grep enforcement.** `sensei_api/` and the contract doc must not contain the strings `herald`, `carmatch`, `avt_extractor`, or any venture name outside registration fixtures. Any hit is an agnosticism bug. Add as a 5-line CI check:

```bash
#!/usr/bin/env bash
set -euo pipefail
VENTURES=(herald carmatch avt_extractor subastop)
for v in "${VENTURES[@]}"; do
  if grep -rni --include='*.py' --include='*.md' "$v" sensei_api/ sensei_trigger/ sensei_observability/ contract/; then
    echo "LEAK: venture name '$v' found in agnostic surface"; exit 1
  fi
done
```

**Language enforcement.** No contract language that reads "for data-hub adapters..." or "for ventures with batch sessions..." The contract knows only about *adapters* as a generic type. If a clause only makes sense for one venture, it belongs in that venture's adapter spec, not in SENSEI.

---

## What is out of scope, intentionally

**No auth layer in v1.** Internal trusted-network deployment, documented. When SENSEI opens to external clients, auth and materiality verification both become required. Design for that boundary but do not cross it yet.

**No disk persistence for adapter specs.** Ventures own registration lifecycle. If operational pain forces a change, add it in v2 behind a config flag. Do not premature-optimize for something that may never hurt.

**No versioned adapter migrations.** `adapter_id` is the version. `herald.v1` and `herald.v2` are distinct adapters in the registry. Sidesteps a whole class of migration complexity.

---

## The stakes framing

Ship this correction before first deployment and you pay zero migration cost. Ship after and every existing adapter plus every venture already waiting in the queue pays the tax. You are currently in the cheapest possible window to fix this. It will not get cheaper.

The primitive is worth more than the shortcut. Hold the line.

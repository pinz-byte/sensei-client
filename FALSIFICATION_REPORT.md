# Falsification Report — Second Venture Adoption

**Venture:** AVT Extractor
**Adapter:** `avt_extractor.v1`
**Contract under test:** SENSEI v0.3 (JSON registration path)
**Test date:** 2026-04-17
**Author:** M1

## The question

Is `sensei_client` actually agnostic, or is it a HERALD-shaped helper wearing agnostic clothes?

The only test that answers this is adopting a *second* venture and observing what breaks. HERALD was the primitive's first customer; HERALD is also the only venture whose patterns shaped the v0.3 contract. If v0.3 is genuinely domain-neutral, a second venture integrates without the contract needing to bend. If it isn't, the seams surface immediately and v0.4 gets written before v0.3 calcifies.

## Test design

AVT Extractor was chosen over CarMatch and Subastop for a methodological reason: AVT has an unambiguous Worker-output shape (per-scrape-run extraction envelope + metadata), which means the test isolates the *primitive* without also stressing the *venture's own design*. Subastop is the higher-stakes test, deliberately deferred to a later round once the primitive has passed a cleaner trial.

Deliverables:
1. Adapter spec JSON conforming to contract v0.3 §2.3
2. Materiality function conforming to §2.6 / JSON-path caller-asserted semantics (§4.2)
3. Integration README showing the four-step adoption from `INTEGRATION.md`
4. End-to-end smoke test covering the four contract-critical paths (proceed / escalate / 404-retry / fail-open)

## Verdict

**Contract v0.3 held. The shipped client did not.**

Initial analytical pass: no clauses required amendment; no fields needed addition; no invariants were violated. The four-step integration from `sensei_client/INTEGRATION.md` ran as described on paper. Spec authored in one pass. Materiality function written in under 50 lines of pure, offline, deterministic Python. Same `check_and_escalate` entry point, same `SenseiConfig.from_spec_file` loader, same registration semantics.

**Then the end-to-end smoke test ran against a live M3 SENSEI service and contradicted the paper verdict.**

`sensei_client/types.py` in v0.3.1 made three of the four contract-required confidence fields `Optional[float] = None` and silently omitted them from the `/decide` wire payload when the caller didn't supply them. Contract v0.3 §3 (`SENSEI_Contract_v0.3.md` lines 120–123) specifies all four as required floats. M3's `sensei_api` enforces this correctly; `/decide` returned 422 on every non-trivial scenario.

**Re-verdict:** the contract itself passed falsification. The *implementation* (`sensei_client` v0.3.1) did not comply with the contract it claimed to implement. Fixed in v0.3.2: all four confidence fields required, no silent defaults, no optional omission. Smoke test now passes 4/4 on M1 and M3.

This outcome is cleaner than the paper verdict. An untested claim of contract adherence is exactly the failure mode an end-to-end smoke test is designed to surface. The test worked as intended.

## What held (the important negatives)

These are the axes where a HERALD-leaking contract *would* have broken, and didn't:

- **Worker-output shape.** HERALD's Worker emits free-form natural language. AVT's emits a structured envelope with key=value fields. Both slotted into the `worker_output: string` field without issue. The hard-task signal matchers (keyword / regex / structural) were flexible enough to cover both shapes from opposite ends of the text-vs-structured spectrum.

- **Materiality semantic domain.** HERALD's four factors score *what kind of operation Worker is asking to perform* (destructive? regulated data? fan-out? high-volume?). AVT's four factors score *how trustworthy this extraction run is* (volume anomaly? schema drift? price outliers? source novelty?). Completely different semantics — same mechanical composition (weighted sum → float in [0,1]) and same wire format (`materiality_value`). Invariant I11 (materiality ≠ confidence) continued to hold in the new domain without reinterpretation.

- **Irreversibility surface.** HERALD worries about SQL-style destruction (`DROP`, `TRUNCATE`, `DELETE FROM`). AVT worries about promotion-to-consumers (`canonical_write`, `commit to canonical`). Different vocabularies, same underlying concern — and the hard-task signal matcher accommodated both without any contract change. The contract stayed abstract over "what is irreversible"; each venture fills in its own answer.

- **Cross-boundary concept.** HERALD: `fan-out to consumers`, `egress to external`. AVT: `cross-market write`, `wrong market code`. The pattern generalizes — each venture defines what its trust boundaries are and writes matchers against them. The contract never had to name any specific boundary.

- **Advisor per-session cap.** HERALD uses 4; AVT uses 6. Different operational rhythms (HERALD processes Worker tasks individually; AVT runs scrape sessions across many sources back-to-back). The cap is a knob, not a ceiling — both values live happily in the same schema field.

- **Registration path.** AVT used the JSON path (`POST /adapters` + `materiality_value` in the /decide body). HERALD also uses JSON path. Plugin path (the Python `compute_materiality` callable server-side) was not needed — and specifically, AVT did *not* reveal a case for it. This is a data point in favor of JSON-path-is-sufficient for the venture class M1 is authoring for. The plugin path remains available for the genuine power-user case but the default has been validated.

- **Contract version marker.** HERALD's JSON still carries `"contract_version": "0.3-proposed"` (legacy from when v0.3 was a draft). AVT's is `"0.3"`. Both were accepted. This is a latent cleanup item for HERALD, not a contract issue.

## What surfaced (design insights, not failures)

These are observations that emerged from the adoption exercise. They are not contract breaks — but they are load-bearing to understand if ventures 3+ adopt.

### 1. Hard-task signals are rhetorical; materiality is structural.

The server-side hard-task signal layer pattern-matches on the *text* the Worker emits. If the Worker doesn't *say* something anomalous happened, the signal doesn't fire — even if the structural facts in the envelope would justify firing. For example, `rows_extracted=120 expected_rows=1800` is a 93% volume collapse structurally, but the `extraction_volume_collapse` signal only fires if the Worker emits the literal words "volume collapse" or equivalents.

This isn't a bug. It's a contract: the Worker is expected to annotate its output with semantic tags when it knows something's wrong, and the materiality function is expected to catch what the Worker doesn't (or won't) flag. The two layers compose. Ventures adopting should understand this split.

**Implication for venture authors:** Train Workers to emit semantic status tags. Don't rely on materiality to carry the load alone.

### 2. Weighted-sum calibration has a single-factor floor.

With four factors weighted 0.15–0.30 each, no *single* factor at full strength reaches the threshold of 0.40 on its own. Materiality-only escalation requires at least two factors firing partially, or a factor × 1.33 saturated.

This is consistent with HERALD's calibration (threshold 0.35, weights 0.20–0.30). The design intent is that single-factor silent anomalies are caught by the hard-task signal layer (which has base_weights of 0.60–0.95 per signal); materiality is for the *conjunction* of soft signals that no single pattern would flag.

**Implication:** Ventures that want single-factor escalation paths need to express them as hard-task signals, not materiality factors. Alternatively, raise a single-factor weight above the threshold — but that collapses the composition semantics into a gate, defeating the point.

### 3. Source novelty is an uncertainty multiplier, not a stakes signal.

`source_novelty` (weight 0.15) is doing something structurally different from the other three AVT factors. It's not scoring "how bad is this outcome" — it's scoring "how much should we trust the other three factors' baselines." The existing contract doesn't have a vocabulary for "meta-signal" — and v0.3 doesn't need it, because weighted sum handles the degenerate case fine. But if a fourth or fifth venture wants multiple uncertainty multipliers interacting nonlinearly, v0.4 may need `composition_strategy` values beyond `weighted_sum / max_signal / threshold_gated`.

**Implication:** Track how many ventures want nonlinear composition. If ≥2, propose a `composition_strategy: "custom_plugin"` path in v0.4 (keeping JSON path agnostic, but letting plugin-path ventures compose richly).

### 4. The venture-specific README pattern works.

HERALD's `herald_sensei/README.md` and AVT's `sensei_adapters/avt_extractor/README.md` have identical structure: what is gated, the four factors table, the hard-task signal list, the Worker envelope shape, the three-line integration, and "worth calling out" for domain-specific caveats. This consistency isn't accidental — it fell out of the four-step integration framework. Third-venture adoption (Subastop or CarMatch) should reuse this skeleton.

## Residual risk

### Unknown

AVT is structurally *less* demanding than HERALD in some ways: its Worker output is smaller (a scrape envelope is ~500 bytes vs HERALD's potentially-long text), its materiality signals are numerical (not pattern-based), and its irreversibility boundary is singular (canonical_listings table). A venture with *larger* Worker output, *nested* materiality, and *multiple* irreversibility boundaries might still surface contract weaknesses AVT did not. Subastop may be that venture — bid validation has multiple trust layers (bidder identity, bid magnitude, auction state, reserve-price proximity).

### Known-and-accepted

- Registration is in-memory only (§3.5). M3 SENSEI service restart drops all JSON registrations. 404-retry path handles this; adoption cost is the 4ms of re-register latency on the restart-boundary request. Acceptable.
- `materiality_value` is caller-asserted on the JSON path (§7). Internal-trusted-network deployment profile is fine for M-lattice traffic. Becomes a hardening item if SENSEI is ever exposed outside the M-lattice.

## Next test

Third venture: **Subastop bid validation Worker** OR **CarMatch recommendation Worker**.

Pick the one whose Worker shape is most *unlike* both HERALD (free-text operations) and AVT (structured extraction envelopes). My current bet: Subastop, because bid validation involves identity + magnitude + state + reversibility windows — four-way composition that's different in character from either existing adapter. If Subastop adopts cleanly, v0.3 has earned its keep; propose ratification.

If Subastop surfaces contract gaps, those become the v0.4 input list *before* v0.3 gets baked into three-plus venture deployments and the cost of amendment compounds.

## Summary

Contract v0.3 passed its first falsification test. No amendments required. Adapter spec, materiality function, and four-step integration all worked as documented. The primitive is, for the ventures tested, genuinely domain-neutral.

This is the moment the agnosticism claim stops being a design aspiration and becomes an engineering fact. One more venture to ratify.

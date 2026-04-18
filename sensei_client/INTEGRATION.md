# sensei_client — Integration Guide

**Audience:** any builder in M1 wiring a venture into SENSEI.
**Contract:** SENSEI v0.3 (`/contract/SENSEI_Contract_v0.3.md`).
**Scope:** venture-agnostic. Use verbatim for HERALD, CarMatch, AVT Extractor, Subastop, or whatever ships next.

---

## The 4-step integration

Every venture integrates in the same four steps. The details differ; the shape doesn't.

1. **Author your adapter spec** — a JSON file conforming to `AdapterSpec` (contract v0.3 §2.1).
2. **Write your materiality function** — a pure function `(str) -> float` returning a stakes score in `[0.0, 1.0]`.
3. **Register at startup** — one call to `SenseiClient.register_from_config()`.
4. **Wrap Worker outputs** — one call to `check_and_escalate(...)` per Worker completion, before the output leaves the venture.

That's it. Everything else — 404 re-registration, fail-open on SENSEI unreachable, Advisor invocation, verdict parsing — lives inside `sensei_client`.

---

## Step 1 — Adapter spec

A SENSEI adapter spec declares:

- **Hard-task patterns** — keyword / regex / structural signals that flag a Worker output as non-trivial.
- **Trigger params** — threshold, budget, composition strategy.
- **Advisor config** — which model reviews escalations, with what system prompt.

See `sensei_adapters/herald/herald.v1.json` for a worked example.

Skeleton for a new venture:

```json
{
  "adapter_id": "your_venture.v1",
  "contract_version": "0.3",
  "hard_task_registry": [
    {
      "id": "some_signal_name",
      "description": "What this signal means in your domain.",
      "matcher": {"type": "keyword", "any_of": ["phrase one", "phrase two"]},
      "base_weight": 0.80,
      "materiality_boost": 0.20
    }
  ],
  "trigger_threshold": 0.35,
  "trigger_budget_tokens": 512,
  "composition_strategy": "weighted_sum",
  "materiality_floor": 0.0,
  "advisor_model": "claude-opus-4-7",
  "advisor_per_session_cap": 4,
  "advisor_prompt_template": "You are reviewing a task that your_venture's Worker model escalated. Your priorities are X, Y, Z. End your response with exactly one verdict token on its own line — APPROVE, REJECT, or ESCALATE."
}
```

Discipline:

- **Do not** leak venture-specific language into shared code. The JSON file is yours; the Python library is everyone's.
- **Weights sum is arbitrary.** SENSEI uses `composition_strategy` to combine signals. For `weighted_sum`, weights don't need to normalize — the `trigger_threshold` is what you tune.
- **Write the description in domain language.** When SENSEI escalates, the fired-pattern list goes into the Advisor prompt. Descriptions are what makes review decisions legible.

---

## Step 2 — Materiality function

Materiality is the stakes score. *Not quality.* Invariant I11: materiality and confidence are independent signals.

A good materiality function is:

- **Pure.** Same input → same output. No hidden state.
- **Offline.** No LLM calls. No network. No DB reads. This runs on every Worker output — it must be fast.
- **Bounded.** Returns a float in `[0.0, 1.0]`. Clamp if needed.
- **Interpretable.** You should be able to explain "materiality was 0.7 because …" from the inputs alone.

Structural shape we use for most ventures is a weighted sum of binary factors:

```python
def compute_my_venture_materiality(worker_output: str) -> float:
    text = worker_output or ""
    factor_a = 1.0 if SOME_PATTERN.search(text) else 0.0
    factor_b = 1.0 if OTHER_PATTERN.search(text) else 0.0
    factor_c = _count_based_factor(text)  # normalized to [0, 1]

    raw = (
        0.30 * factor_a
      + 0.40 * factor_b
      + 0.30 * factor_c
    )
    return max(0.0, min(1.0, raw))
```

HERALD's full function lives at `sensei_adapters/herald/herald_materiality.py` — copy the shape, replace the patterns.

---

## Step 3 — Register at startup

Registration is idempotent: if you re-register the same `adapter_id`, SENSEI replaces the spec and keeps the event log (contract §4.1).

```python
from sensei_client import SenseiConfig, SenseiClient

def sensei_bootstrap():
    config = SenseiConfig.from_env(
        spec_path="/etc/your_venture/your_venture.v1.json",
    )
    client = SenseiClient(config)
    client.register_from_config()
    return config, client
```

Environment variables:

- `SENSEI_API_URL` — defaults to `http://localhost:8000` if unset.
- `SENSEI_ADAPTER_SPEC` — path to your adapter JSON (alternative to passing `spec_path` explicitly).

Re-registration lifecycle: SENSEI may be in-memory only (contract v0.3 §4). If you see a 404 on `/decide`, `sensei_client` re-registers automatically. You do not need to manage this.

---

## Step 4 — Wrap Worker outputs

```python
from sensei_client import WorkerTask, check_and_escalate

def run_worker(task_id, session_id, turn_index, prompt):
    worker_output = worker_model.run(prompt)
    confidence = worker_model.confidence()

    task = WorkerTask(
        task_id=task_id,
        worker_output=worker_output,
        worker_model="claude-haiku-4-5-20251001",
        session_id=session_id,
        turn_index=turn_index,
        confidence_score=confidence,
    )

    result = check_and_escalate(
        task=task,
        materiality_fn=compute_my_venture_materiality,
        config=CONFIG,
        client=CLIENT,
    )

    if result.should_ship:
        emit(worker_output)
        return

    if result.verdict == "REJECT":
        log_rejection(task_id, result.advisor.reasoning_text)
        return

    # ESCALATE
    queue_for_human_review(task_id, result)
```

---

## Verdict semantics

| verdict    | meaning                                              | typical action             |
|------------|------------------------------------------------------|----------------------------|
| `PROCEED`  | SENSEI did not flag. Ship the Worker output.         | emit downstream            |
| `APPROVE`  | Advisor reviewed and approved.                       | emit downstream            |
| `REJECT`   | Advisor reviewed and rejected.                       | drop / retry with different prompt |
| `ESCALATE` | Advisor said a human must look at this.              | queue for human review     |

`result.should_ship` is the one-liner: `True` iff verdict is PROCEED or APPROVE.

**Fail-safe default:** if the Advisor response can't be parsed for a verdict, it defaults to `ESCALATE`. Human review is always safer than silent auto-approve.

**Fail-open default:** if SENSEI is unreachable (network error, 5xx), the guard returns `PROCEED` with `sensei_reachable=False` and `fail_open=True`. SENSEI going down must not block your pipeline. Log the flag, alert on it, but don't deadlock.

To tighten either default, pass `fail_open_on_unreachable=False` or `fail_safe_on_advisor_error=False` to `check_and_escalate`.

---

## Per-call overrides

Most callers use the library with defaults. Three knobs matter when you need more control:

- `focus_directives` — list of strings added to the Advisor's user message under "Focus On". Use to narrow review scope for a specific task type.
- `retrieval_hints` — list of strings passed as "Retrieval Hints". Use when you want to tell the Advisor which docs / context to consider.
- `additional_system_instructions` — appended to the Advisor's system prompt. Use for per-call policy (e.g., "This is a production-critical task; be conservative").

All three are optional. The adapter spec's `advisor_prompt_template` is the baseline; these are composable additions, not replacements.

---

## What NOT to do

- **Do not compute materiality inside SENSEI.** Under the v0.3 JSON-registration path, materiality is caller-asserted. This is the reason SENSEI is agnostic. Computing it inside SENSEI re-introduces the Python-plugin coupling.
- **Do not share `adapter_id` across ventures.** Each venture owns its ID namespace.
- **Do not bypass the guard in the "hot path."** The whole point is that every Worker output crosses SENSEI before leaving the venture. If performance is a concern, use async (v1.1 candidate) — don't skip the check.
- **Do not log the Advisor response as structured JSON and forget the raw text.** The verdict parser scans the text; changes to the parser want the original string to replay.

---

## Testing your integration

A minimum viable integration test:

```python
from sensei_client import SenseiConfig, WorkerTask, check_and_escalate
from unittest.mock import MagicMock

def test_guard_flow():
    config = SenseiConfig.from_spec_dict(
        adapter_spec=YOUR_SPEC_DICT,
        api_url="http://localhost:8000",
    )
    client = MagicMock()
    client.config = config
    client.adapter_id = config.adapter_id
    client.decide.return_value = {
        "adapter_id": config.adapter_id,
        "escalate": False,
        "trigger_score": 0.1,
        "fired_patterns": [],
        "composition_strategy": "weighted_sum",
        "decision_trace": {},
    }

    task = WorkerTask(
        task_id="t1",
        worker_output="benign worker output",
        worker_model="test-model",
        session_id="s1",
        turn_index=0,
        confidence_score=0.9,
    )

    result = check_and_escalate(
        task=task,
        materiality_fn=lambda _: 0.2,
        config=config,
        client=client,
    )

    assert result.verdict == "PROCEED"
    assert result.should_ship is True
```

Before production: run against a local SENSEI instance, register your adapter, send a mix of benign / material / destructive Worker outputs, verify the verdicts match your expectations.

---

## When to extend the library vs your venture

**Extend `sensei_client` when** the change helps every venture — new transport concern, new retry policy, new auth primitive, new advisor provider.

**Extend your venture's adapter when** the change is domain-specific — a new pattern, a refined materiality function, a tuned prompt.

If in doubt, start in your venture. Promote to `sensei_client` only when a second venture needs the same thing.

---

## Reference artifacts

- **Contract:** `contract/SENSEI_Contract_v0.3.md`
- **Advisory:** `advisories/SENSEI_v0.3_advisory.md`
- **Reference venture integration (HERALD):** `herald_sensei/` — copy this as your template.
- **Reference adapter artifacts (HERALD):** `sensei_adapters/herald/` — spec JSON + materiality function.

---

## Versioning

`sensei_client` tracks the SENSEI contract major+minor. `sensei_client==0.3.x` implements contract v0.3. When the contract bumps to v0.4, the client bumps too and this guide gets revised.

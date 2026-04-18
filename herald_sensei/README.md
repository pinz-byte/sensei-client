# herald_sensei

HERALD's adoption of `sensei_client`. Also serves as the **worked example** other M1 ventures clone when integrating with SENSEI.

## What this package is

Three files of glue between HERALD's runtime and the agnostic `sensei_client` library:

- `materiality.py` — HERALD's 4-factor materiality function, surfaced as the `(str) -> float` callable SENSEI expects.
- `wiring.py` — `bootstrap_herald_sensei()` and `guard_herald_output()`. Call the first at startup, call the second around every Worker output.
- `__init__.py` — public API.

Everything heavy — config loading, HTTP, 404 retry, Advisor invocation, verdict parsing, fail-open / fail-safe logic — lives in `sensei_client`. This package is ~150 lines; the intent is explicit and copyable.

## How HERALD uses it

At HERALD process startup:

```python
from herald_sensei import bootstrap_herald_sensei
bootstrap_herald_sensei()
```

Around every Worker output:

```python
from herald_sensei import guard_herald_output
from sensei_client import WorkerTask

task = WorkerTask(
    task_id=task_id,
    worker_output=worker_model_output,
    worker_model="claude-haiku-4-5-20251001",
    session_id=session_id,
    turn_index=turn_index,
    confidence_score=worker_confidence,
)

result = guard_herald_output(task)

if result.should_ship:
    emit_downstream(worker_model_output)
elif result.verdict == "REJECT":
    log_rejection(task_id, result.advisor.reasoning_text)
else:  # ESCALATE
    queue_for_human_review(task_id, result)
```

That's the whole integration. Two call sites.

## How to use this as a template

Any new venture integrating with SENSEI (CarMatch, AVT Extractor, Subastop, …) can clone this directory.

The three edits:

1. **Rename.** `cp -r herald_sensei/ your_venture_sensei/`, then s/herald/your_venture/g across filenames and identifiers.
2. **Swap the materiality function.** Replace `materiality.py` — either import from your venture's adapter package, or define your factors inline.
3. **Point at your spec JSON.** Update `DEFAULT_SPEC_PATH` in `wiring.py` to the location of `your_venture.v1.json`.

After that, `bootstrap_your_venture_sensei()` and `guard_your_venture_output()` behave identically.

## What's in `sensei_adapters/herald/`

The adapter-side artifacts — spec JSON, Python plugin reference, full materiality function with its component breakdown. Those are HERALD's declarations about what it wants SENSEI to do. This directory is HERALD's wiring to actually use it.

The split is deliberate: adapter-side artifacts can be versioned and audited without touching the runtime glue.

## Environment variables

- `SENSEI_API_URL` — SENSEI base URL. Defaults to `http://localhost:8000`.
- `HERALD_SENSEI_SPEC_PATH` — override for the spec JSON path. Defaults to the conventional location adjacent to `sensei_adapters/herald/`.

## See also

- `sensei_client/INTEGRATION.md` — the generic integration guide.
- `contract/SENSEI_Contract_v0.3.md` — the formal contract.
- `advisories/SENSEI_v0.3_advisory.md` — architectural notes for the SENSEI builder.

# Install Guide — sensei_client

**Audience:** any consuming station (M2, M3, HERALD's deployment, a venture runtime) that needs to install and use `sensei_client`. M1 is the authoring station; other stations install it as a distributed artifact.
**Time to running integration:** under one hour.
**Contract version:** v0.3.

---

## Station model

Before the install steps, the topology this guide assumes:

- **M1** authors and maintains `sensei_client`, the adapter artifacts, and the HERALD adoption.
- **M3** runs the SENSEI service. Adapter registrations hit M3's HTTP endpoint.
- **M2** is an independent workstation with its own build; it consumes `sensei_client` the same way any other station does.
- **Venture runtimes** (HERALD, CarMatch, AVT Extractor, Subastop, anything next) consume `sensei_client` independently from whichever environment they run in.

Workstations are **not** a shared filesystem. Code authored on M1 travels to the consumer through one of the distribution channels below. Pick one and stick with it per consumer.

---

## Prerequisites

Before you start, confirm on the **consuming** station (not M1):

- **Python 3.9 or newer.** Check with `python3 --version`.
- **Network reachability to SENSEI on M3.** Default URL is `http://localhost:8000` for local tests; ask M3 for the production URL and confirm the port is open from your station.
- **(Optional) `ANTHROPIC_API_KEY` environment variable** — required only if your venture will invoke the Advisor model after a SENSEI escalation. Most do.

Install-time dependencies are resolved automatically: `httpx` (hard) and `anthropic` (optional, for the Advisor).

---

## Step 1 — Get `sensei_client` onto your station

Three distribution paths, in rough order of preference.

### 1A — Install from the shared git repo (preferred)

Once M1 has pushed to a shared repo this is the one-liner:

```bash
pip install "git+<m1-repo-url>@v0.3.0#egg=sensei_client"
```

Pin a tag or SHA. Do not install from `main` into a venture runtime — SENSEI contract bumps rely on coordinated deploys.

### 1B — Install from a wheel artifact

M1 builds a wheel and publishes it to a shared drop (S3, shared drive, private index, whatever the org uses):

```bash
# On M1 — build the wheel
cd "/path/on/M1/SENSEI M1"
python -m pip install build
python -m build --wheel
# → dist/sensei_client-0.3.0-py3-none-any.whl
```

Then the consumer copies the `.whl` across and installs:

```bash
pip install /path/to/sensei_client-0.3.0-py3-none-any.whl
```

### 1C — Editable install from a local copy (M1 only, or a mounted volume)

This is the path M1 itself uses for development, and what any consumer falls back to if they have the source tree locally:

```bash
cd "/path/to/SENSEI M1"
pip install -e .
```

**Important:** editable installs are for the authoring station or for a station that has the source tree mounted. Do not ship production ventures pointing at a path on M1 — the coupling is brittle.

### Verify which path you used

Whichever distribution channel you chose, the next steps are identical. If in doubt:

```bash
python3 -c "import sensei_client; print(sensei_client.__file__)"
```

If it prints a path inside your venv's site-packages, you installed from a wheel or git URL. If it prints a path inside a local `SENSEI M1/` tree, you're on an editable install.

---

## Step 2 — Confirm the install landed

Paths 1A and 1B have already finished installing; this step is a sanity check. Path 1C has already finished too.

```bash
python3 -c "import sensei_client; print(sensei_client.__version__)"
```

Expected output:

```
0.3.0
```

If you hit `ModuleNotFoundError`, the install was run in a different Python environment than the one you're testing in. Activate your venv and re-check:

```bash
python3 -m venv .venv
source .venv/bin/activate
# then re-run whichever of 1A / 1B / 1C you chose
```

**Using `uv` instead of pip:** all three paths work by substituting `uv pip install ...` for `pip install ...`.

---

## Step 3 — Install the Advisor extras (if you'll escalate)

Most ventures escalate at least some outputs. To enable the Advisor call, add the `[advisor]` extra for whichever install path you used in Step 1:

```bash
# Path 1A (git)
pip install "sensei_client[advisor] @ git+<m1-repo-url>@v0.3.0"

# Path 1B (wheel)
pip install "/path/to/sensei_client-0.3.0-py3-none-any.whl[advisor]"

# Path 1C (editable, on a station with the source tree)
pip install -e ".[advisor]"
```

This pulls in the `anthropic` SDK. Then set your key:

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

Skip this step only if you intend to handle escalations yourself (queueing to a human reviewer without automated Advisor review).

---

## Step 4 — Point at SENSEI

```bash
export SENSEI_API_URL="http://localhost:8000"       # or your production URL
```

Optionally, set the default adapter spec path so you don't need to pass it in code:

```bash
export SENSEI_ADAPTER_SPEC="/absolute/path/to/your_venture.v1.json"
```

For HERALD specifically:

```bash
export HERALD_SENSEI_SPEC_PATH="$(pwd)/sensei_adapters/herald/herald.v1.json"
```

---

## Step 5 — Verify the install

Two checks. The first works on any station (no spec file needed). The second requires you to have your adapter spec JSON on the station.

### 5a — Library imports and types construct

```bash
python3 -c "
import sensei_client
print('sensei_client', sensei_client.__version__)
from sensei_client import WorkerTask, SenseiConfig, check_and_escalate
print('public API reachable')
"
```

Expected:

```
sensei_client 0.3.0
public API reachable
```

### 5b — Spec file parses

If you already have your adapter spec on this station:

```bash
python3 -c "
from sensei_client import SenseiConfig
config = SenseiConfig.from_spec_file('$SENSEI_ADAPTER_SPEC')
print('adapter_id:', config.adapter_id)
print('advisor_model:', config.advisor_model)
print('contract:', config.contract_version)
"
```

If you're on a station that doesn't have `herald.v1.json` and doesn't yet have its own spec — that's fine. Skip 5b and come back after Step 6 when you've authored your adapter spec.

---

## Step 6 — Integrate your venture

The path forks here depending on whether you are HERALD (adoption) or a new venture (template).

### Path A — You are HERALD

Your adapter artifacts already exist at `sensei_adapters/herald/`. Your runtime wiring already exists at `herald_sensei/`.

**At HERALD process startup:**

```python
from herald_sensei import bootstrap_herald_sensei

bootstrap_herald_sensei()   # registers herald.v1 with SENSEI
```

**Around every Worker output:**

```python
from herald_sensei import guard_herald_output
from sensei_client import WorkerTask

def handle_worker_output(task_id, session_id, turn_index, prompt, worker_output, confidence):
    task = WorkerTask(
        task_id=task_id,
        worker_output=worker_output,
        worker_model="claude-haiku-4-5-20251001",
        session_id=session_id,
        turn_index=turn_index,
        confidence_score=confidence,
    )

    result = guard_herald_output(task)

    if result.should_ship:
        ship_to_downstream(worker_output)
    elif result.verdict == "REJECT":
        log_rejection(task_id, result.advisor.reasoning_text)
    else:  # ESCALATE
        queue_for_human_review(task_id, result)
```

Done. Skip to Step 7.

### Path B — You are a new venture (CarMatch / AVT / Subastop / next)

Three edits turn the HERALD template into your integration. Do the edits inside your venture's own repo — do not work inside M1's source tree.

You need two reference artifacts from M1 as starting skeletons: `herald.v1.json` and the `herald_sensei/` directory. Pull them from whichever channel you used in Step 1 — git (`git archive` or a shallow clone), the wheel's source bundle, or a copy M1 sends you out-of-band.

**B.1 — Author your adapter spec.**

Place it in your venture's repo, not in M1's tree:

```bash
mkdir -p your_venture/sensei/
cp /path/to/reference/herald.v1.json your_venture/sensei/your_venture.v1.json
```

Edit the JSON:

- `adapter_id` → `your_venture.v1`
- `hard_task_registry` → replace HERALD's 7 patterns with signals that matter in *your* domain
- `advisor_prompt_template` → rewrite in your domain's language with your review priorities

See `sensei_client/INTEGRATION.md` (packaged inside the library) for the full AdapterSpec schema.

**B.2 — Write your materiality function.**

Pure. Offline. No network. Returns a float in `[0.0, 1.0]`. Use HERALD's function as a reference (`herald_materiality.py` in the M1 source tree or in a git-archived copy).

Place yours inside your venture's repo:

```python
# your_venture/sensei/materiality.py

def compute_your_venture_materiality(worker_output: str) -> float:
    ...
    return value  # clamped to [0.0, 1.0]
```

**B.3 — Clone the runtime wiring into your venture.**

Copy `herald_sensei/` into your venture's repo as a sibling to your materiality function:

```bash
cp -r /path/to/reference/herald_sensei your_venture/sensei_wiring/
```

Then edit three things:

1. `wiring.py` — rename `bootstrap_herald_sensei` → `bootstrap_your_venture_sensei`, same for `guard_herald_output`. Update `DEFAULT_SPEC_PATH` to point at your JSON (an absolute path that resolves inside your venture's deployment, not inside M1's tree).
2. `materiality.py` — drop the `importlib` trampoline (it was reaching across M1's tree into `sensei_adapters/herald/`). Replace with a direct import from your venture's own materiality module:
    ```python
    from your_venture.sensei.materiality import compute_your_venture_materiality
    ```
3. `__init__.py` — update the public exports to your venture's names.

At your venture's process startup:

```python
from your_venture.sensei_wiring import bootstrap_your_venture_sensei, guard_your_venture_output
bootstrap_your_venture_sensei()
```

Around every Worker output:

```python
result = guard_your_venture_output(task)
```

Same verdict semantics as HERALD. The wiring code is your venture's now — not coupled to M1's tree.

---

## Step 7 — End-to-end smoke test against live SENSEI on M3

With SENSEI running on M3 and your adapter registered, send a test Worker output and confirm the flow. This script uses a **trivial inline materiality function** so it runs on any station without depending on M1's `herald_sensei` tree:

```python
import os
from sensei_client import (
    SenseiConfig, SenseiClient, WorkerTask, check_and_escalate,
)

config = SenseiConfig.from_spec_file(os.environ["SENSEI_ADAPTER_SPEC"])
client = SenseiClient(config)
client.register_from_config()

def trivial_materiality(worker_output: str) -> float:
    # Crude but sufficient for smoke: destructive keywords → 0.9, else 0.0.
    destructive = ("DROP", "TRUNCATE", "DELETE FROM", "rm -rf")
    return 0.9 if any(k in worker_output for k in destructive) else 0.0

task = WorkerTask(
    task_id="smoke-001",
    worker_output="DROP TABLE users CASCADE; -- clean out the legacy PII",
    worker_model="test-worker",
    session_id="smoke-session",
    turn_index=0,
    confidence_score=0.95,
)

result = check_and_escalate(
    task=task,
    materiality_fn=trivial_materiality,
    config=config,
    client=client,
)

print(f"verdict:          {result.verdict}")
print(f"sensei_reachable: {result.sensei_reachable}")
print(f"materiality:      {result.materiality_value}")
print(f"fired_patterns:   {result.decision.fired_patterns if result.decision else None}")
if result.advisor:
    print(f"advisor_verdict:  {result.advisor.verdict}")
    print(f"advisor_text:     {result.advisor.reasoning_text[:200]}")
```

This payload is worst-case (destructive + classified + cross-boundary — assuming your spec has patterns matching these). You should see SENSEI fire multiple patterns, escalate, and the Advisor return `REJECT` or `ESCALATE`.

Once smoke passes, replace `trivial_materiality` with your venture's real materiality function and ship.

---

## Troubleshooting

**`ModuleNotFoundError: No module named 'sensei_client'`**
→ `pip install -e .` didn't run or ran in a different environment. Activate your venv first, then reinstall.

**`SenseiUnreachable: POST /adapters failed`**
→ SENSEI service is not responding at `$SENSEI_API_URL`. Confirm the service is up: `curl $SENSEI_API_URL/health`. If the guard still fails open, that's by design — SENSEI being down must not block your pipeline. Log the `fail_open=True` flag and investigate async.

**`AdapterNotRegistered`**
→ Normally auto-retried once by the guard. If it persists, your registration is failing — read the next error up the chain:
- `SenseiConflict` (409): your `adapter_id` collides with a Python-plugin adapter. Rename your venture's `adapter_id`.
- `SenseiBadRequest` (400): your spec is malformed. Re-validate against `contract/SENSEI_Contract_v0.3.md` §2.1.

**`400 materiality_value required`**
→ Your materiality function returned `None` or the guard was bypassed. Check your `materiality_fn` signature: `(str) -> float`. Do not return `None` on "no signal" — return `0.0`.

**Advisor returns `ESCALATE` when you expected `APPROVE`**
→ Intentional fail-safe. The verdict parser only accepts `APPROVE | REJECT | ESCALATE` tokens and defaults to `ESCALATE` on any parse failure. Inspect `result.advisor.reasoning_text` — if the model's response doesn't end with a verdict token on its own line, tighten the `advisor_prompt_template`.

**`RuntimeError: invoke_advisor requires the 'anthropic' package`**
→ You skipped Step 3. Run `pip install -e ".[advisor]"` and set `ANTHROPIC_API_KEY`.

**`FileNotFoundError: Adapter spec file not found`**
→ `SENSEI_ADAPTER_SPEC` (or the `spec_path` you passed) points at a missing file. Use an absolute path.

**The guard seems to run slowly on every Worker output.**
→ Each `/decide` is one HTTP round-trip plus, on escalation, one Advisor call. If latency matters in your hot path, wrap Worker outputs asynchronously — the sync guard is a v1 choice. Async is a v1.1 candidate on the roadmap.

**SENSEI restarts and my decisions start 404ing.**
→ Expected and handled. The guard catches the 404, re-registers, retries. If re-registration itself fails, that surfaces as `AdapterNotRegistered` — check SENSEI logs.

---

## Uninstall

```bash
pip uninstall sensei_client
```

This leaves the workspace folder untouched; only removes the editable install link.

---

## Where to read next

- [`sensei_client/INTEGRATION.md`](sensei_client/INTEGRATION.md) — the generic integration guide.
- [`contract/SENSEI_Contract_v0.3.md`](contract/SENSEI_Contract_v0.3.md) — the formal contract. Read §2 (types) and §3 (endpoints) before authoring a spec.
- [`herald_sensei/README.md`](herald_sensei/README.md) — HERALD as a template.
- [`advisories/SENSEI_v0.3_advisory.md`](advisories/SENSEI_v0.3_advisory.md) — the architectural reasoning behind v0.3.

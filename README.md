# SENSEI Integration — authored on M1

This directory is M1's working build of the SENSEI client library and HERALD's adoption of it. M1, M2, and M3 are independent workstations, each running their own builds. SENSEI the service runs on M3. HERALD runs wherever HERALD runs. This folder is **not** a shared workspace — it is M1's local source tree for the authored artifacts that other stations and venture deployments install.

Consumption is cross-station by default. See `INSTALL.md` for the distribution paths (git URL, wheel artifact, path install).

## What lives here

```
./                                      M1's local source tree
├── pyproject.toml                      sensei_client packaging (hatchling)
├── INSTALL.md                          Cross-station install guide
├── contract/                           Formal v0.3 contract (RFC 2119)
├── advisories/                         Architectural advisory to the SENSEI builder
├── sensei_client/                      AGNOSTIC Python client — distributed to consumers
│   ├── INTEGRATION.md                  Start here if you are integrating a new venture
│   ├── config.py / client.py / guard.py / advisor.py / types.py / exceptions.py
├── sensei_adapters/
│   └── herald/                         HERALD's adapter-side artifacts
│       ├── herald.v1.json              JSON registration payload
│       ├── herald_materiality.py       Reference materiality (4-factor)
│       └── adapter.py                  Python-plugin reference (legacy path)
└── herald_sensei/                      HERALD's runtime wiring — also the worked example
    ├── README.md                       How to clone this for a new venture
    ├── materiality.py / wiring.py / __init__.py
```

## Station topology

| station | role                                                        |
|---------|-------------------------------------------------------------|
| **M1**  | Authors and maintains `sensei_client` and HERALD adoption.  |
| **M3**  | Runs the SENSEI service. Consumes test adapter registrations. |
| **M2**  | Independent build; may consume `sensei_client` for its own integrations. |
| venture deployments | Consume the published `sensei_client` wheel / git ref. |

Stations are not mounted filesystems. Code authored here travels to consumers via the distribution channel documented in `INSTALL.md`.

## The agnostic primitive, not a HERALD helper

`sensei_client/` is the load-bearing artifact authored on M1. Contract-v0.3-conformant, venture-neutral, ~600 lines. Any consuming station or venture — HERALD, CarMatch, AVT Extractor, Subastop, or a new M-station build — installs this library and gets:

- Config loading from JSON spec + env vars
- HTTP client with typed exceptions (`AdapterNotRegistered`, `SenseiUnreachable`, `SenseiBadRequest`, `SenseiConflict`)
- `check_and_escalate(task, materiality_fn, config, client)` — one call wraps every Worker output
- 404 → re-register → retry, automatic
- Fail-open on SENSEI unreachable (configurable)
- Fail-safe on Advisor parse failure (defaults to `ESCALATE`)
- Advisor invocation using the adapter spec's `advisor_prompt_template`
- Composable prompt extensions (`focus_directives`, `retrieval_hints`, `additional_system_instructions`)

## Integration in four steps

Every consuming station follows the same path. See `sensei_client/INTEGRATION.md` for the walk-through.

1. **Install `sensei_client`** — via git URL, wheel, or local path (see `INSTALL.md`).
2. **Author an adapter spec** — JSON conforming to contract v0.3 §2.1.
3. **Write a materiality function** — pure, offline, `(str) -> float` in `[0.0, 1.0]`.
4. **Bootstrap at startup, wrap Worker outputs** — `register_from_config()` + `check_and_escalate(...)`.

HERALD's adoption in `herald_sensei/` is the worked example — copy that directory into the consuming codebase, rename, swap the materiality function and spec path. Under an hour to a running integration.

## Status

- Contract **v0.3** — shipped from M1 to M3 (the SENSEI builder station).
- Architectural advisory — shipped, acted on, CI check live, 176 tests passing on SENSEI (M3).
- `sensei_client` — v0.3.4. Authored on M1. Cross-station verified (M1 ↔ M3) via live smoke test against M3's SENSEI service. Installs the `[advisor]` extra by default; `ANTHROPIC_API_KEY` required at venture runtime.
- `herald_sensei` — integration glue complete. Wire-up into HERALD's Worker call sites pending HERALD source from whichever station hosts HERALD.
- **Distribution channel** — pending decision. Default assumption: shared git repo so consuming stations do `pip install git+<url>`. Until that exists, consumers install from a wheel artifact or path.

## Conventions the workspace enforces

- **SENSEI stays agnostic.** `sensei_client/` and `contract/` contain zero venture-specific strings. HERALD only appears in `herald_sensei/` and `sensei_adapters/herald/`.
- **Two registration paths, structurally distinct.** JSON (primary, agnostic) and Python plugin (power-user, legacy). Do not frame the JSON path as "Python minus `compute_materiality`" — they have different shapes by design.
- **Materiality ≠ confidence.** Invariant I11. Materiality is stakes; confidence is self-report. Compose them via `composition_strategy`, never fuse them.
- **Fail-open SENSEI, fail-safe Advisor.** SENSEI being down must not block ventures. Advisor parse failure must not silently auto-approve.

## Where to read next

- Starting a new integration → `sensei_client/INTEGRATION.md`
- Understanding the primitive → `contract/SENSEI_Contract_v0.3.md`
- HERALD as a template → `herald_sensei/README.md`
- Why v0.3 exists → `advisories/SENSEI_v0.3_advisory.md`

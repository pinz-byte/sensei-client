# SENSEI Station Setup

Role-specific bootstrap for the three M-stations in the lattice. Each station runs one script, sets one env var, runs one verify, and is ready to consume `sensei_client` against M3.

## Station matrix

| station | role                                                               | bootstrap path                   |
|---------|--------------------------------------------------------------------|----------------------------------|
| **M1**  | Author and maintain `sensei_client`, HERALD adoption, AVT adoption | [M1.md](M1.md) — verify checklist |
| **M2**  | Independent consumer of `sensei_client`                            | [M2.md](M2.md) — full bootstrap   |
| **M3**  | Runs the SENSEI service (port 8000); may also consume             | [M3.md](M3.md) — dual-role setup  |

## Quickstart — any station

```bash
cd /path/to/this/repo   # wherever you clone sensei-client
./setup/bootstrap.sh <M1|M2|M3>
```

The script asks for M3's reachable URL (M1/M2 only), creates a venv, installs `sensei_client` from the v0.3.0 tag, writes `.env`, and runs the smoke test. Idempotent — safe to re-run.

## What you end up with

After bootstrap on any station:

```
./venv/                           # per-station virtualenv
./.env                            # SENSEI_API_URL, SENSEI_STATION, PYTHONPATH
./setup/bootstrap.sh              # executable, re-runnable
```

`sensei_client` is importable in the venv. Environment variables load from `.env` (source it with `set -a; source .env; set +a` or equivalent). The smoke test at `tests/e2e_smoke.py` is runnable.

## Cross-station wiring

M1 and M2 need network reach to M3 on port 8000 (or whatever `SENSEI_API_URL` resolves to). Validate reachability before running the smoke test:

```bash
curl -f "${SENSEI_API_URL}/adapters"
```

If that fails, the issue is connectivity, not sensei_client.

## After setup

Each station is ready to host a project that wires `sensei_client` into its Worker path. See `sensei_client/INTEGRATION.md` in the repo root for the four-step venture-adoption walk-through.

The smoke test is a *wiring* test, not a project test. It proves the station can reach SENSEI and the guard round-trip works. Your next step — running `sensei_client` inside an actual venture project — is what validates end-to-end behavior under real Worker outputs.

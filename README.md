# Devin · Apache Superset Remediation

> An event-driven automation that turns dependency/security/code-quality
> findings in a fork of [Apache Superset](https://github.com/apache/superset)
> into **independently-verified** pull requests — by spawning and managing
> autonomous Devin sessions, and escalating to a human when (and only when) the
> fix isn't safely achievable.
>
> Built as a take-home for Cognition (Applied AI Engineer). The thesis:
> **Devin is the engine; this orchestrator is thin glue that adds the guardrails
> an enterprise needs to turn an autonomous agent loose safely** — event
> triggering, session lifecycle, independent verification, a bounded
> self-correction loop, escalation, and leader-facing observability.

---

## The one loop

```
 event ─▶ scan ─▶ file GitHub issue ─▶ create Devin session
                                              │
                                        Devin does the work
                                     (reads the code, edits
                                      across files, writes/runs
                                      tests, opens a PR — or escalates)
                                              │
        poll ◀───────────────────────────────┘
          │
   INDEPENDENT VERIFY  ── CI green? ──▶ ✅ verified → human merge
   (GitHub Checks API,     │
    not Devin's word)      ├── CI red? ──▶ send failure back to Devin
                           │               (bounded correction budget)
                           └── escalated? ─▶ ⚠️ needs-human, with rationale
          │
     SQLite state ─▶ /api/metrics + dashboard  ("is this working?")
```

**5 real issues, chosen to exercise 5 distinct behaviors** (see
[`docs/ISSUES.md`](docs/ISSUES.md)) — not a demo reel of easy wins:

| # | Issue | What it proves |
|---|-------|----------------|
| 1 | `cum.py` `fillna(0)` corrupts cumulative min/max/prod | **The judgment fix a bot can't do** — a semantics bug + regression test |
| 2 | Bump dompurify `^3.4.11 → ^3.4.13` (GHSA-55q2-fjhq-7xh7) | Trivial fast path — throughput & low ACU baseline |
| 3 | Blanket `# type: ignore` sweep + enable ruff PGH003 | In-tree code fix **+ self-correction when the gate turns CI red** |
| 4 | deck.gl/loaders.gl DoS advisory chain | **Bounded autonomy** — Devin *correctly refuses* and escalates |
| 5 | Blank Metric Warning text, `#42704` (`🦾 ai-candidate`) | Real maintainer-curated app bug, with a regression test |

These are **verified against the fork's `master` @ `e2bb33b` (2026-08-15, mirrors `apache/superset`)**
at the exact file/line/advisory: `cum.py:49` runs `fillna(0)` unconditionally
before every cumulative operator; `superset-frontend/package.json` pins the
vulnerable `dompurify: ^3.4.11`; blanket `# type: ignore` comments hide type
errors with ruff PGH003 not yet enabled; the deck.gl/loaders.gl DoS chain's only
`npm audit` fix is an unacceptable major downgrade; and `#42704` is a live,
maintainer-tagged issue. (The disproven Flask 3.x / SQLAlchemy 2.0 items from the
first draft are gone — see [`superset_scan_analysis.md`](../superset_scan_analysis.md).)

---

## Quickstart

### Run the full pipeline with **zero credentials** (recommended first)

```bash
cp .env.example .env          # DEMO_MODE=true is the default
# Optional: set GITHUB_REPO=your-org/superset so demo links point at your fork.
docker compose up --build
```

Prefer running it on the host? Use [uv](https://docs.astral.sh/uv/) — it reads
`pyproject.toml` + `uv.lock` and manages the virtualenv for you:

```bash
cp .env.example .env
uv run --env-file .env uvicorn main:app --app-dir orchestrator --host 0.0.0.0 --port 8000
```

Open **http://localhost:8000** and trigger a scan:

```bash
curl -X POST http://localhost:8000/api/scan
```

Within ~30s the dashboard shows all 5 tasks settle: **4 verified, 1 escalated**,
including issue #3 going red-CI → self-correct → green. `DEMO_MODE` replays
recorded, realistic Devin trajectories through the **identical** orchestrator
code path (same create/poll/verify/correct/escalate logic) — nothing about the
orchestrator is mocked, only the API backend. This is what makes a 5-minute demo
reliable.

### Run it **live** against real Devin + your fork

```bash
# 1. Fork apache/superset into your org.
# 2. Fill .env:
#    DEMO_MODE=false
#    DEVIN_API_KEY=...   DEVIN_ORG_ID=...       (app.devin.ai → Settings → API)
#    GITHUB_TOKEN=...    GITHUB_REPO=you/superset  (PAT with repo scope)
#    SUPERSET_CHECKOUT=../superset              (host path to your fork)
#    REPO_PATH=/superset                        (container path used by scanner.py)

# 3. Register the version-controlled Playbook with Devin (idempotent):
docker compose run --rm orchestrator python /app/scripts/bootstrap_playbook.py

# 4. File the remediation issues in your fork:
docker compose run --rm orchestrator python /app/scripts/create_issues.py --dry-run
docker compose run --rm orchestrator python /app/scripts/create_issues.py

# 5. Bring it up and scan:
docker compose up --build
curl -X POST http://localhost:8000/api/scan
```

If you prefer running the helper scripts locally instead of through Docker, use
[uv](https://docs.astral.sh/uv/) — `uv run --env-file .env` loads your `.env`
and runs inside the project's locked virtualenv (no manual activate/install):

```bash
uv sync                                                   # create .venv from uv.lock
uv run --env-file .env python scripts/bootstrap_playbook.py
uv run --env-file .env python scripts/create_issues.py --dry-run
uv run --env-file .env python scripts/create_issues.py
```

Now the orchestrator files real issues, opens real Devin sessions, reads the
**real GitHub Checks API** to verify each PR, and comments the session/PR links
back on each issue.

---

## Triggers (all converge on the same path)

| Source | Path | Purpose |
|--------|------|---------|
| **Scheduled** | GitHub Action cron → `POST /api/scan` (`.github/workflows/nightly-scan.yml`) | The periodic case |
| **Webhook** | GitHub `issues.labeled: devin-remediate` → `POST /webhook/github` (HMAC-verified) | Human/tool-filed one-offs |
| **Manual** | `POST /api/scan`, `POST /api/trigger/{issue}` | Demos, smoke tests |

## API

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Observability dashboard (auto-refresh) |
| GET | `/healthz` | Health check |
| GET | `/api/metrics` | Pipeline metrics (JSON) |
| GET | `/api/tasks` | All tasks with issue/session/PR links |
| POST | `/api/scan` | Run a scan now (optional `?subset=1,3`) |
| POST | `/api/trigger/{issue}` | Trigger one finding |
| POST | `/webhook/github` | GitHub event receiver (HMAC) |

---

## Observability — "how would an eng leader know this is working?"

The dashboard leads with the numbers a VP repeats to *their* boss:

- **Success rate** (verified + escalated, both correct outcomes)
- **PRs opened** — observable GitHub output, ready for human merge
- **Self-corrections** — how often the loop auto-nudged Devin past red CI (no human)
- **Human handoffs** — escalated/failed tasks that actually need a person (the honesty metric)
- **MTTR** — issue filed → resolved
- **ACU / PR** — cost-per-fix economics from session-reported ACU when available
- **In flight** — live throughput

Every task row links to the GitHub issue, the Devin session, and the resulting
PR. Escalations are shown as a **first-class successful outcome**, not a failure —
because bounded autonomy is the point.

---

## Key design decisions

- **Devin is the primitive, not a helper.** The orchestrator is deliberately thin
  (~1 file of routing). Everything requiring engineering judgment — diagnosing a
  correctness bug, writing a regression test, recovering from red CI, deciding a
  task isn't safely doable — happens *inside* Devin. Delete Devin and there's no
  product.
- **Eval over demonstration.** Success is marked from the **GitHub Checks API**,
  never from Devin's self-reported status. We separate *"PR opened"* from
  *"issue fixed"* ([`orchestrator/verifier.py`](orchestrator/verifier.py)).
- **Bounded autonomy.** One PR per issue; a correction budget
  (`MAX_CORRECTION_ROUNDS`) then escalate; **merge is always human**.
- **The Playbook is a Git asset**, synced into Devin via the API — reviewable in
  PRs, diffable, and reskinnable per customer engagement.
- **Tags are the contract.** Every session is tagged with issue, severity, kind,
  and `engagement:` — so a partner can attribute session activity to a customer/SOW.
- **Buy vs. build.** Devin ships [native Automations](https://docs.devin.ai/product-guides/automations)
  (GitHub/Slack/Linear/schedule triggers). For simple triggers a customer should
  use those. This custom orchestrator earns its keep only where it adds what
  native automations don't: cross-run **state** (issue↔session↔PR), independent
  **verification**, the **correction loop**, and **billing attribution**.

## Layout

```
devin-superset-remediation/
├── orchestrator/
│   ├── main.py           # FastAPI: triggers, scan→issue→session, poll→verify
│   ├── devin_client.py   # Thin Devin v3 API wrapper + prompt builder
│   ├── mock_devin.py     # DEMO_MODE scripted trajectories (same code path)
│   ├── verifier.py       # Independent CI-based verdict — the eval layer
│   ├── scanner.py        # Verified findings (+ live npm audit / pip-audit)
│   ├── github_client.py  # Issues, comments, PR CI status (+ mock)
│   ├── db.py             # SQLite state + leader-facing metrics
│   └── dashboard.py      # Server-rendered observability HTML
├── playbooks/remediation_v1.md   # The version-controlled SOP Devin executes
├── scripts/{create_issues,bootstrap_playbook}.py
├── .github/workflows/nightly-scan.yml   # the "event" in event-driven
├── docs/{ISSUES.md,ARCHITECTURE.md}
├── Dockerfile · docker-compose.yml · .env.example
└── pyproject.toml · uv.lock          # deps, lockfile, ruff + mypy config
```

## Development

Dependencies, the lockfile, and the ruff/mypy config all live in
[`pyproject.toml`](pyproject.toml); the Docker image builds from the same
`uv.lock`, so local and container installs are identical.

```bash
uv sync                 # install runtime + dev deps into .venv from the lockfile
uv run ruff check       # lint  (rich ruleset incl. PGH003 — the issue #3 gate)
uv run ruff format      # format
uv run mypy             # type-check (orchestrator/ is the flat-import source root)
```

Both `ruff check` and `mypy` are clean on the current tree.

## Next steps (in a real customer engagement)

1. Swap the custom scanner for Devin's Beta **Code Scans / Guardrail Violations**
   API as the event source; add Linear/Jira + Slack triage triggers.
2. Migrate simple triggers to Devin's **native Automations**; keep this
   orchestrator only for cross-run analytics, verification, and billing.
3. Harden: Postgres (swap `db.py`), auth/mTLS on endpoints, a
   `pull_request.closed` webhook to track *merges* (close the funnel to merged).
4. **Productize:** this repo becomes a template a partner SE reskins per customer
   — swap the repo URL, tune the Playbook to their coding standards, deploy. The
   Playbook + orchestrator + dashboard are the reusable IP.

_License: Apache-2.0._

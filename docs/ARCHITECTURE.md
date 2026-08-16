# Architecture

## Sequence: from finding to verified PR

```
Trigger        Orchestrator        Devin              GitHub
  │                │                 │                  │
  │─ scan ────────▶│                 │                  │
  │           scanner.py finds       │                  │
  │           grounded issues        │                  │
  │                │─ create issue ─────────────────────▶│
  │                │◀─ {number} ─────────────────────────│
  │                │─ create session ▶│                  │
  │                │  (prompt=playbook+issue, tags)      │
  │                │◀─ {session_id} ─│                  │
  │                │─ comment "session started" ────────▶│
  │                │                 │  Devin works:     │
  │                │                 │  reads the code,  │
  │                │                 │  edits N files,   │
  │                │                 │  writes/runs tests│
  │                │                 │  opens PR ───────▶│
  │           poller (interval)      │                  │
  │                │─ get_session ──▶│                  │
  │                │◀─ status,       │                  │
  │                │   structured_out│                  │
  │           verifier.evaluate(session, ci_state)       │
  │                │─ GET checks for PR head ───────────▶│   ← INDEPENDENT signal
  │                │◀─ success | failure | pending ──────│
  │                │                 │                  │
  │        ┌───────┴────────┬─────────────────┐          │
  │     verified         ci_failed         escalated     │
  │        │                │                 │          │
  │   label+comment    send_message      label+comment   │
  │   "✅ verified"    (correction)      "⚠️ needs-human" │
  │                    budget→fail                       │
```

## Why this shape

**Stateless workers, stateful orchestrator.** Devin sessions are ephemeral. The
orchestrator holds the durable workflow state that connects
`scan → issue → session → PR → verdict`. It's small *because* Devin does the hard
work — the code you'd delete last is Devin, not the glue.

**Two sources of truth, on purpose.** SQLite knows the *workflow* (which issue
spawned which session, which PR it opened, and the current verdict). Devin's
session metadata supplies ACU when available, and a production deployment can
reconcile that against billing exports. GitHub's Checks API is the *independent
verification* signal. We never let Devin grade its own homework.

**Verification is a separate module.** [`verifier.py`](../orchestrator/verifier.py)
takes a session + a CI state and returns one of `verified | ci_failed |
escalated | unverified`. This is the literal code embodiment of "separate patch
generated from task completed."

**Polling, not Devin→us webhooks.** Idempotent, survives restarts, trivial to
debug. Swaps to first-class completion webhooks in two lines when they ship.

## Failure modes & handling

| Failure | Detection | Handling |
|---|---|---|
| Devin API 5xx on create | `DevinAPIError` | Task → `failed`, logged, retried next scan |
| Session stops, no PR, no escalation | verifier -> `unverified` | Mark failed; never counted as success |
| PR opened but CI red | Checks API → `failure` | `send_message` correction, bounded by budget → `failed` |
| Devin escalates | `structured_output.status == needs_human` | Task → `escalated` (a *correct* outcome), labelled `needs-human` |
| Orchestrator restart | SQLite volume-mounted | State preserved; poller resumes; idempotency key prevents dup sessions |
| No credentials / demo | `DEMO_MODE=true` | Scripted trajectories through identical code path |

## What's mocked vs. real in DEMO_MODE

| Component | DEMO_MODE=true | DEMO_MODE=false |
|---|---|---|
| Orchestrator logic (scan→dispatch→poll→verify→correct→escalate) | **real** | real |
| Devin sessions | scripted trajectories | **real v3 API** |
| GitHub issues/PRs/CI | synthesized | **real API + Checks** |
| Dashboard & metrics | **real** (reads the real SQLite) | real |

The point: `DEMO_MODE` swaps *only the external backends*, never the logic being
evaluated.

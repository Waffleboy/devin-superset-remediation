"""
Event-driven Devin remediation orchestrator (FastAPI).

Flow (one closed loop):
    event -> scan -> file GitHub issue -> create Devin session
          -> poll -> INDEPENDENTLY VERIFY (CI) -> correct or escalate
          -> record verdict -> dashboard

The orchestrator is intentionally thin. Devin is the engine that does the
engineering work; this service only routes events, manages session lifecycle,
verifies results against an independent signal, and reports.

Triggers (all converge on the same path):
  - Scheduled scan     (APScheduler cron)          -> the "periodic" case
  - GitHub webhook     (issues.labeled devin-remediate)
  - Manual             (POST /api/scan, /api/trigger/{issue})
"""

from __future__ import annotations

import datetime
import hashlib
import hmac
import json
import logging
import os
import time
from contextlib import asynccontextmanager

import uvicorn
from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse

import db
import scanner
import verifier
from devin_client import (
    TERMINAL_STATUSES,
    DevinClient,
    build_remediation_prompt,
    consumption_total_acu,
    session_acu,
    session_pr_url,
    session_status,
    session_structured_output,
)
from github_client import GitHubClient, MockGitHubClient
from issue_format import issue_body, labels
from mock_devin import MockDevinClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s — %(message)s",
)
log = logging.getLogger("orchestrator")

DEMO_MODE = os.environ.get("DEMO_MODE", "false").lower() == "true"
GITHUB_REPO = os.environ.get("GITHUB_REPO", "local-demo/superset")
ENGAGEMENT = os.environ.get("ENGAGEMENT", "superset-secops")
WEBHOOK_SECRET = os.environ.get("GITHUB_WEBHOOK_SECRET", "")
REMEDIATE_LABEL = os.environ.get("REMEDIATE_LABEL", "devin-remediate")
SCAN_INTERVAL_HOURS = int(os.environ.get("SCAN_INTERVAL_HOURS", "24"))
POLL_INTERVAL_SECS = int(os.environ.get("POLL_INTERVAL_SECONDS", "5" if DEMO_MODE else "60"))
MAX_CORRECTION_ROUNDS = int(os.environ.get("MAX_CORRECTION_ROUNDS", "3"))
# How long we wait on a terminal session whose PR exists but whose CI never
# reports a conclusion, before escalating it for a human. Generous by default so
# a slow CI queue isn't mistaken for a stuck session.
VERIFY_TIMEOUT_SECS = int(os.environ.get("VERIFY_TIMEOUT_SECONDS", "3600"))
PORT = int(os.environ.get("PORT", "8000"))

# The Playbook is the version-controlled SOP we register with Devin
# (scripts/bootstrap_playbook.py) and attach to every session so Devin executes
# it rather than relying on the inline prompt alone. Title must match the script.
PLAYBOOK_TITLE = "Dependency & Security Remediation (v1)"
DEVIN_PLAYBOOK_ID = os.environ.get("DEVIN_PLAYBOOK_ID", "")
_playbook_id_cache: str | None = None

_HERE = os.path.dirname(os.path.abspath(__file__))
scheduler = BackgroundScheduler()


# --- client factories -------------------------------------------------------


def devin():
    return MockDevinClient() if DEMO_MODE else DevinClient()


def github():
    return MockGitHubClient() if DEMO_MODE else GitHubClient(repo=GITHUB_REPO)


# In DEMO_MODE we keep one Devin client so scripted session state persists.
_DEMO_DEVIN = MockDevinClient() if DEMO_MODE else None


def _devin_client():
    return _DEMO_DEVIN if DEMO_MODE else DevinClient()


def _resolve_playbook_id(client) -> str | None:
    """
    Find the id of the registered remediation Playbook so we can attach it to
    every session. Prefers an explicit DEVIN_PLAYBOOK_ID; otherwise looks it up
    by title once and caches the result. Returns None if it can't be resolved
    (the session still runs off the inline prompt — the Playbook just augments it).
    """
    global _playbook_id_cache
    if DEVIN_PLAYBOOK_ID:
        return DEVIN_PLAYBOOK_ID
    if _playbook_id_cache is not None:
        return _playbook_id_cache or None
    try:
        for pb in client.list_playbooks().get("playbooks", []):
            if pb.get("title") == PLAYBOOK_TITLE:
                _playbook_id_cache = pb.get("playbook_id", "")
                if _playbook_id_cache:
                    log.info("Attaching Playbook %s to sessions", _playbook_id_cache)
                return _playbook_id_cache or None
        log.warning(
            "Playbook %r not registered — run scripts/bootstrap_playbook.py. "
            "Sessions will use the inline prompt only.",
            PLAYBOOK_TITLE,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("Could not resolve Playbook id: %s", e)
    _playbook_id_cache = ""
    return None


# --- core: scan -> issue -> session -----------------------------------------


def run_scan_and_dispatch(subset: list[int] | None = None) -> dict:
    scan_id = db.create_scan()
    findings = scanner.run_scan(GITHUB_REPO, demo_mode=DEMO_MODE, subset=subset)
    gh = github()
    dispatched = []
    existing = {
        t["finding_id"]
        for t in db.list_tasks()
        if t["status"] in ("running", "verified", "escalated")
    }
    for f in findings:
        if f["id"] in existing:
            log.info("Finding %s already has an active/resolved task — skipping", f["id"])
            continue
        # Reuse an issue already filed for this finding (e.g. by
        # scripts/create_issues.py) instead of filing a duplicate.
        issue = gh.find_issue_by_title(f["title"])
        if issue:
            log.info("Reusing existing issue #%d for finding %s", issue["number"], f["id"])
        else:
            issue = gh.create_issue(
                title=f["title"],
                body=issue_body(f),
                labels=labels(f, REMEDIATE_LABEL),
            )
        task_id = db.create_task(f, issue["number"])
        _dispatch(task_id, f, issue["number"], gh)
        dispatched.append(issue["number"])
    db.finish_scan(scan_id, len(findings))
    gh.close()
    return {"scan_id": scan_id, "findings": len(findings), "dispatched": dispatched}


def _dispatch(
    task_id: int, finding: dict, issue_number: int, gh, prompt: str | None = None
) -> None:
    client = _devin_client()
    if prompt is None:
        prompt = build_remediation_prompt(
            issue_number, finding["title"], issue_body(finding), GITHUB_REPO
        )
    tags = [
        "cve-remediation",
        f"issue:{finding['id']}",
        f"severity:{finding['severity']}",
        f"kind:{finding['kind']}",
        f"engagement:{ENGAGEMENT}",  # billing attribution per customer/SOW
    ]
    try:
        sess = client.create_session(
            prompt=prompt,
            tags=tags,
            playbook_id=_resolve_playbook_id(client),
            idempotency_key=f"issue-{issue_number}-{GITHUB_REPO}",
        )
        db.start_task(task_id, sess["session_id"], sess["url"])
        gh.comment(
            issue_number,
            f"🤖 **Devin session started** — {sess['url']}\n\n"
            f"Devin is remediating this autonomously. A PR will follow, or Devin "
            f"will escalate with a rationale if the fix isn't safely achievable.",
        )
        log.info("Dispatched Devin %s for issue #%d", sess["session_id"], issue_number)
    except Exception as e:  # noqa: BLE001
        db.update_task(task_id, status="failed", finished_at=time.time(), detail=str(e))
        log.error("Dispatch failed for issue #%d: %s", issue_number, e)


def dispatch_issue(issue_number: int) -> dict:
    """
    Remediate a specific, already-filed GitHub issue (the webhook path). Unlike
    the scan path this does NOT invent a finding or file a new issue — it reads
    the labeled issue and hands its real title/body to Devin. Works for any
    human/tool-filed issue, not just the grounded set.
    """
    gh = github()
    try:
        issue = gh.get_issue(issue_number)
    except Exception as e:  # noqa: BLE001
        log.error("Could not fetch issue #%d: %s", issue_number, e)
        gh.close()
        return {"status": "error", "issue": issue_number, "detail": str(e)}

    active = {
        t["issue_number"]
        for t in db.list_tasks()
        if t["status"] in ("running", "verified", "escalated")
    }
    if issue_number in active:
        log.info("Issue #%d already has an active/resolved task — skipping", issue_number)
        gh.close()
        return {"status": "already_active", "issue": issue_number}

    finding = {
        "id": issue_number,
        "title": issue.get("title", f"Issue #{issue_number}"),
        "severity": "medium",
        "kind": "webhook",
    }
    prompt = build_remediation_prompt(
        issue_number, finding["title"], issue.get("body") or "", GITHUB_REPO
    )
    task_id = db.create_task(finding, issue_number)
    _dispatch(task_id, finding, issue_number, gh, prompt=prompt)
    gh.close()
    return {"status": "dispatched", "issue": issue_number}


# --- poll + verify (the differentiator) -------------------------------------


def poll_running() -> None:
    tasks = db.list_running_tasks()
    if not tasks:
        return
    client = _devin_client()
    gh = github()
    for t in tasks:
        try:
            _poll_one(t, client, gh)
        except Exception as e:  # noqa: BLE001
            log.error("Poll error task %s: %s", t["id"], e)
    gh.close()


def _poll_one(task: dict, client, gh) -> None:
    sess = client.get_session(task["devin_session"])
    status = session_status(sess)
    log.info("Session %s -> %s", task["devin_session"], status)

    # Independent CI signal. In DEMO_MODE the mock supplies `_ci`; in real mode
    # we read the GitHub Checks API for the PR head.
    so = session_structured_output(sess)
    pr_url = session_pr_url(sess)
    if pr_url and not so.get("pr_url"):
        so["pr_url"] = pr_url
        sess["structured_output"] = so

    claimed = str(so.get("status", "")).lower()
    if (
        status not in TERMINAL_STATUSES
        and not pr_url
        and claimed not in {"succeeded", "needs_human", "failed"}
    ):
        db.update_task(task["id"], acu=session_acu(sess, task.get("acu") or 0))
        return  # still working

    ci_state = sess.get("_ci") if DEMO_MODE else (gh.pr_ci_state(pr_url) if pr_url else None)

    verdict = verifier.evaluate(sess, ci_state)
    acu = session_acu(sess, task.get("acu") or 0)

    if verdict.outcome == "verified":
        db.update_task(
            task["id"],
            status="verified",
            pr_url=verdict.pr_url,
            ci_state="success",
            acu=acu,
            finished_at=time.time(),
            detail=verdict.detail,
        )
        gh.add_labels(task["issue_number"], ["devin-verified"])
        gh.comment(
            task["issue_number"],
            f"✅ **Verified fix.** PR {verdict.pr_url} — CI green. Ready for human merge.",
        )
        log.info("Task %s VERIFIED (%s)", task["id"], verdict.pr_url)

    elif verdict.outcome == "escalated":
        db.update_task(
            task["id"], status="escalated", acu=acu, finished_at=time.time(), detail=verdict.detail
        )
        gh.add_labels(task["issue_number"], ["devin-escalated", "needs-human"])
        gh.comment(
            task["issue_number"],
            f"⚠️ **Escalated for human review.** Devin determined the fix isn't "
            f"safely achievable:\n\n> {verdict.detail}",
        )
        log.info("Task %s ESCALATED", task["id"])

    elif verdict.outcome == "ci_failed":
        rounds = (task["correction_rounds"] or 0) + 1
        if rounds <= MAX_CORRECTION_ROUNDS:
            client.send_message(
                task["devin_session"],
                "Your PR's CI is failing. Read the failing checks, fix the root "
                "cause, push, and confirm CI is green. Do not open a new PR.",
            )
            db.update_task(
                task["id"], status="running", correction_rounds=rounds, ci_state="failure", acu=acu
            )
            log.info("Task %s CI red -> correction round %d", task["id"], rounds)
        else:
            db.update_task(
                task["id"],
                status="failed",
                ci_state="failure",
                acu=acu,
                finished_at=time.time(),
                detail="Exhausted correction budget with red CI.",
            )
            gh.add_labels(task["issue_number"], ["devin-failed", "needs-human"])
            log.warning("Task %s FAILED after %d corrections", task["id"], rounds)

    else:  # unverified
        now = time.time()
        started = task.get("started_at") or now
        if status in TERMINAL_STATUSES and not pr_url:
            # Session ended with no PR and no escalation rationale — it's dead,
            # not in-progress. Don't poll a terminal session forever.
            db.update_task(
                task["id"],
                status="failed",
                acu=acu,
                finished_at=now,
                detail="Session ended without a PR or an escalation rationale.",
            )
            gh.add_labels(task["issue_number"], ["devin-failed", "needs-human"])
            log.warning("Task %s FAILED — terminal session, no PR/escalation", task["id"])
        elif status in TERMINAL_STATUSES and (now - started) > VERIFY_TIMEOUT_SECS:
            # PR exists but CI never reported a conclusion within the budget.
            db.update_task(
                task["id"],
                status="escalated",
                acu=acu,
                finished_at=now,
                ci_state=ci_state or "pending",
                detail="PR opened but CI never reported a conclusion within the verify timeout.",
            )
            gh.add_labels(task["issue_number"], ["devin-escalated", "needs-human"])
            log.warning("Task %s ESCALATED — CI never reported within timeout", task["id"])
        elif pr_url:
            # PR opened and CI legitimately pending — keep polling.
            db.update_task(task["id"], acu=acu, ci_state=ci_state or "pending")
        else:
            # Still working, no PR yet — keep polling.
            db.update_task(task["id"], acu=acu)


# --- metrics: canonical cost from Devin, not our own tally ------------------


def _canonical_acu() -> float | None:
    """
    Total ACU spend pulled from Devin's own Consumption API — the billed source
    of truth for cost. We still record per-session ACU in SQLite (for the per-row
    breakdown and as a fallback), but the headline economics should reconcile
    against Devin's ledger, not our local sum. Returns None if the API is
    unreachable or reports nothing, so the caller falls back to SQLite.
    """
    tasks = db.list_tasks()
    if not tasks:
        return None
    starts = [t["created_at"] for t in tasks if t.get("created_at")]
    window_start = min(starts) if starts else time.time()
    start_date = datetime.date.fromtimestamp(window_start).isoformat()
    end_date = datetime.date.fromtimestamp(time.time()).isoformat()
    client = _devin_client()
    try:
        payload = client.get_daily_consumption(start_date, end_date)
    except Exception as e:  # noqa: BLE001
        log.warning("Canonical ACU fetch failed — falling back to SQLite tally: %s", e)
        return None
    return consumption_total_acu(payload)


def current_metrics() -> dict:
    """
    Dashboard metrics with cost reconciled against Devin's Consumption API. The
    resolution/PR/MTTR figures come from our workflow state (SQLite); the ACU
    economics prefer Devin's billed total when available. `acu_source` records
    which one won so the dashboard can label it honestly.
    """
    m = db.get_metrics()
    canonical = _canonical_acu()
    if canonical is not None and canonical > 0:
        m["total_acu"] = round(canonical, 1)
        m["acu_per_pr"] = round(canonical / m["prs_opened"], 2) if m["prs_opened"] else 0.0
        m["acu_source"] = "devin"
    else:
        m["acu_source"] = "sqlite"
    return m


# --- FastAPI ----------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    if not DEMO_MODE:
        # Verify Devin credentials before we start burning ACU.
        try:
            who = DevinClient().whoami()
            log.info(
                "Devin auth OK: %s",
                who.get("service_user_name") or who.get("principal_type") or "ok",
            )
        except Exception as e:  # noqa: BLE001
            log.error("Devin credential check failed — sessions will error: %s", e)
    scheduler.add_job(
        lambda: run_scan_and_dispatch(),
        "interval",
        hours=SCAN_INTERVAL_HOURS,
        id="scan",
        replace_existing=True,
    )
    scheduler.add_job(
        poll_running, "interval", seconds=POLL_INTERVAL_SECS, id="poll", replace_existing=True
    )
    scheduler.start()
    log.info(
        "Started. DEMO_MODE=%s scan=%dh poll=%ds",
        DEMO_MODE,
        SCAN_INTERVAL_HOURS,
        POLL_INTERVAL_SECS,
    )
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="Devin Superset Remediation", version="1.0.0", lifespan=lifespan)


@app.get("/healthz")
def healthz():
    return {"status": "ok", "demo_mode": DEMO_MODE, "repo": GITHUB_REPO}


@app.get("/api/metrics")
def metrics():
    return current_metrics()


@app.get("/api/tasks")
def tasks():
    return db.list_tasks()


@app.post("/api/scan")
def scan(background_tasks: BackgroundTasks, subset: str | None = None):
    ids = [int(x) for x in subset.split(",")] if subset else None
    background_tasks.add_task(run_scan_and_dispatch, ids)
    return {"status": "scan_started", "subset": ids}


@app.post("/api/trigger/{issue_number}")
def trigger(issue_number: int):
    # Re-run the finding whose id matches this issue (demo convenience).
    return run_scan_and_dispatch(subset=[issue_number])


@app.post("/webhook/github")
async def webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_hub_signature_256: str | None = Header(None),
    x_github_event: str | None = Header(None),
):
    body = await request.body()
    if WEBHOOK_SECRET:
        expected = "sha256=" + hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
        if not x_hub_signature_256 or not hmac.compare_digest(expected, x_hub_signature_256):
            raise HTTPException(401, "Invalid signature")
    payload = json.loads(body or b"{}")
    if (
        x_github_event == "issues"
        and payload.get("action") == "labeled"
        and payload.get("label", {}).get("name") == REMEDIATE_LABEL
    ):
        num = payload["issue"]["number"]
        # Remediate the issue that was labeled — read its real title/body and
        # dispatch Devin against it, rather than re-scanning or filing a new one.
        background_tasks.add_task(dispatch_issue, num)
        return {"status": "dispatched", "issue": num}
    return {"status": "ignored"}


@app.get("/", response_class=HTMLResponse)
def dashboard():
    return _render_dashboard(current_metrics(), db.list_tasks())


def _render_dashboard(m: dict, tasks: list[dict]) -> str:
    # Import here to keep module import cheap; template is pure-python string.
    from dashboard import render  # noqa: PLC0415

    return render(m, tasks, repo=GITHUB_REPO, demo=DEMO_MODE)


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=PORT, reload=False)

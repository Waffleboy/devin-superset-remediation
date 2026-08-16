"""
MockDevinClient — a drop-in for DEMO_MODE.

Why this exists: the assignment says "focus on a working end-to-end demo".
A live Devin run against a real fork takes many minutes and needs credentials.
For a reliable 5-minute demo and for evaluators who don't have keys, the mock
replays *recorded, realistic* session trajectories through the IDENTICAL
orchestrator code path — same create/poll/verify/correct/escalate logic, same
dashboard. Nothing about the orchestrator changes; only the API backend swaps.

Crucially, the mock reproduces the three behaviors that matter:
  - a clean success (fast, cheap),
  - a success that FAILS CI on the first try and self-corrects,
  - a task Devin correctly ESCALATES instead of forcing a broken fix.
"""

from __future__ import annotations

import copy
import logging
import os
from typing import Any

log = logging.getLogger("devin.mock")

# Per-issue scripted trajectories keyed by the issue number embedded in tags.
# Each entry: list of (status, extra) states returned on successive polls.
_TRAJECTORIES: dict[int, list[tuple[str, dict]]] = {
    # Issue 1 — cum.py cumulative min/max/prod bug. The HERO: a correctness bug
    # Devin diagnoses, fixes, and covers with a regression test.
    1: [
        ("running", {}),
        ("running", {}),
        (
            "stopped",
            {
                "structured_output": {
                    "status": "succeeded",
                    "pr_url": "__MOCK_PR_101__",
                    "files_changed": 2,
                    "summary": "Guard fillna(0) to cumsum only; fix cumulative min/max/prod + regression test",
                    "verification": "cummin/cummax/cumprod now correct on gap data ([3,NaN,2,5] -> [3,NaN,2,2]); new test_cum.py cases pass; existing postprocessing tests green",
                },
                "acu": 4.2,
                "_ci": "success",
            },
        ),
    ],
    # Issue 2 — dompurify CVE bump. Trivial fast path.
    2: [
        ("running", {}),
        (
            "stopped",
            {
                "structured_output": {
                    "status": "succeeded",
                    "pr_url": "__MOCK_PR_102__",
                    "files_changed": 2,
                    "summary": "Bump dompurify ^3.4.11 -> ^3.4.13 (GHSA-55q2-fjhq-7xh7)",
                    "verification": "npm audit clean for GHSA-55q2-fjhq-7xh7; lockfile updated; SafeMarkdown Jest tests pass; frontend build ok",
                },
                "acu": 1.1,
                "_ci": "success",
            },
        ),
    ],
    # Issue 3 — type-ignore sweep + enable ruff PGH003. Enabling the gate turns
    # CI red on the hidden type errors; Devin self-corrects to green.
    3: [
        ("running", {}),
        (
            "stopped",
            {  # first stop: PR opened but PGH003/mypy will be red
                "structured_output": {
                    "status": "succeeded",
                    "pr_url": "__MOCK_PR_103__",
                    "files_changed": 5,
                    "summary": "Replace blanket # type: ignore with coded ignores in superset/sql/; enable ruff PGH003",
                    "verification": "ruff PGH003 passes locally",
                },
                "acu": 2.1,
                "_ci": "failure",  # verifier will see red CI (mypy) and send it back
            },
        ),
        ("running", {}),  # after correction message
        (
            "stopped",
            {  # second stop: CI now green
                "structured_output": {
                    "status": "succeeded",
                    "pr_url": "__MOCK_PR_103__",
                    "files_changed": 7,
                    "summary": "Coded ignores + PGH003; fix the 4 real type errors the blanket ignores were hiding",
                    "verification": "mypy surfaced 4 hidden errors once blanket ignores were removed; all fixed; ruff PGH003 + mypy clean; CI green",
                },
                "acu": 3.4,
                "_ci": "success",
            },
        ),
    ],
    # Issue 4 — deck.gl/loaders.gl DoS chain. Devin correctly REFUSES and escalates.
    4: [
        ("running", {}),
        ("running", {}),
        (
            "blocked",
            {
                "structured_output": {
                    "status": "needs_human",
                    "pr_url": None,
                    "files_changed": 0,
                    "summary": "deck.gl/loaders.gl DoS advisory: no safe upgrade path",
                    "verification": "npm audit's only offered fix is a major downgrade of @deck.gl/* and loaders.gl that breaks the map visualisations. Recommend a tracked staged upgrade instead. Escalating rather than shipping a breaking change.",
                },
                "acu": 3.6,
            },
        ),
    ],
    # Issue 5 — apache/superset#42704 (🦾 ai-candidate). Narrow app bug + test.
    5: [
        ("running", {}),
        ("running", {}),
        (
            "stopped",
            {
                "structured_output": {
                    "status": "succeeded",
                    "pr_url": "__MOCK_PR_105__",
                    "files_changed": 2,
                    "summary": "Fix blank Metric Warning text after save (#42704) — align warning_text / extra.warning_markdown mapping",
                    "verification": "Added unit test asserting the warning text renders after save in Edit Dataset from Explore; fails without the fix, passes with it",
                },
                "acu": 4.8,
                "_ci": "success",
            },
        ),
    ],
}


def _issue_from_tags(tags: list[str]) -> int:
    for t in tags or []:
        if t.startswith("issue:"):
            try:
                return int(t.split(":", 1)[1])
            except ValueError:
                pass
    return 0


def _repo() -> str:
    return os.environ.get("GITHUB_REPO", "local-demo/superset")


def _mock_url(token: str) -> str:
    number = token.removeprefix("__MOCK_PR_").removesuffix("__")
    return f"https://github.com/{_repo()}/pull/{number}"


def _hydrate_urls(value: Any) -> Any:
    if isinstance(value, str) and value.startswith("__MOCK_PR_"):
        return _mock_url(value)
    if isinstance(value, dict):
        return {k: _hydrate_urls(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_hydrate_urls(v) for v in value]
    return value


class MockDevinClient:
    """Same public surface as DevinClient, backed by scripted trajectories."""

    def __init__(self, *_, **__) -> None:
        self.org_id = "mock-org"
        self._counter = 0
        self._sessions: dict[str, dict[str, Any]] = {}

    def whoami(self) -> dict[str, Any]:
        return {"principal_type": "service_user", "service_user_name": "MockBot"}

    def create_session(
        self,
        prompt: str,
        tags: list[str] | None = None,
        playbook_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        self._counter += 1
        sid = f"devin-mock-{self._counter:03d}"
        issue = _issue_from_tags(tags or [])
        self._sessions[sid] = {
            "session_id": sid,
            "url": f"https://app.devin.ai/sessions/{sid}",
            "status": "running",
            "tags": tags or [],
            "issue": issue,
            "poll": 0,
        }
        log.info("MOCK created session %s (issue %s)", sid, issue)
        return dict(self._sessions[sid])

    def get_session(self, session_id: str) -> dict[str, Any]:
        s = self._sessions[session_id]
        traj = _TRAJECTORIES.get(s["issue"], [("stopped", {})])
        idx = min(s["poll"], len(traj) - 1)
        status, extra = traj[idx]
        s["poll"] += 1
        s["status"] = status
        out = dict(s)
        out.update(_hydrate_urls(copy.deepcopy(extra)))
        # Accrue ACU on the session so get_daily_consumption can report the same
        # canonical spend the billing API would — this is what the dashboard's
        # cost tiles reconcile against.
        if out.get("acu") is not None:
            s["acu"] = out["acu"]
        return out

    def send_message(self, session_id: str, message: str) -> dict[str, Any]:
        # No-op beyond logging: the scripted trajectory already contains the
        # post-correction "running" -> "stopped (green)" retry states, which the
        # next get_session polls advance into.
        log.info("MOCK correction -> session %s", session_id)
        return {"ok": True}

    def create_playbook(self, title: str, body: str) -> dict[str, Any]:
        return {"playbook_id": "pb-mock-001", "title": title}

    def list_playbooks(self) -> dict[str, Any]:
        return {"playbooks": []}

    def get_session_metrics(self, start_date: str, end_date: str) -> dict[str, Any]:
        done = [s for s in self._sessions.values() if s["status"] in ("stopped", "blocked")]
        return {"total_sessions": len(self._sessions), "completed_sessions": len(done)}

    def get_daily_consumption(self, start_date: str, end_date: str) -> dict[str, Any]:
        # Canonical billed ACU = spend accrued across every session so far. Shaped
        # like the real /consumption/daily response (top-line total + daily rows).
        total = round(sum(float(s.get("acu") or 0) for s in self._sessions.values()), 1)
        return {
            "start_date": start_date,
            "end_date": end_date,
            "total_acu": total,
            "daily": [{"date": end_date, "acu": total}],
        }

    def close(self) -> None:
        pass

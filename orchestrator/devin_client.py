"""
Thin wrapper around the Devin v3 Organization API.

Deliberately small. The whole thesis of this project is that Devin is the
*engine* — the primitive that does the actual engineering work. Our job is to
glue events to sessions and verify the result, not to re-implement what Devin
already provides (planning, code editing, running tests, opening PRs).

Docs: https://docs.devin.ai/api-reference/overview
Base: https://api.devin.ai/v3/organizations/{org_id}/...
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx

log = logging.getLogger("devin")

DEVIN_BASE_URL = os.environ.get("DEVIN_BASE_URL", "https://api.devin.ai/v3")

# Devin session lifecycle. A session that has stopped may or may not have
# produced a PR — that is exactly why we verify independently (see verifier.py)
# rather than trusting the session's own status.
TERMINAL_STATUSES = {
    "blocked",
    "completed",
    "done",
    "expired",
    "failed",
    "finished",
    "needs_human",
    "stopped",
    "succeeded",
    "terminated",
}


class DevinAPIError(Exception):
    """Raised on any non-2xx from the Devin API."""


class DevinClient:
    """Minimal client for the Devin v3 Organization API (real backend)."""

    def __init__(
        self,
        api_key: str | None = None,
        org_id: str | None = None,
        base_url: str = DEVIN_BASE_URL,
        timeout: float = 60.0,
    ) -> None:
        self.api_key = api_key or os.environ["DEVIN_API_KEY"]
        self.org_id = org_id or os.environ["DEVIN_ORG_ID"]
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )

    # ---- internal ----------------------------------------------------------

    def _org_url(self, path: str) -> str:
        return f"{self.base_url}/organizations/{self.org_id}/{path.lstrip('/')}"

    def _request(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        try:
            r = self._client.request(method, url, **kwargs)
        except httpx.HTTPError as exc:
            raise DevinAPIError(f"Network error talking to Devin: {exc}") from exc
        if r.status_code >= 400:
            raise DevinAPIError(f"Devin {method} {url} -> {r.status_code}: {r.text[:400]}")
        return r.json() if r.content else {}

    # ---- auth --------------------------------------------------------------

    def whoami(self) -> dict[str, Any]:
        """Verify credentials before we start burning ACU."""
        return self._request("GET", f"{self.base_url}/self")

    # ---- sessions (the primitive) -----------------------------------------

    def create_session(
        self,
        prompt: str,
        tags: list[str] | None = None,
        playbook_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """
        Create a Devin session. Tags are the contract: every session is tagged
        with the issue number, severity and engagement so the dashboard can
        slice by any dimension and a partner can attribute ACU to a customer/SOW.
        """
        body: dict[str, Any] = {"prompt": prompt}
        if tags:
            body["tags"] = tags
        if playbook_id:
            body["playbook_id"] = playbook_id
        if idempotency_key:
            body["idempotency_key"] = idempotency_key
        data = self._request("POST", self._org_url("sessions"), json=body)
        sid = data.get("session_id") or data.get("devin_id") or data.get("id") or ""
        data["session_id"] = sid
        data.setdefault(
            "url",
            data.get("web_url") or data.get("app_url") or f"https://app.devin.ai/sessions/{sid}",
        )
        log.info("Created Devin session %s", sid)
        return data

    def get_session(self, session_id: str) -> dict[str, Any]:
        return self._request("GET", self._org_url(f"sessions/{session_id}"))

    def send_message(self, session_id: str, message: str) -> dict[str, Any]:
        """Used by the self-correction loop to feed CI failures back to Devin."""
        return self._request(
            "POST",
            self._org_url(f"sessions/{session_id}/messages"),
            json={"message": message},
        )

    def list_sessions(self, limit: int = 50) -> dict[str, Any]:
        return self._request("GET", self._org_url("sessions"), params={"limit": limit})

    # ---- playbooks (the version-controlled asset) --------------------------

    def create_playbook(self, title: str, body: str) -> dict[str, Any]:
        return self._request(
            "POST", self._org_url("playbooks"), json={"title": title, "body": body}
        )

    def list_playbooks(self) -> dict[str, Any]:
        return self._request("GET", self._org_url("playbooks"))

    # ---- automations (Devin's native event primitive) ---------------------
    # We use an Automation for the *trigger* only (a red CI check kicks off a
    # fix session). Verification, the correction budget, escalation and cost
    # tracking stay in the orchestrator — an Automation can't do those.

    def create_automation(self, body: dict[str, Any]) -> dict[str, Any]:
        """POST a native Automation (event trigger -> action)."""
        return self._request("POST", self._org_url("automations"), json=body)

    def list_automations(self) -> dict[str, Any]:
        return self._request("GET", self._org_url("automations"))

    # ---- analytics: pulled from Devin, not re-implemented ------------------

    def get_session_metrics(self, start_date: str, end_date: str) -> dict[str, Any]:
        return self._request(
            "GET",
            self._org_url("metrics/sessions"),
            params={"start_date": start_date, "end_date": end_date},
        )

    def get_daily_consumption(self, start_date: str, end_date: str) -> dict[str, Any]:
        """ACU spend — the raw material for cost-per-fix economics."""
        return self._request(
            "GET",
            self._org_url("consumption/daily"),
            params={"start_date": start_date, "end_date": end_date},
        )

    def close(self) -> None:
        self._client.close()


# --------------------------------------------------------------------------
# Prompt builder — this is where we hand Devin a bounded, verifiable task.
# --------------------------------------------------------------------------


def build_remediation_prompt(
    issue_number: int, issue_title: str, issue_body: str, repo: str
) -> str:
    owner, name = repo.split("/", 1)
    return f"""You are an autonomous software engineer working on a fork of Apache Superset at https://github.com/{owner}/{name}.

## GitHub issue #{issue_number}: {issue_title}

{issue_body}

## Your task
1. Clone the repo and create a branch `devin/issue-{issue_number}`.
2. Reproduce / understand the root cause described in the issue. Read any
   upstream migration guides or changelogs relevant to the fix.
3. Implement the MINIMUM-SCOPE fix. Do not make unrelated changes. If a
   dependency upgrade has cascading effects (removed imports, moved symbols),
   handle every one of them so the app still imports and tests still run.
4. Verify locally before opening a PR:
   - `pip install -e .` (or the relevant requirements) must succeed.
   - The affected modules must import without error.
   - Run the narrowest relevant test slice you can.
5. If — and only if — you determine the fix is NOT safely achievable (e.g. the
   ecosystem does not yet support the target version), DO NOT force a broken
   change. Instead, write up your findings and set status to "needs_human".
6. If the fix is achievable: commit, push, and open a PR into `master` whose
   body includes a change summary, the verification you ran, and `Closes #{issue_number}`.
7. Post the PR URL (or your escalation writeup) as a comment on issue #{issue_number}.

## Return a JSON object as your final structured output, exactly:
{{
  "status": "succeeded" | "needs_human" | "failed",
  "pr_url": "<url or null>",
  "files_changed": <int>,
  "summary": "<one sentence>",
  "verification": "<what you ran to prove the fix works, or why you escalated>",
  "issue_number": {issue_number}
}}
"""


def session_structured_output(session: dict[str, Any]) -> dict[str, Any]:
    """Return structured output from likely Devin API field names."""
    raw = (
        session.get("structured_output")
        or session.get("validated_structured_output")
        or session.get("structuredOutput")
    )
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def session_pr_url(session: dict[str, Any]) -> str | None:
    """Extract a PR URL from structured output or Devin's PR metadata."""
    so = session_structured_output(session)
    for key in ("pr_url", "pull_request_url", "pullRequestUrl"):
        if so.get(key):
            return str(so[key])
        if session.get(key):
            return str(session[key])

    prs = session.get("pull_requests") or session.get("pullRequests") or []
    if isinstance(prs, dict):
        prs = [prs]
    for pr in prs:
        if not isinstance(pr, dict):
            continue
        for key in ("html_url", "url", "web_url", "permalink"):
            if pr.get(key):
                return str(pr[key])
    return None


def session_status(session: dict[str, Any]) -> str:
    """
    Normalize session lifecycle fields across mock and real API responses.

    If the API omits a lifecycle field but already has final structured output,
    treat the session as ready for verification instead of polling forever.
    """
    for key in ("status", "state", "lifecycle_status", "lifecycleStatus"):
        if session.get(key):
            return str(session[key]).lower()

    claimed = str(session_structured_output(session).get("status", "")).lower()
    if claimed in {"succeeded", "needs_human", "failed"}:
        return claimed
    if session_pr_url(session):
        return "finished"
    return "running"


def session_acu(session: dict[str, Any], fallback: float = 0) -> float:
    """Read ACU spend from the mock shape or common real API field names."""
    for key in ("acu", "acus_consumed", "acusConsumed", "total_acu", "totalAcus"):
        value = session.get(key)
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                pass
    consumption = session.get("consumption")
    if isinstance(consumption, dict):
        for key in ("acu", "acus", "total_acu", "totalAcus"):
            value = consumption.get(key)
            if value is not None:
                try:
                    return float(value)
                except (TypeError, ValueError):
                    pass
    return float(fallback or 0)


def _first_acu_field(obj: dict[str, Any]) -> float | None:
    for key in ("total_acu", "totalAcus", "total_acus", "acu", "acus", "acus_consumed"):
        value = obj.get(key)
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                pass
    return None


def consumption_total_acu(payload: dict[str, Any]) -> float | None:
    """
    Extract total ACU from a Devin ``consumption/daily`` response — the canonical,
    billed cost figure, independent of whatever we tallied per-session locally.

    Handles both a top-level total and a list of per-day/per-session rows, across
    the field names the real API and the mock use. Returns None if nothing is
    parseable so the caller can fall back to the local SQLite tally.
    """
    if not isinstance(payload, dict):
        return None
    top = _first_acu_field(payload)
    if top is not None:
        return round(top, 2)
    for key in ("days", "daily", "consumption", "sessions", "data", "results", "items"):
        rows = payload.get(key)
        if isinstance(rows, list):
            values = [_first_acu_field(r) for r in rows if isinstance(r, dict)]
            present = [v for v in values if v is not None]
            if present:
                return round(sum(present), 2)
    return None

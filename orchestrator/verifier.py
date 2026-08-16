"""
Independent verification — the heart of "eval over demonstration".

Devin reports its own status in structured output. We DO NOT trust that alone.
A task is only "verified" when an *independent* signal confirms it:

  - For a fix that opened a PR: the PR's CI checks must be green.
  - For an escalation: Devin must have written a rationale AND opened no PR.

This is the difference between "a patch was generated" and "the task was
completed" — the distinction Cognition explicitly grades on.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger("verifier")


@dataclass
class Verdict:
    outcome: str  # "verified" | "ci_failed" | "escalated" | "unverified"
    pr_url: str | None
    ci_state: str | None  # "success" | "failure" | "pending" | None
    detail: str


def _structured(session: dict) -> dict:
    so = (
        session.get("structured_output")
        or session.get("validated_structured_output")
        or session.get("structuredOutput")
    )
    return so if isinstance(so, dict) else {}


def evaluate(session: dict, ci_state: str | None) -> Verdict:
    """
    Combine Devin's self-report with an independent CI signal into a verdict.

    `ci_state` comes from the GitHub Checks API for the PR head (github_client),
    or from the mock's scripted `_ci` field in DEMO_MODE.
    """
    so = _structured(session)
    claimed = so.get("status", "")
    pr_url = so.get("pr_url")

    # Devin decided the task isn't safely achievable and escalated. That is a
    # SUCCESSFUL outcome for the system — bounded autonomy working as designed.
    if claimed == "needs_human" and not pr_url:
        return Verdict(
            outcome="escalated",
            pr_url=None,
            ci_state=None,
            detail=so.get("verification", "Escalated for human review."),
        )

    # Devin claims success. Trust, but verify against CI.
    if pr_url:
        if ci_state == "success":
            return Verdict("verified", pr_url, "success", so.get("verification", "CI green."))
        if ci_state == "failure":
            return Verdict(
                "ci_failed",
                pr_url,
                "failure",
                "PR opened but CI is red — sending correction to Devin.",
            )
        # No CI signal yet — keep polling, don't declare victory.
        return Verdict("unverified", pr_url, ci_state or "pending", "PR opened; awaiting CI.")

    # Devin stopped without a PR and without escalating — treat as unverified.
    return Verdict(
        "unverified", None, ci_state, "Session ended without a PR or an escalation rationale."
    )

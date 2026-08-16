#!/usr/bin/env python3
"""
Register the native Devin Automation that closes the CI self-correction loop
(idempotent).

This is the hybrid design. Devin's Automations API is its *own* event
primitive, so we use it for exactly one thing: the trigger. When the
`remediation-verify` check goes red on a remediation PR, this Automation starts
a Devin session on the SAME branch to fix it — a real red -> green, driven by
the Devin platform rather than our poller.

Everything that makes the system trustworthy still lives in the orchestrator,
because an Automation cannot do it:
  - Automation   = the event trigger (Devin-native).
  - Orchestrator = independent CI verification, correction budget, escalation,
                   per-engagement cost tracking.

Prerequisites (set in Devin, not here) for the Automation to actually FIRE:
  - Devin's GitHub app is connected to the repo (Settings -> Connections).
  - Automation scope includes this repo. Public forks need the scope set to
    "All installed repos" (Waffleboy/superset is public) — enable deliberately.

    DEVIN_API_KEY=... DEVIN_ORG_ID=... GITHUB_REPO=you/superset \
      python scripts/bootstrap_automation.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "orchestrator"))

from devin_client import DevinAPIError, DevinClient

NAME = "Superset remediation — CI self-correction"
REPO = os.environ.get("GITHUB_REPO", "your-org/superset")
CHECK_NAME = os.environ.get("VERIFY_CHECK_NAME", "remediation-verify")
ENGAGEMENT = os.environ.get("ENGAGEMENT", "superset-secops")
# Org / service-user API keys must run as the organization; a personal token
# can use "creator". Override with AUTOMATION_RUN_AS if the POST is rejected.
RUN_AS = os.environ.get("AUTOMATION_RUN_AS", "organization")


def build_body() -> dict:
    prompt = (
        f"The GitHub check `{CHECK_NAME}` just failed on a remediation pull "
        f"request in @{REPO}. The full check-run event payload is appended "
        "below — it identifies the pull request, its head branch, and the "
        "failing check.\n\n"
        "Your task:\n"
        "1. Check out the PR's existing head branch. Do NOT open a new PR or a "
        "new branch — push to the same branch so the open PR re-runs.\n"
        "2. Read the failing check's logs and find the exact cause.\n"
        "3. Implement the minimum fix and push it to that branch so "
        f"`{CHECK_NAME}` runs again and goes green.\n"
        "4. If the failure cannot be fixed safely, comment on the PR explaining "
        "why and stop — do not force a broken change.\n"
    )
    return {
        "name": NAME,
        "run_as": {"type": RUN_AS},
        "triggers": [
            {
                "event_type": "github:check_run",
                # Two-level DNF envelope. Field names come from the live schemas
                # endpoint (GET .../automations/schemas). `repository.full_name`
                # is REQUIRED for check_run triggers. We scope tightly: our repo,
                # our verify check, a failing conclusion, on a devin/* branch.
                "conditions": {
                    "any": [
                        {
                            "all": [
                                {"field": "repository.full_name", "operator": "eq", "value": REPO},
                                {
                                    "field": "check_run.conclusion",
                                    "operator": "eq",
                                    "value": "failure",
                                },
                                {"field": "check_run.name", "operator": "eq", "value": CHECK_NAME},
                                {
                                    "field": "check_run.check_suite.head_branch",
                                    "operator": "starts_with",
                                    "value": "devin/",
                                },
                            ]
                        }
                    ]
                },
            }
        ],
        "actions": [
            {
                "type": "start_session",
                # @owner/repo is how the action binds to a repo — `session.repos`
                # is read-only and derived from this token.
                "prompt": prompt,
                "session": {
                    "tags": ["ci-self-correction", f"engagement:{ENGAGEMENT}"],
                },
            }
        ],
    }


def main() -> None:
    client = DevinClient()

    # Idempotent: skip if an automation with this name already exists.
    try:
        existing = client.list_automations()
        items = (
            existing.get("automations")
            or existing.get("items")
            or (existing if isinstance(existing, list) else [])
        )
        for a in items:
            if isinstance(a, dict) and a.get("name") == NAME:
                print(f"Automation already registered: {a.get('automation_id')}")
                return
    except DevinAPIError as exc:
        # If listing isn't available, fall through and attempt to create.
        print(f"(could not list automations, will attempt create): {exc}")

    created = client.create_automation(build_body())
    print(f"Registered automation: {created.get('automation_id')}")
    print(
        "Reminder: it only FIRES if Devin's GitHub app is connected to "
        f"{REPO} and the automation scope covers it (public repos need "
        "scope = 'All installed repos')."
    )


if __name__ == "__main__":
    main()

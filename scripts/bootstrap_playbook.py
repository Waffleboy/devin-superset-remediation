#!/usr/bin/env python3
"""
Sync the version-controlled Playbook into Devin (idempotent).

The Playbook lives in Git (playbooks/remediation_v1.md) as the source of truth;
Devin is the runtime. Run this once per environment / after editing the Playbook.

    DEVIN_API_KEY=... DEVIN_ORG_ID=... python scripts/bootstrap_playbook.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "orchestrator"))

from devin_client import DevinClient

PLAYBOOK = os.path.join(os.path.dirname(__file__), "..", "playbooks", "remediation_v1.md")
TITLE = "Dependency & Security Remediation (v1)"


def main() -> None:
    with open(PLAYBOOK, encoding="utf-8") as fh:
        body = fh.read()
    client = DevinClient()
    existing = client.list_playbooks().get("playbooks", [])
    for pb in existing:
        if pb.get("title") == TITLE:
            print(f"Playbook already registered: {pb.get('playbook_id')}")
            return
    pb = client.create_playbook(TITLE, body)
    print(f"Registered playbook: {pb.get('playbook_id')}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Part 1 helper: create the remediation issues in YOUR Superset fork.

Files one GitHub issue per grounded finding (see orchestrator/scanner.py),
labelled so the webhook/scan path can pick them up. Run once against your fork.

    GITHUB_TOKEN=... GITHUB_REPO=your-org/superset python scripts/create_issues.py

Add --dry-run to print what would be filed without calling GitHub.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "orchestrator"))

import scanner
from issue_format import issue_body, labels


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    repo = os.environ.get("GITHUB_REPO", "your-org/superset")
    findings = scanner.run_scan(repo)

    if args.dry_run:
        for f in findings:
            print(f"\n=== [{f['severity'].upper()}] {f['title']}")
            print("labels:", ", ".join(labels(f)))
            print(issue_body(f))
        return

    from github_client import GitHubClient  # noqa: PLC0415

    gh = GitHubClient(repo=repo)
    for f in findings:
        issue = gh.create_issue(f["title"], issue_body(f), labels(f))
        print(f"Filed #{issue['number']}: {f['title']}")
    gh.close()


if __name__ == "__main__":
    main()

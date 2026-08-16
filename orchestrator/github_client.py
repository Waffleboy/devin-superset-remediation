"""
GitHub client — files issues, comments, and reads PR CI status.

In DEMO_MODE we don't touch the GitHub API: issue numbers are synthesized and
CI state comes from the mock. With a real GITHUB_TOKEN + GITHUB_REPO this
creates real issues and reads the real Checks API for the PR head — which is
how the verifier gets its independent signal.
"""

from __future__ import annotations

import logging
import os
import re

import httpx

log = logging.getLogger("github")

GITHUB_API = "https://api.github.com"

# The scoped check the verifier trusts as its independent signal. Set empty to
# fall back to aggregating ALL checks on the PR head (legacy behavior). Keyed to
# a single check on purpose: a fork can't pass Superset's full CI matrix without
# secrets/services, so we verify against a dedicated `remediation-verify` check
# that runs only the narrow, real test for what the PR changed.
VERIFY_CHECK_NAME = os.environ.get("VERIFY_CHECK_NAME", "remediation-verify")


class GitHubClient:
    def __init__(self, token: str | None = None, repo: str | None = None) -> None:
        self.token = token or os.environ.get("GITHUB_TOKEN", "")
        self.repo = repo or os.environ.get("GITHUB_REPO", "")
        self._client = httpx.Client(
            timeout=30.0,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
            },
        )

    def create_issue(self, title: str, body: str, labels: list[str]) -> dict:
        r = self._client.post(
            f"{GITHUB_API}/repos/{self.repo}/issues",
            json={"title": title, "body": body, "labels": labels},
        )
        r.raise_for_status()
        return r.json()

    def get_issue(self, issue_number: int) -> dict:
        r = self._client.get(f"{GITHUB_API}/repos/{self.repo}/issues/{issue_number}")
        r.raise_for_status()
        return r.json()

    def find_issue_by_title(self, title: str) -> dict | None:
        """
        Return an existing (non-PR) issue with this exact title, or None. Lets
        the scan path reuse issues already filed (e.g. by create_issues.py)
        instead of filing duplicates.
        """
        r = self._client.get(
            f"{GITHUB_API}/repos/{self.repo}/issues",
            params={"state": "all", "per_page": 100},
        )
        if r.status_code >= 400:
            return None
        for it in r.json():
            if "pull_request" in it:
                continue  # the issues endpoint also returns PRs
            if it.get("title") == title:
                return it
        return None

    def comment(self, issue_number: int, body: str) -> None:
        self._client.post(
            f"{GITHUB_API}/repos/{self.repo}/issues/{issue_number}/comments",
            json={"body": body},
        )

    def add_labels(self, issue_number: int, labels: list[str]) -> None:
        self._client.post(
            f"{GITHUB_API}/repos/{self.repo}/issues/{issue_number}/labels",
            json={"labels": labels},
        )

    def pr_ci_state(self, pr_url: str) -> str | None:
        """
        Read combined CI state for a PR's head commit via the Checks API.
        Returns 'success' | 'failure' | 'pending' | None.
        This is the INDEPENDENT signal the verifier uses — not Devin's word.
        """
        m = re.search(r"/pull/(\d+)", pr_url or "")
        if not m:
            return None
        pr_number = int(m.group(1))
        pr_response = self._client.get(f"{GITHUB_API}/repos/{self.repo}/pulls/{pr_number}")
        if pr_response.status_code >= 400:
            log.warning("Could not read PR %s for CI state: %s", pr_number, pr_response.text[:200])
            return None
        pr = pr_response.json()
        sha = pr.get("head", {}).get("sha")
        if not sha:
            return None
        # per_page=100: a real PR triggers the full upstream CI matrix (30+
        # checks), and the API defaults to 30 per page — our scoped
        # `remediation-verify` check can land on page 2 and be missed entirely.
        runs_response = self._client.get(
            f"{GITHUB_API}/repos/{self.repo}/commits/{sha}/check-runs",
            params={"per_page": 100},
        )
        if runs_response.status_code >= 400:
            log.warning("Could not read check runs for %s: %s", sha, runs_response.text[:200])
            return None
        runs = runs_response.json()
        checks = runs.get("check_runs", [])
        # Verify against ONLY our scoped check when configured. Superset's full
        # CI matrix can't pass on a fork (no secrets/services), so aggregating
        # every check would report a permanent, meaningless "failure". Keying off
        # `remediation-verify` gives an honest, reproducible signal.
        if VERIFY_CHECK_NAME:
            scoped = [c for c in checks if c.get("name") == VERIFY_CHECK_NAME]
            if not scoped:
                # Our check hasn't reported yet (or the workflow isn't installed
                # on this PR). Keep polling rather than trusting the noisy matrix.
                return "pending"
            checks = scoped
        if not checks:
            return "pending"
        failed = {"action_required", "cancelled", "failure", "startup_failure", "timed_out"}
        if any(c.get("conclusion") in failed for c in checks):
            return "failure"
        successful = {"success", "neutral", "skipped"}
        completed = [c for c in checks if c.get("status") == "completed"]
        if completed and all(c.get("conclusion") in successful for c in completed):
            return "success"
        return "pending"

    def close(self) -> None:
        self._client.close()


class MockGitHubClient:
    """DEMO_MODE: synthesize issue numbers, no network."""

    def __init__(self, *_, **__) -> None:
        self._n = 0
        self.repo = os.environ.get("GITHUB_REPO", "local-demo/superset")

    def create_issue(self, title: str, body: str, labels: list[str]) -> dict:
        self._n += 1
        return {"number": self._n, "html_url": f"https://github.com/{self.repo}/issues/{self._n}"}

    def get_issue(self, issue_number: int) -> dict:
        return {"number": issue_number, "title": f"Issue #{issue_number}", "body": ""}

    def find_issue_by_title(self, title: str) -> dict | None:
        # No pre-existing issues in the synthesized demo — always file fresh.
        return None

    def comment(self, issue_number: int, body: str) -> None:
        pass

    def add_labels(self, issue_number: int, labels: list[str]) -> None:
        pass

    def pr_ci_state(self, pr_url: str) -> str | None:
        # CI state is supplied by the mock session's `_ci` field instead.
        return None

    def close(self) -> None:
        pass

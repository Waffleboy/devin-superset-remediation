"""GitHub issue formatting shared by the API and helper scripts."""

from __future__ import annotations


def issue_body(finding: dict) -> str:
    lines = [
        f"## Summary\n{finding['summary']}",
        f"\n## Detection\n`{finding['detection']}`",
        f"\n## Severity\n{finding['severity'].upper()}",
    ]
    if finding.get("package"):
        target = f" -> `{finding['target']}`" if finding.get("target") else ""
        lines.append(f"\n## Affected\n`{finding['package']}=={finding['installed']}`{target}")
    lines.append(f"\n## Why this issue is in scope\n{finding['expected_behavior']}")
    lines.append("\n---\n_Filed automatically by devin-superset-remediation_")
    return "\n".join(lines)


def labels(finding: dict, remediate_label: str = "devin-remediate") -> list[str]:
    labels_ = [remediate_label, finding["kind"], f"severity-{finding['severity']}"]
    if finding["kind"] == "dependency-vulnerability":
        labels_.append("security")
    return labels_

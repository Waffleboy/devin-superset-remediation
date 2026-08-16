"""
Scanner — turns the repo into a list of remediation findings.

Design decision: the scanner runs OUTSIDE Devin. `npm audit`/`pip-audit`/`ruff`
run in seconds and cost nothing. Devin's value is the *fix*, not the *find*.
Devin as a primitive means we spend ACU only on work that needs judgment.

The findings below are grounded in the REAL current state of apache/superset,
**verified against the fork's `master` @ `e2bb33b` (2026-08-15)** — not invented CVEs — so
the submission addresses a real problem and every issue has a machine-checkable
success signal:

  - `superset/utils/pandas_postprocessing/cum.py:49` runs `df_cum.fillna(0)`
    UNCONDITIONALLY before `getattr(df_cum, "cum"+operator)()`. Correct only for
    cumsum: `[3, NaN, 2, 5]` under cummin returns `[3,0,0,0]` instead of
    `[3,NaN,2,2]`. A real correctness bug + regression test — the HERO issue.
  - `superset-frontend/package.json` pins `dompurify: ^3.4.11`, which is
    vulnerable (GHSA-55q2-fjhq-7xh7, moderate XSS, patched in 3.4.13). Clean
    drop-in bump.
  - Blanket `# type: ignore` comments remain in active code; ruff's PGH003 rule
    is NOT yet enabled in pyproject.toml — enabling it is a genuine CI gate.
  - The deck.gl/loaders.gl geospatial stack carries a DoS advisory chain whose
    only `npm audit` fix is a MAJOR downgrade that breaks the maps — the correct
    outcome is escalation, not a broken PR.
  - apache/superset#42704 is open and labelled `🦾 ai-candidate` (maintainers
    curate it *for* AI agents) — a narrow, unit-testable app bug.

When a Superset checkout is available (REPO_PATH set, DEMO_MODE off) we run the
real scanners — `npm audit` over `superset-frontend/` (which surfaces the
dompurify bump and the deck.gl DoS chain) and optionally `pip-audit` over the
Python requirements (bonus real advisories) — and merge them with the grounded
code/correctness issues (cum.py, type-ignore sweep, #42704) that no dependency
scanner can surface. Otherwise we fall back to the grounded set so the demo is
deterministic.

Note: the disproven Flask 3.x / SQLAlchemy 2.0 items from the first draft are
gone — Flask 3 has no clean path (capped by flask-appbuilder) and SQLAlchemy 2.0
is already merged upstream (PR #42803). See ../../superset_scan_analysis.md.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from typing import Any

log = logging.getLogger("scanner")

# Where the Superset checkout lives when we run live tooling against it.
# In a container this is a mounted volume; locally, set REPO_PATH in .env.
REPO_PATH = os.environ.get("REPO_PATH", "")

# A live audit finding needs the same shape as a grounded one so the rest of the
# pipeline (issue body, Devin prompt, dashboard) is agnostic to its source. We
# enrich the two high-signal frontend packages with the "why it's in scope"
# narrative so a live-sourced finding still slots into the demo's behavior
# spectrum (id 2 = baseline bump, id 4 = escalation); everything else gets a
# generic-but-honest dependency-vulnerability record with an id >= 200.
_ENRICH_EXACT: dict[str, dict[str, Any]] = {
    "dompurify": {
        "id": 2,
        "kind": "dependency-vulnerability",
        "severity": "medium",
        "expected_behavior": "BASELINE: fast, low-ACU, real advisory with a drop-in patch — throughput.",
        "title_override": "Bump dompurify ^3.4.11 -> ^3.4.13 (GHSA-55q2-fjhq-7xh7, moderate XSS)",
        "target_override": "^3.4.13",
    },
}

# Substring rules for the multi-package geospatial DoS chain — npm audit reports
# it under several names (@deck.gl/core, loaders.gl, ...); any of them maps to
# the single escalation issue (id 4), deduped so it's filed once.
_ENRICH_CHAIN: list[tuple[tuple[str, ...], dict[str, Any]]] = [
    (
        ("deck.gl", "loaders.gl"),
        {
            "id": 4,
            "kind": "dependency-vulnerability",
            "severity": "high",
            "expected_behavior": "ESCALATE: the only fix is an unacceptable major geospatial downgrade — Devin refuses and escalates.",
            "title_override": "Investigate deck.gl/loaders.gl DoS advisory chain (no safe upgrade path)",
            "package_override": "deck.gl/loaders.gl advisory chain",
            "target_override": "no safe automatic fix",
            "summary_override": (
                "`npm audit` reports a high-severity advisory chain through deck.gl / "
                "loaders.gl. npm's proposed fix is semver-major for the affected "
                "geospatial packages and would downgrade/break the current map stack. "
                "The correct outcome is investigation plus escalation with a staged "
                "upgrade recommendation, not an automatic PR."
            ),
        },
    ),
]

# Each finding maps 1:1 to an issue we will file and hand to Devin.
# `kind` drives labels; `expected_behavior` documents WHY this issue is in the
# set (each one exercises a different Devin/system behavior).
GROUNDED_FINDINGS: list[dict[str, Any]] = [
    {
        "id": 1,
        "kind": "correctness-bug",
        "severity": "high",
        "title": "Fix `fillna(0)` corrupting cumulative min/max/prod in pandas postprocessing",
        "package": None,
        "installed": None,
        "target": None,
        "detection": "code review + pytest (tests/.../test_cum.py)",
        "summary": (
            "`superset/utils/pandas_postprocessing/cum.py:49` calls "
            "`df_cum = df_cum.fillna(0)` UNCONDITIONALLY, before "
            "`getattr(df_cum, 'cum' + operator)()`. That is only correct for "
            "`cumsum`. For cumulative min/max/prod a gap becomes a zero and "
            "corrupts the series: `[3, NaN, 2, 5]` under `cummin` returns "
            "`[3, 0, 0, 0]` instead of `[3, NaN, 2, 2]`. Guard the fill to the "
            "cumsum case (or make it operator-aware) and add a regression test "
            "covering min/max/prod over gap data."
        ),
        "expected_behavior": "HERO: a real correctness bug no scanner finds and no bot fixes — Devin diagnoses it and writes a regression test.",
    },
    {
        "id": 2,
        "kind": "dependency-vulnerability",
        "severity": "medium",
        "title": "Bump dompurify ^3.4.11 -> ^3.4.13 (GHSA-55q2-fjhq-7xh7, moderate XSS)",
        "package": "dompurify",
        "installed": "^3.4.11",
        "target": "^3.4.13",
        "detection": "npm audit (superset-frontend)",
        "summary": (
            "`superset-frontend/package.json` pins `dompurify: ^3.4.11`, which is "
            "vulnerable per GitHub advisory GHSA-55q2-fjhq-7xh7 (moderate XSS, "
            "affects <= 3.4.12, patched in 3.4.13). dompurify is a direct dep on "
            "Superset's HTML/markdown sanitisation path (SafeMarkdown). This is a "
            "true drop-in patch — clean, single-package remediation."
        ),
        "expected_behavior": "BASELINE: fast, low-ACU, real advisory with a drop-in patch — throughput.",
    },
    {
        "id": 3,
        "kind": "code-quality",
        "severity": "low",
        "title": "Replace blanket `# type: ignore` with coded ignores and enable ruff PGH003 (scope one package)",
        "package": None,
        "installed": None,
        "target": None,
        "detection": "ruff PGH003 + mypy",
        "summary": (
            "Blanket `# type: ignore` comments remain in active code and tests, "
            "and ruff's PGH003 rule is NOT yet enabled in `pyproject.toml`. "
            "Scope one directory: remove "
            "the blanket ignores, run mypy to read the errors they were masking, "
            "re-add scoped `# type: ignore[code]` where genuinely needed, then "
            "enable PGH003 so CI enforces it going forward. Expect the first pass "
            "to surface hidden type errors (red CI) that must be fixed to green."
        ),
        "expected_behavior": "IN-TREE CODE FIX + self-correction: enabling the gate turns CI red on hidden errors, Devin fixes them to green.",
    },
    {
        "id": 4,
        "kind": "dependency-vulnerability",
        "severity": "high",
        "title": "Investigate deck.gl/loaders.gl DoS advisory chain (no safe upgrade path)",
        "package": None,
        "installed": None,
        "target": None,
        "detection": "npm audit (superset-frontend)",
        "summary": (
            "`npm audit` reports a DoS advisory chain in the deck.gl / loaders.gl "
            "geospatial rendering stack. The only fix it offers is a MAJOR version "
            "downgrade of `@deck.gl/*` and `loaders.gl` that would break the map "
            "visualisations. The correct outcome is investigation plus an "
            "escalation with a written rationale and a tracked staged-upgrade "
            "recommendation — NOT a forced, breaking PR."
        ),
        "expected_behavior": "ESCALATE: the only fix is an unacceptable major geospatial downgrade — Devin refuses and escalates.",
    },
    {
        "id": 5,
        "kind": "bug",
        "severity": "medium",
        "title": "Fix blank Metric Warning text after save in Edit Dataset from Explore (#42704, `🦾 ai-candidate`)",
        "package": None,
        "installed": None,
        "target": None,
        "detection": "maintainer-tagged issue apache/superset#42704",
        "summary": (
            "apache/superset#42704 is open and curated by the maintainers, "
            "labelled both `good first issue` and `🦾 ai-candidate` (they "
            "explicitly tag issues for AI agents). Metric warning text renders "
            "blank after saving in Edit Dataset from Explore — suspected "
            "`warning_text` vs `extra.warning_markdown` field-mapping mismatch. "
            "Narrow, unit-testable fix with a regression test."
        ),
        "expected_behavior": "CREDIBILITY ANCHOR: a real issue Superset maintainers curated *for AI agents* — proves real-world fit, plus a regression test.",
    },
]


def _pip_audit(req_files: list[str]) -> list[dict]:
    """
    Run pip-audit against one or more requirements files and normalise its JSON
    to our finding shape. Returns [] if pip-audit isn't installed or finds
    nothing. These are BONUS live Python advisories (ids >= 100); the verified
    demo set (1-5) is frontend/code and isn't pip-audit-derivable.

    pip-audit --format json emits: {"dependencies": [{name, version,
    vulns: [{id, fix_versions, description}], ...}]}
    """
    if not shutil.which("pip-audit"):
        log.warning("pip-audit not on PATH — skipping Python dep audit")
        return []

    findings: list[dict] = []
    next_id = 100  # live findings get ids >=100 so they never collide with 1-5
    for req in req_files:
        if not os.path.exists(req):
            continue
        try:
            proc = subprocess.run(
                ["pip-audit", "-r", req, "--format", "json"],
                capture_output=True,
                text=True,
                timeout=180,
            )
            data = json.loads(proc.stdout or "{}")
        except (subprocess.SubprocessError, json.JSONDecodeError) as e:
            log.error("pip-audit failed on %s: %s", req, e)
            continue

        for dep in data.get("dependencies", []):
            for v in dep.get("vulns", []):
                fix = (v.get("fix_versions") or [None])[0]
                findings.append(
                    {
                        "id": next_id,
                        "kind": "dependency-vulnerability",
                        "severity": "medium",
                        "package": dep.get("name"),
                        "installed": dep.get("version"),
                        "target": fix or "latest patched",
                        "detection": f"pip-audit ({os.path.basename(req)})",
                        "summary": v.get("description")
                        or f"{v.get('id')} affects {dep.get('name')} {dep.get('version')}.",
                        "title": f"[{v.get('id')}] Upgrade {dep.get('name')} to {fix or 'a patched release'}",
                        "expected_behavior": "Live pip-audit finding — Python dependency remediation.",
                    }
                )
                next_id += 1
    return findings


def _npm_audit(frontend_dir: str) -> list[dict]:
    """
    Run `npm audit --json` against the Superset frontend and normalise it to our
    finding shape. This is what genuinely surfaces the two headline dependency
    issues: the dompurify bump (id 2) and the deck.gl/loaders.gl DoS chain
    (id 4, the escalation). Enriched findings adopt those fixed ids so they slot
    into the demo's behavior spectrum; anything else gets an id >= 200.

    npm audit (v7+) emits: {"vulnerabilities": {"<pkg>": {severity, range,
    fixAvailable, ...}}}. Returns [] if npm isn't available or nothing is found.
    """
    if not frontend_dir or not os.path.isdir(frontend_dir):
        return []
    if not shutil.which("npm"):
        log.warning("npm not on PATH — skipping frontend audit")
        return []
    try:
        proc = subprocess.run(
            ["npm", "audit", "--json"],
            cwd=frontend_dir,
            capture_output=True,
            text=True,
            timeout=180,
        )
        data = json.loads(proc.stdout or "{}")
    except (subprocess.SubprocessError, json.JSONDecodeError) as e:
        log.error("npm audit failed in %s: %s", frontend_dir, e)
        return []

    findings: list[dict] = []
    next_id = 200
    seen_enriched: set[int] = set()
    for name, adv in (data.get("vulnerabilities") or {}).items():
        pkg = name.lower()
        base = {
            "id": next_id,
            "kind": "dependency-vulnerability",
            "severity": adv.get("severity", "medium"),
            "package": name,
            "installed": (adv.get("range") or None),
            "target": "patched release" if adv.get("fixAvailable") else "no safe fix",
            "detection": "npm audit (superset-frontend)",
            "summary": f"npm audit reports a {adv.get('severity', 'moderate')} "
            f"advisory affecting `{name}` ({adv.get('range', 'see audit')}).",
            "title": f"[npm audit] Remediate {name} advisory",
            "expected_behavior": "Live npm audit finding — frontend dependency remediation.",
        }
        ench = _ENRICH_EXACT.get(pkg) or next(
            (e for subs, e in _ENRICH_CHAIN if any(s in pkg for s in subs)), None
        )
        if ench:
            if ench["id"] in seen_enriched:
                continue  # e.g. several @deck.gl/* packages -> one escalation issue
            base.update({k: ench[k] for k in ("id", "kind", "severity", "expected_behavior")})
            base["title"] = ench["title_override"]
            if ench.get("target_override"):
                base["target"] = ench["target_override"]
            if ench.get("package_override"):
                base["package"] = ench["package_override"]
            if ench.get("summary_override"):
                base["summary"] = ench["summary_override"]
            seen_enriched.add(ench["id"])
        else:
            next_id += 1
        findings.append(base)
    return findings


def run_scan(repo: str, demo_mode: bool = False, subset: list[int] | None = None) -> list[dict]:
    """
    Return findings for the repo.

    Live mode: if a Superset checkout is available (REPO_PATH) we run the real
    scanners — `npm audit` over the frontend (surfaces the dompurify bump and the
    deck.gl DoS chain) and `pip-audit` over the Python requirements (bonus
    advisories, ids >= 100). We MERGE in the grounded code/correctness findings
    (cum.py, the type-ignore sweep, #42704) that no dependency scanner can
    surface, so the full behavior spectrum is always demonstrable. A live-sourced
    finding that maps onto a grounded slot (dompurify -> id 2, deck.gl -> id 4)
    REPLACES the grounded stand-in, so the demo can honestly claim it came from a
    real scan.

    Demo/fallback: if live tooling isn't available, return the grounded set so
    the pipeline is deterministic. `subset` filters by finding id either way.
    """
    grounded = [dict(f) for f in GROUNDED_FINDINGS]
    live: list[dict] = []
    if not demo_mode and REPO_PATH:
        live = _npm_audit(os.path.join(REPO_PATH, "superset-frontend"))
        live += _pip_audit(
            [
                os.path.join(REPO_PATH, "requirements", "base.txt"),
                os.path.join(REPO_PATH, "requirements", "development.txt"),
            ]
        )

    if live:
        # Live findings that mapped onto a grounded slot (ids 2/4 via enrichment)
        # replace the grounded stand-in; grounded code/correctness issues and any
        # other live advisories are all kept.
        replaced = {f["id"] for f in live if f["id"] in (2, 4)}
        findings = live + [f for f in grounded if f["id"] not in replaced]
        findings.sort(key=lambda f: f["id"])
        log.info(
            "Scan of %s: %d live audit + %d grounded finding(s)",
            repo,
            len(live),
            len(findings) - len(live),
        )
    else:
        findings = grounded
        log.info(
            "Scan of %s produced %d grounded finding(s)%s",
            repo,
            len(findings),
            " [demo]" if demo_mode else " [no live tooling]",
        )

    if subset:
        findings = [f for f in findings if f["id"] in subset]
    return [dict(f) for f in findings]

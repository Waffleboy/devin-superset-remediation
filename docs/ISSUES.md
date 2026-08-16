# Part 1 — The issues, and why each one is here

The issue set is engineered so that, together, the five tasks demonstrate the
**full honest spectrum** of what an autonomous-agent remediation system does —
including the parts that *aren't* a clean win. A reviewer should come away
convinced the system handles success, self-correction, escalation, and cost,
not just happy-path PRs.

All findings are **verified against the fork's `master` @ `e2bb33b`
(2026-08-15, mirrors `apache/superset`)** at the exact file/line/advisory — not invented CVEs, and not the
disproven Flask/SQLAlchemy claims from the first draft (see
[`../../superset_scan_analysis.md`](../../superset_scan_analysis.md)). Every
issue has an objective pass/fail signal, which is what makes the observability
layer meaningful.

---

### Issue 1 — Cumulative min/max/prod corruption in `cum.py` (HERO)
**Severity:** High · **Kind:** correctness-bug

`superset/utils/pandas_postprocessing/cum.py:49` runs `df_cum = df_cum.fillna(0)`
**unconditionally**, before `getattr(df_cum, "cum" + operator)()`. That is only
correct for `cumsum`. For cumulative **min/max/prod**, a gap in the data becomes
a zero and corrupts the whole series: `[3, NaN, 2, 5]` under `cummin` returns
`[3, 0, 0, 0]` instead of `[3, NaN, 2, 2]`.

**Why it's here:** this is the *judgment* fix no bot can do — it's not a CVE or a
version bump, it's a semantics bug. Devin must diagnose it, guard the fill to the
cumsum case (or make it operator-aware), and **write a regression test** proving
min, max, and product now behave over gap data. **This is the "why Devin" story.**

### Issue 2 — Bump dompurify `^3.4.11 → ^3.4.13` (BASELINE)
**Severity:** Medium · **Kind:** dependency-vulnerability

`superset-frontend/package.json` pins `dompurify: ^3.4.11`, vulnerable per
**GHSA-55q2-fjhq-7xh7** (moderate XSS, affects ≤ 3.4.12, patched 3.4.13).
dompurify is a direct dependency on Superset's HTML/markdown sanitisation path
(SafeMarkdown). True drop-in patch.

**Why it's here:** the trivial fast path. It anchors the **throughput** and
**low-ACU** baseline, so the hard issues have something to be measured against.
Verify with `npm audit` + the SafeMarkdown Jest tests.

### Issue 3 — Blanket `# type: ignore` sweep + enable ruff PGH003 (SELF-CORRECTION)
**Severity:** Low · **Kind:** code-quality

Blanket `# type: ignore` comments remain in active code and tests, while ruff's
**PGH003** rule is **not yet enabled** in `pyproject.toml`. Scope one directory:
strip the blanket ignores, run mypy to read the errors they were masking, re-add
scoped `# type: ignore[code]`, and turn on PGH003 so CI enforces it.

**Why it's here:** proves Devin fixes **in-tree code**, not just dependency pins —
and exercises the **bounded self-correction loop**. Enabling the gate turns CI
**red** on the hidden type errors; the verifier catches it and feeds the failure
back, and Devin corrects to green within the retry budget.

### Issue 4 — deck.gl/loaders.gl DoS advisory chain (ESCALATION)
**Severity:** High · **Kind:** dependency-vulnerability

`npm audit` reports a DoS advisory chain in the deck.gl / loaders.gl geospatial
rendering stack. The **only** fix it offers is a **major version downgrade** of
`@deck.gl/*` and `loaders.gl` that would break the map visualisations.

**Why it's here:** the single most important beat for Cognition's bar. The
*correct* outcome is **not** a PR — it's Devin investigating, discovering that the
only remedy is unacceptable, and **escalating with a written rationale** and a
tracked staged-upgrade recommendation instead of forcing a breaking change.
Escalation is a **successful** system outcome. This is **bounded autonomy**.

### Issue 5 — Blank Metric Warning text, `#42704` (CREDIBILITY ANCHOR)
**Severity:** Medium · **Kind:** bug

`apache/superset#42704` is open and curated by the maintainers, labelled both
`good first issue` and **`🦾 ai-candidate`** — they explicitly tag issues *for*
AI agents. Metric warning text renders blank after saving in Edit Dataset from
Explore; suspected `warning_text` vs `extra.warning_markdown` field-mapping
mismatch. Narrow and unit-testable.

**Why it's here:** the credibility anchor. Remediating an issue the Superset
maintainers themselves earmarked for AI agents shows real-world fit, not a
contrived task — and shows Devin writing a **regression test**, not just a patch.

---

## How to file them in your fork

```bash
GITHUB_TOKEN=... GITHUB_REPO=your-org/superset python scripts/create_issues.py
# or preview first:
python scripts/create_issues.py --dry-run
```

Each issue is labelled `devin-remediate`, its `kind`, and `severity-*` (the two
dependency-vulnerability issues also get `security`), so both the scheduled scan
and the `issues.labeled` webhook can pick them up.

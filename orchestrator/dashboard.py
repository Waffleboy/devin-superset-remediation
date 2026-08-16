"""
Observability dashboard — the answer to "how would an eng leader know this works?"

Server-rendered HTML (no build step), auto-refreshing. The layout is a single
"instrument readout" rather than a grid of equal cards:
  - One headline metric (success rate) reads large in crisp ink; the split gauge
    carries the only color, so status — not decoration — is what draws the eye.
  - The remaining six metrics collapse into a compact hairline-divided strip
    inside the same panel, giving clear hierarchy (one loud number, quiet rest).
  - Status is always icon + label + color, never color alone.
  - Brand blue is reserved for the mark, links, and the running state; the number
    itself stays ink so the panel reads premium, not neon.

Palette is stepped for the dark surface (#09090C): ink/secondary/muted clear the
contrast floor, and the status hues are the dark-mode steps (not flipped light
tokens).
"""

from __future__ import annotations

# Status palette: reserved states, each shipped with an icon + label.
# (glyph, label, foreground ink, translucent fill, border tint)
_STATUS = {
    "verified": ("✓", "Verified", "#3fb950", "rgba(63,185,80,.14)", "rgba(63,185,80,.35)"),
    "escalated": ("!", "Escalated", "#e3b341", "rgba(227,179,65,.14)", "rgba(227,179,65,.35)"),
    "running": ("●", "Running", "#7d94ff", "rgba(125,148,255,.16)", "rgba(125,148,255,.4)"),
    "queued": ("·", "Queued", "#9aa3b2", "rgba(154,163,178,.12)", "rgba(154,163,178,.28)"),
    "failed": ("×", "Failed", "#ff6b63", "rgba(255,107,99,.14)", "rgba(255,107,99,.35)"),  # noqa: RUF001
}


def _stat(label: str, value, sub: str = "") -> str:
    """One cell in the compact instrument strip beneath the hero metric."""
    sub_html = f'<div class="stat-sub">{sub}</div>' if sub else ""
    return f"""<div class="stat">
      <div class="stat-lbl">{label}</div>
      <div class="stat-val">{value}</div>{sub_html}
    </div>"""


def _readout(m: dict, strip: str) -> str:
    """The signature panel: headline success rate + gauge, over the stat strip."""
    total = m["total_tasks"] or 0
    v_pct = (100 * m["verified"] / total) if total else 0
    e_pct = (100 * m["escalated"] / total) if total else 0
    gauge = f"""<div class="gauge" title="{m["verified"]} verified · {m["escalated"]} escalated">
        <span class="seg v" style="width:{v_pct:.2f}%"></span>
        <span class="seg e" style="width:{e_pct:.2f}%"></span>
      </div>"""
    return f"""<section class="readout">
    <div class="hero">
      <div class="hero-head">
        <span class="eyebrow">Success rate</span>
        <span class="hero-caption">{m["verified"]} verified · {m["escalated"]} escalated</span>
      </div>
      <div class="hero-body">
        <div class="hero-num">{m["success_rate_pct"]}<span class="pct">%</span></div>
        {gauge}
      </div>
    </div>
    <div class="strip">{strip}</div>
  </section>"""


def _row(t: dict, repo: str) -> str:
    _glyph, word, fg, bg, bd = _STATUS.get(
        t["status"], ("·", t["status"], "#9aa3b2", "rgba(154,163,178,.12)", "rgba(154,163,178,.28)")
    )
    badge = (
        f'<span class="badge" style="color:{fg};background:{bg};border-color:{bd}">'
        f'<i class="dot" style="background:{fg}"></i>{word}</span>'
    )
    issue = f"#{t['issue_number']}" if t["issue_number"] else "—"
    issue_link = (
        f'<a href="https://github.com/{repo}/issues/{t["issue_number"]}">{issue}</a>'
        if t["issue_number"]
        else "—"
    )
    session = f'<a href="{t["session_url"]}">session ↗</a>' if t.get("session_url") else "—"
    pr = f'<a href="{t["pr_url"]}">PR ↗</a>' if t.get("pr_url") else "—"
    ci = t.get("ci_state") or "—"
    corr = t.get("correction_rounds") or 0
    return f"""<tr>
      <td class="mono nowrap">{issue_link}</td>
      <td class="title">{t["title"]}</td>
      <td>{badge}</td>
      <td class="nowrap">{session}</td>
      <td class="nowrap">{pr}</td>
      <td class="mono">{ci}</td>
      <td class="mono">{corr}</td>
    </tr>"""


def render(m: dict, tasks: list[dict], repo: str, demo: bool) -> str:
    strip = "".join(
        [
            _stat("PRs opened", m["prs_opened"], "ready to merge"),
            _stat("Self-corrections", m["self_corrections"], "CI retries, no human"),
            _stat("Human handoffs", m["human_handoffs"], "needs a person"),
            _stat("MTTR", f'{m["mttr_minutes"]}<span class="unit">m</span>', "issue → resolved"),
            _stat(
                "Resolution",
                f'{m["success_rate_pct"]}<span class="unit">%</span>',
                "verified or escalated · cost in Devin console",
            ),
            _stat("In flight", m["running"], f"of {m['total_tasks']} tasks"),
        ]
    )
    readout = _readout(m, strip)
    rows = (
        "".join(_row(t, repo) for t in tasks)
        or '<tr><td colspan="7" class="empty">No tasks yet — trigger a scan.</td></tr>'
    )
    banner = (
        '<span class="demo"><i class="pulse"></i>DEMO MODE · scripted Devin '
        "trajectories, identical orchestrator path</span>"
        if demo
        else ""
    )

    return f"""<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="5">
<title>Devin · Superset Remediation</title>
<style>
  :root {{
    --bg:#09090c; --surface:#0f1013; --surface-2:#121317; --hover:#16171c;
    --line:#212329; --line-soft:#191a1f;
    --ink:#f3f4f7; --ink2:#9aa1af; --muted:#5f6572;
    --accent:#6e8bff; --accent-2:#9db2ff;
    --ok:#43b95a; --warn:#e0b23f;
    --shadow:0 1px 0 rgba(255,255,255,.02), 0 18px 40px -24px rgba(0,0,0,.9);
  }}
  * {{ box-sizing:border-box; }}
  html,body {{ height:100%; }}
  body {{
    margin:0; color:var(--ink); background:var(--bg);
    font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
    -webkit-font-smoothing:antialiased; text-rendering:optimizeLegibility;
  }}
  a {{ color:var(--accent-2); text-decoration:none; transition:color .15s; }}
  a:hover {{ color:#c7d2ff; }}
  .wrap {{ width:100%; max-width:1120px; margin:0 auto; padding:0 clamp(20px,3vw,44px); }}

  /* Header — quiet, brand mark carries the only saturated blue up top */
  header {{ padding:30px 0 22px; }}
  .brand {{ display:flex; align-items:center; gap:12px; }}
  .logo {{ width:26px; height:26px; border-radius:8px; flex:0 0 auto; position:relative;
           background:linear-gradient(150deg,#8ea1ff,#4c6fff);
           box-shadow:0 0 0 1px rgba(255,255,255,.10), 0 4px 14px rgba(76,111,255,.35); }}
  .logo::after {{ content:""; position:absolute; inset:8px; border-radius:50%;
                  background:var(--bg); box-shadow:inset 0 0 0 2px rgba(255,255,255,.92); }}
  h1 {{ font-size:17px; margin:0; font-weight:600; letter-spacing:-.01em; }}
  h1 .thin {{ color:var(--ink2); font-weight:400; }}
  .repo {{ color:var(--muted); font-size:12.5px; margin:6px 0 0 38px; }}
  .demo {{ display:inline-flex; align-items:center; gap:7px; margin:14px 0 0 38px;
           font-size:11.5px; color:var(--warn); font-weight:500; letter-spacing:.01em;
           background:rgba(224,178,63,.08); border:1px solid rgba(224,178,63,.24);
           padding:4px 11px; border-radius:999px; }}
  .pulse {{ width:6px; height:6px; border-radius:50%; background:var(--warn);
            box-shadow:0 0 0 0 rgba(224,178,63,.6); animation:pulse 2s infinite; }}
  @keyframes pulse {{ 0%{{box-shadow:0 0 0 0 rgba(224,178,63,.45)}}
                      70%{{box-shadow:0 0 0 6px rgba(224,178,63,0)}}
                      100%{{box-shadow:0 0 0 0 rgba(224,178,63,0)}} }}

  .rule {{ height:1px; background:var(--line-soft); margin:0; }}
  main {{ padding:26px 0 52px; }}

  /* Signature: one instrument readout — hero metric over a hairline stat strip */
  .readout {{ background:var(--surface); border:1px solid var(--line);
              border-radius:14px; overflow:hidden; box-shadow:var(--shadow);
              margin-bottom:34px; }}
  .hero {{ padding:22px 26px 24px; }}
  .hero-head {{ display:flex; align-items:baseline; justify-content:space-between; gap:12px; }}
  .eyebrow {{ font-size:11px; text-transform:uppercase; letter-spacing:.14em;
              color:var(--muted); font-weight:600; }}
  .hero-caption {{ font-size:12px; color:var(--ink2); font-variant-numeric:tabular-nums; }}
  .hero-body {{ display:flex; align-items:center; gap:22px; margin-top:12px; }}
  .hero-num {{ font-size:clamp(44px,3.4vw,66px); font-weight:600; letter-spacing:-.035em;
               line-height:1; color:var(--ink); font-variant-numeric:tabular-nums;
               flex:0 0 auto; }}
  .hero-num .pct {{ font-size:.52em; font-weight:500; color:var(--ink2); margin-left:2px; }}
  .gauge {{ flex:1; display:flex; height:5px; border-radius:999px; overflow:hidden;
            background:rgba(255,255,255,.05); }}
  .gauge .seg {{ height:100%; }}
  .gauge .seg.v {{ background:var(--ok); }}
  .gauge .seg.e {{ background:var(--warn); }}

  /* gap:1px over a line-colored bed renders as crisp hairline dividers, wrap-safe */
  .strip {{ display:grid; grid-template-columns:repeat(6,1fr); gap:1px;
            background:var(--line-soft); border-top:1px solid var(--line-soft); }}
  .stat {{ background:var(--surface); padding:15px 18px 16px;
           transition:background .15s; }}
  .stat:hover {{ background:var(--surface-2); }}
  .stat-lbl {{ color:var(--ink2); font-size:11.5px; font-weight:500; }}
  .stat-val {{ font-size:clamp(21px,1.5vw,27px); font-weight:600; letter-spacing:-.02em;
               color:var(--ink); font-variant-numeric:tabular-nums; line-height:1.1;
               margin-top:6px; }}
  .stat-val .unit {{ font-size:13px; font-weight:500; color:var(--ink2); margin-left:1px; }}
  .stat-sub {{ color:var(--muted); font-size:11px; margin-top:5px; }}

  .section-h {{ display:flex; align-items:baseline; gap:10px; margin:0 0 13px; }}
  h2 {{ font-size:11px; text-transform:uppercase; letter-spacing:.14em;
        color:var(--muted); margin:0; font-weight:600; }}
  .count {{ font-size:12px; color:var(--muted); font-variant-numeric:tabular-nums; }}

  .card {{ background:var(--surface); border:1px solid var(--line); border-radius:14px;
           overflow:hidden; box-shadow:var(--shadow); }}
  table {{ width:100%; border-collapse:collapse; }}
  th,td {{ text-align:left; padding:11px 16px; border-bottom:1px solid var(--line-soft);
           vertical-align:middle; }}
  th {{ font-size:10.5px; text-transform:uppercase; letter-spacing:.08em;
        color:var(--muted); font-weight:600;
        background:var(--surface-2); position:sticky; top:0; }}
  tbody tr {{ transition:background .12s; }}
  tbody tr:hover {{ background:var(--hover); }}
  tbody tr:last-child td {{ border-bottom:none; }}
  td.title {{ max-width:380px; color:var(--ink); }}
  .nowrap {{ white-space:nowrap; }}
  .mono {{ font-variant-numeric:tabular-nums; font-feature-settings:"tnum";
           color:var(--ink2); }}
  .badge {{ display:inline-flex; align-items:center; gap:6px; font-size:11.5px;
            font-weight:500; padding:3px 10px 3px 8px; border-radius:999px;
            border:1px solid transparent; white-space:nowrap; }}
  .badge .dot {{ width:6px; height:6px; border-radius:50%; }}
  .empty {{ text-align:center; color:var(--muted); padding:34px; }}
  footer {{ padding:18px 0 0; color:var(--muted); font-size:11.5px; }}

  /* Fill wide, high-resolution displays instead of hugging a narrow column */
  @media (min-width:1500px) {{ .wrap {{ max-width:1320px; }} }}
  @media (min-width:1900px) {{ .wrap {{ max-width:1560px; }} }}
  @media (min-width:2400px) {{ .wrap {{ max-width:1800px; }} }}

  @media (max-width:720px) {{
    .strip {{ grid-template-columns:repeat(2,1fr); }}
    .hero-body {{ flex-wrap:wrap; gap:14px; }}
    .gauge {{ flex:1 1 100%; }}
  }}
  @media (prefers-reduced-motion:reduce) {{
    .pulse {{ animation:none; }} * {{ transition:none !important; }}
  }}
</style></head><body>
<header class="wrap">
  <div class="brand">
    <span class="logo"></span>
    <h1>Devin <span class="thin">· Apache Superset Remediation</span></h1>
  </div>
  <div class="repo">{repo} · autonomous CVE / dependency / code-quality remediation</div>
  {banner}
</header>
<div class="rule"></div>
<main class="wrap">
  {readout}
  <div class="section-h">
    <h2>Remediation tasks</h2>
    <span class="count">{len(tasks)} total</span>
  </div>
  <div class="card">
    <table>
      <thead><tr>
        <th>Issue</th><th>Task</th><th>Status</th><th>Devin</th><th>PR</th>
        <th>CI</th><th>Corrections</th>
      </tr></thead>
      <tbody>{rows}</tbody>
    </table>
  </div>
  <footer>Auto-refreshes every 5s · success is marked from CI, not Devin's self-report.</footer>
</main>
</body></html>"""

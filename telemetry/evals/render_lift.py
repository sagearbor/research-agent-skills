#!/usr/bin/env python3
"""Render telemetry/evals/skill-lift.jsonl as a self-contained HTML view of
Skill Lift over time, per skill / case / model.

No external assets of any kind — inline CSS, inline SVG, no fonts, no scripts
fetched, nothing that needs a network in two years' time (the same practice as
llm-usage-ledger's html_report()).  Stdlib only.

Two things this renderer refuses to do:

  * hide failed runs.  A run that errored is a red marker on the timeline and a
    row in the table, with its error type.  A gap in a chart must never be the
    only trace of a run that happened.
  * pretend one measurement is a trend.  With a single row it renders a single
    labelled point and says so, rather than drawing a line to nowhere.

Usage:
    render_lift.py [--ledger PATH] [--out PATH] [--open]
"""

from __future__ import annotations

import argparse
import html
import json
from collections import OrderedDict
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_LEDGER = HERE / "skill-lift.jsonl"
DEFAULT_OUT = HERE / "skill-lift.html"

PASS_T, FAIL_T = 0.05, -0.10

CSS = """
:root{--bg:#ffffff;--ink:#1c2733;--muted:#5b6b7b;--grid:#e3e8ee;--card:#f7f9fb;
      --good:#008300;--bad:#e34948;--warn:#8a6d00;--line:#3b6ea5;}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){
      --bg:#12171d;--ink:#e6edf3;--muted:#9aa8b6;--grid:#2a323b;--card:#1a2129;
      --good:#3fb950;--bad:#ff7b72;--warn:#d2a400;--line:#79a8dd;}}
:root[data-theme="dark"]{--bg:#12171d;--ink:#e6edf3;--muted:#9aa8b6;
      --grid:#2a323b;--card:#1a2129;--good:#3fb950;--bad:#ff7b72;
      --warn:#d2a400;--line:#79a8dd;}
*{box-sizing:border-box}
body{margin:0;padding:24px;background:var(--bg);color:var(--ink);
     font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif}
h1{font-size:20px;margin:0 0 4px}
h2{font-size:15px;margin:28px 0 8px}
.sub{color:var(--muted);font-size:12px;margin-bottom:18px}
.cards{display:flex;flex-wrap:wrap;gap:10px;margin:14px 0 6px}
.card{background:var(--card);border:1px solid var(--grid);border-radius:8px;
      padding:10px 14px;min-width:120px}
.card .n{font-size:22px;font-weight:600;line-height:1.1}
.card .l{font-size:11px;color:var(--muted);text-transform:uppercase;
         letter-spacing:.04em}
.good{color:var(--good)}.bad{color:var(--bad)}.warn{color:var(--warn)}
.grp{background:var(--card);border:1px solid var(--grid);border-radius:8px;
     padding:12px 14px;margin:12px 0}
.grp h3{margin:0 0 2px;font-size:14px}
.grp .meta{color:var(--muted);font-size:12px;margin-bottom:8px}
.wrap{overflow-x:auto;-webkit-overflow-scrolling:touch}
table{border-collapse:collapse;width:100%;min-width:640px;font-size:12px}
th,td{text-align:left;padding:5px 8px;border-bottom:1px solid var(--grid);
      white-space:nowrap}
th{color:var(--muted);font-weight:600;font-size:11px;text-transform:uppercase}
td.wrapcell{white-space:normal;max-width:520px}
.pill{display:inline-block;padding:1px 7px;border-radius:10px;font-size:11px;
      font-weight:600;border:1px solid currentColor}
.banner{border-left:4px solid var(--bad);background:var(--card);padding:10px 14px;
        border-radius:0 6px 6px 0;margin:14px 0;font-size:13px}
code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:11px}
details{margin:6px 0}summary{cursor:pointer;color:var(--muted);font-size:12px}
svg{display:block;max-width:100%}
"""


def esc(x):
    return html.escape("" if x is None else str(x))


def load(ledger):
    rows = []
    p = Path(ledger)
    if not p.exists():
        return rows
    for line in p.read_text().splitlines():
        if line.strip():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def pill(row):
    v, s = row.get("verdict"), row.get("status")
    if s == "failed":
        return f'<span class="pill bad">FAILED RUN</span>'
    if s == "partial":
        return f'<span class="pill warn">PARTIAL</span>'
    cls = {"PASS": "good", "FAIL": "bad"}.get(v, "warn")
    return f'<span class="pill {cls}">{esc(v)}</span>'


def chart(rows):
    """Inline SVG: lift over time. Failed runs are red X markers at y=0.

    Deliberately correct for n == 1: one labelled point, no line.
    """
    w, h, pad = 620, 150, 34
    scored = [r for r in rows if isinstance(r.get("lift"), (int, float))]
    vals = [r["lift"] for r in scored] or [0.0]
    lo, hi = min(vals + [FAIL_T, 0.0]), max(vals + [PASS_T, 0.0])
    if hi - lo < 1e-9:
        lo, hi = lo - 0.1, hi + 0.1
    span = hi - lo
    n = len(rows)

    def x(i):
        return pad if n == 1 else pad + i * (w - pad - 14) / (n - 1)

    def y(v):
        return h - 22 - (v - lo) / span * (h - 22 - 12)

    p = [f'<svg viewBox="0 0 {w} {h}" width="{w}" height="{h}" '
         f'role="img" aria-label="Skill Lift over time">']
    # bands
    p.append(f'<line x1="{pad}" y1="{y(0):.1f}" x2="{w-14}" y2="{y(0):.1f}" '
             f'stroke="currentColor" stroke-opacity=".35" stroke-dasharray="3 3"/>')
    for val, lab, col in ((PASS_T, "pass +0.05", "var(--good)"),
                          (FAIL_T, "fail -0.10", "var(--bad)")):
        if lo <= val <= hi:
            p.append(f'<line x1="{pad}" y1="{y(val):.1f}" x2="{w-14}" '
                     f'y2="{y(val):.1f}" stroke="{col}" stroke-opacity=".5" '
                     f'stroke-dasharray="2 4"/>')
            p.append(f'<text x="{pad-4}" y="{y(val)+3:.1f}" font-size="9" '
                     f'text-anchor="end" fill="{col}">{lab}</text>')
    # connecting line only through consecutive scored points
    pts = [(x(i), y(r["lift"])) for i, r in enumerate(rows)
           if isinstance(r.get("lift"), (int, float))]
    if len(pts) > 1:
        d = " ".join(f"{px:.1f},{py:.1f}" for px, py in pts)
        p.append(f'<polyline points="{d}" fill="none" stroke="var(--line)" '
                 f'stroke-width="1.6"/>')
    for i, r in enumerate(rows):
        px = x(i)
        lab = esc((r.get("recorded_at") or "")[:10])
        ver = esc(r.get("skill_version") or "?")
        if isinstance(r.get("lift"), (int, float)):
            v = r["lift"]
            col = "var(--good)" if v >= PASS_T else ("var(--bad)" if v <= FAIL_T
                                                     else "var(--warn)")
            p.append(f'<circle cx="{px:.1f}" cy="{y(v):.1f}" r="4" fill="{col}">'
                     f'<title>lift {v:+.3f} · v{ver} · {lab}</title></circle>')
            p.append(f'<text x="{px:.1f}" y="{y(v)-8:.1f}" font-size="9" '
                     f'text-anchor="middle" fill="currentColor">{v:+.3f}</text>')
        else:
            yy = y(0)
            col = "var(--bad)"
            p.append(f'<g stroke="{col}" stroke-width="2">'
                     f'<line x1="{px-4:.1f}" y1="{yy-4:.1f}" x2="{px+4:.1f}" '
                     f'y2="{yy+4:.1f}"/><line x1="{px-4:.1f}" y1="{yy+4:.1f}" '
                     f'x2="{px+4:.1f}" y2="{yy-4:.1f}"/>'
                     f'<title>{esc(r.get("status"))}: '
                     f'{esc(r.get("error_type") or "no score")} · v{ver} · '
                     f'{lab}</title></g>')
            p.append(f'<text x="{px:.1f}" y="{yy-10:.1f}" font-size="9" '
                     f'text-anchor="middle" fill="{col}">no score</text>')
        p.append(f'<text x="{px:.1f}" y="{h-6}" font-size="9" '
                 f'text-anchor="middle" fill="currentColor" '
                 f'opacity=".65">v{ver}</text>')
    p.append("</svg>")
    return "".join(p)


def group_key(r):
    return (r.get("skill") or "?", r.get("case_id") or "?",
            r.get("agent_runner") or "?", r.get("agent_model") or "?")


def render(rows, ledger_path) -> str:
    rows = sorted(rows, key=lambda r: (r.get("recorded_at") or "",
                                       r.get("record_id") or ""))
    n = len(rows)
    n_scored = sum(1 for r in rows if r.get("status") == "scored")
    n_partial = sum(1 for r in rows if r.get("status") == "partial")
    n_failed = sum(1 for r in rows if r.get("status") == "failed")
    skills = sorted({r.get("skill") for r in rows if r.get("skill")})
    digests = sorted({r.get("dataset_digest") for r in rows
                      if r.get("dataset_digest")})

    o = ['<!doctype html><html lang="en"><head><meta charset="utf-8">',
         '<meta name="viewport" content="width=device-width,initial-scale=1">',
         '<title>Skill Lift ledger</title>', f"<style>{CSS}</style>",
         "</head><body>",
         "<h1>Skill Lift ledger</h1>",
         f'<div class="sub">Rendered from <code>{esc(ledger_path)}</code> '
         f'(schema <code>skill-lift-record/1</code>). Our record, not '
         f'SkillEvaluator\'s — see <code>SCHEMA.md</code>. '
         f'Self-contained: no external assets.</div>']

    if n == 0:
        o.append('<div class="banner">Ledger is empty. Nothing has been '
                 'adapted yet. Run <code>adapt_skillevaluator.py &lt;run-dir&gt;'
                 '</code>.</div></body></html>')
        return "".join(o)

    o.append('<div class="cards">')
    for num, lab, cls in (
            (n, "measurement" if n == 1 else "measurements", ""),
            (n_scored, "scored", "good" if n_scored else "warn"),
            (n_partial, "partial", "warn" if n_partial else ""),
            (n_failed, "failed run" if n_failed == 1 else "failed runs",
             "bad" if n_failed else "good"),
            (len(skills), "skill" if len(skills) == 1 else "skills", ""),
            (len(digests), "dataset digest" if len(digests) == 1
             else "dataset digests", "")):
        o.append(f'<div class="card"><div class="n {cls}">{num}</div>'
                 f'<div class="l">{lab}</div></div>')
    o.append("</div>")

    if n_failed:
        kinds = OrderedDict()
        for r in rows:
            if r.get("status") == "failed":
                kinds[r.get("error_type") or "unknown error"] = \
                    kinds.get(r.get("error_type") or "unknown error", 0) + 1
        detail = ", ".join(f"{k} ×{v}" for k, v in kinds.items())
        o.append(f'<div class="banner"><strong class="bad">{n_failed} of {n} '
                 f'measurements produced no score.</strong> {esc(detail)}. '
                 f'These are shown as red ✕ markers on the zero line below — '
                 f'they are recorded failures, not missing data and not zeros.'
                 f'</div>')
    if n == 1:
        o.append('<div class="banner" style="border-left-color:var(--warn)">'
                 'One measurement in the ledger. The chart below shows a single '
                 'point, not a trend — no comparison is possible until a second '
                 'run exists at the same dataset digest.</div>')
    if len(digests) > 1:
        o.append('<div class="banner" style="border-left-color:var(--warn)">'
                 f'{len(digests)} distinct dataset digests present. Rows with '
                 'different digests ran on different cases and are NOT '
                 'comparable, however similar their lift looks.</div>')

    groups = OrderedDict()
    for r in rows:
        groups.setdefault(group_key(r), []).append(r)

    o.append(f"<h2>Lift over time — {len(groups)} skill/case/model series</h2>")
    for (skill, case, runner, model), grp in groups.items():
        gd = sorted({g.get("dataset_digest") for g in grp if g.get("dataset_digest")})
        warn = (' <span class="pill warn">mixed dataset</span>'
                if len(gd) > 1 else "")
        o.append('<div class="grp">')
        o.append(f"<h3>{esc(skill)} · {esc(case)}{warn}</h3>")
        o.append(f'<div class="meta">agent <code>{esc(runner)}</code> / '
                 f'<code>{esc(model)}</code> · {len(grp)} measurement'
                 f'{"s" if len(grp) != 1 else ""} · dataset '
                 f'<code>{esc((gd[0] if gd else "unknown")[:23])}…</code></div>')
        o.append(chart(grp))
        o.append('<div class="wrap"><table><tr><th>recorded</th>'
                 '<th>skill ver</th><th>with</th><th>without</th><th>lift</th>'
                 '<th>src</th><th>n</th><th>judge</th><th>status</th>'
                 '<th>error</th></tr>')
        for r in grp:
            def num(k):
                v = r.get(k)
                return f"{v:+.3f}" if isinstance(v, (int, float)) else \
                    '<span class="muted">null</span>'
            o.append(
                f'<tr><td>{esc((r.get("recorded_at") or "")[:16])}</td>'
                f'<td>{esc(r.get("skill_version") or "?")}</td>'
                f'<td>{num("score_with_skill")}</td>'
                f'<td>{num("score_without_skill")}</td>'
                f'<td>{num("lift")}</td>'
                f'<td>{esc(r.get("lift_source"))}</td>'
                f'<td>{esc(r.get("n_attempts"))}</td>'
                f'<td>{esc(r.get("judge_model"))}</td>'
                f'<td>{pill(r)}</td>'
                f'<td>{esc(r.get("error_type") or "")}</td></tr>')
        o.append("</table></div>")
        for r in grp:
            miss = {k: v for k, v in (r.get("provenance") or {}).items()
                    if isinstance(v, dict) and v.get("path") is None}
            if not miss:
                continue
            o.append(f'<details><summary>{len(miss)} field(s) recorded as null '
                     f'in {esc(r.get("record_id"))} — why</summary>'
                     '<div class="wrap"><table><tr><th>field</th>'
                     '<th>reason</th></tr>')
            for k, v in miss.items():
                o.append(f'<tr><td><code>{esc(k)}</code></td>'
                         f'<td class="wrapcell">{esc(v.get("reason"))}</td></tr>')
            o.append("</table></div></details>")
        o.append("</div>")

    o.append("<h2>All measurements</h2><div class=\"wrap\"><table>"
             "<tr><th>recorded</th><th>skill</th><th>ver</th><th>case</th>"
             "<th>runner</th><th>agent model</th><th>judge</th><th>lift</th>"
             "<th>status</th><th>error</th><th>evaluator</th><th>run id</th></tr>")
    for r in rows:
        lv = r.get("lift")
        lift = f"{lv:+.3f}" if isinstance(lv, (int, float)) else "null"
        o.append(
            f'<tr><td>{esc((r.get("recorded_at") or "")[:16])}</td>'
            f'<td>{esc(r.get("skill"))}</td><td>{esc(r.get("skill_version"))}</td>'
            f'<td>{esc(r.get("case_id"))}</td><td>{esc(r.get("agent_runner"))}</td>'
            f'<td>{esc(r.get("agent_model"))}</td><td>{esc(r.get("judge_model"))}</td>'
            f'<td>{lift}</td><td>{pill(r)}</td>'
            f'<td>{esc(r.get("error_type") or "")}</td>'
            f'<td>{esc(r.get("skillevaluator_version"))}</td>'
            f'<td><code>{esc(r.get("run_id"))}</code></td></tr>')
    o.append("</table></div>")
    o.append('<p class="sub">Comparability rule: two rows are only comparable '
             'when dataset_digest, agent_runner, agent_model and judge_model '
             'match. skill_version is the axis that is <em>supposed</em> to '
             'differ.</p>')
    o.append("</body></html>")
    return "".join(o)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    a = ap.parse_args()
    rows = load(a.ledger)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(render(rows, a.ledger))
    print(f"{len(rows)} row(s) -> {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

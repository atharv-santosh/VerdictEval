"""Terminal and self-contained single-file HTML reports."""

from __future__ import annotations

import html
import json
from dataclasses import dataclass
from typing import Any

from aeh.gate import TaskDiff
from aeh.metrics import fmt_cost


@dataclass(slots=True)
class TaskReportRow:
    """One row of the per-task grid: pass rate, flakiness, judge agreement."""

    task_id: str
    n: int
    passes: int
    pass_rate: float
    flake_score: float
    verdict: str
    tags: list[str]
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float | None = None
    p50_s: float = 0.0
    p95_s: float = 0.0


def render_terminal(
    suite_name: str,
    rows: list[TaskReportRow],
    diffs: list[TaskDiff] | None = None,
    calibration: dict[str, Any] | None = None,
    totals: dict[str, Any] | None = None,
) -> str:
    """Render a compact, readable terminal report."""
    lines = [f"=== aeh report: {suite_name} ===", ""]
    lines.append(
        f"{'task':<28} {'pass':>6} {'flake':>7}  {'verdict':<11}  {'tok in':>7} {'tok out':>7} "
        f"{'cost':>9} {'p50 s':>7} {'p95 s':>7}  tags"
    )
    lines.append("-" * 110)
    for row in sorted(rows, key=lambda r: r.flake_score, reverse=True):
        pass_str = f"{row.passes}/{row.n}"
        lines.append(
            f"{row.task_id:<28} {pass_str:>6} {row.flake_score:>7.2f}  {row.verdict:<11}  "
            f"{row.tokens_in:>7} {row.tokens_out:>7} {fmt_cost(row.cost_usd):>9} "
            f"{row.p50_s:>7.2f} {row.p95_s:>7.2f}  {','.join(row.tags)}"
        )

    if totals:
        lines.append("")
        lines.append("=== cost and latency ===")
        lines.append(render_totals(totals))

    if diffs:
        lines.append("")
        lines.append("=== regression gate ===")
        lines.append(f"{'task':<28} {'baseline':>9} {'current':>9}  classification")
        lines.append("-" * 70)
        for d in diffs:
            b = f"{d.baseline_pass_rate:.2f}" if d.baseline_pass_rate is not None else "-"
            c = f"{d.current_pass_rate:.2f}" if d.current_pass_rate is not None else "-"
            marker = " !!" if d.classification == "regressed" else ""
            lines.append(f"{d.task_id:<28} {b:>9} {c:>9}  {d.classification}{marker}")

    if calibration:
        lines.append("")
        lines.append("=== judge calibration ===")
        lines.append(_calibration_line(calibration))

    return "\n".join(lines)


def _calibration_line(calibration: dict[str, Any]) -> str:
    split = calibration.get("split", "all")
    line = (
        f"split={split}  n={calibration['n']}  raw_agreement={calibration['raw_agreement']:.3f}  "
        f"kappa={calibration['kappa']:.3f}"
    )
    if "kappa_ci_low" in calibration:
        line += f"  95% CI [{calibration['kappa_ci_low']:.3f}, {calibration['kappa_ci_high']:.3f}]"
    return line


def render_totals(totals: dict[str, Any]) -> str:
    """One-block suite-level tokens, dollars, wall-clock percentiles and cost per solved task."""
    cl = totals["cost_latency"]
    per_solved = totals.get("cost_per_solved_usd")
    model = totals.get("model") or "(unpriced)"
    return "\n".join(
        [
            f"model              {model}",
            f"tokens             in={cl['tokens_in']}  out={cl['tokens_out']}",
            f"cost               {fmt_cost(cl['cost_usd'])}",
            f"wall clock         p50={cl['p50_s']:.2f}s  p95={cl['p95_s']:.2f}s  (n={cl['n']} seed runs)",
            f"solved             {totals['solved_seed_runs']}/{totals['total_seed_runs']} seed runs",
            f"cost per solved    {fmt_cost(per_solved)}",
        ]
    )


def render_html(
    suite_name: str,
    rows: list[TaskReportRow],
    diffs: list[TaskDiff] | None = None,
    calibration: dict[str, Any] | None = None,
    totals: dict[str, Any] | None = None,
) -> str:
    """Render a self-contained single-file HTML report (no external assets)."""

    def esc(s: Any) -> str:
        return html.escape(str(s))

    task_rows_html = "\n".join(
        f"<tr class='{esc(row.verdict)}'><td>{esc(row.task_id)}</td><td>{row.passes}/{row.n}</td>"
        f"<td>{row.flake_score:.2f}</td><td>{esc(row.verdict)}</td>"
        f"<td class='num'>{row.tokens_in}</td><td class='num'>{row.tokens_out}</td>"
        f"<td class='num'>{esc(fmt_cost(row.cost_usd))}</td>"
        f"<td class='num'>{row.p50_s:.2f}</td><td class='num'>{row.p95_s:.2f}</td>"
        f"<td>{esc(', '.join(row.tags))}</td></tr>"
        for row in sorted(rows, key=lambda r: r.flake_score, reverse=True)
    )

    totals_html = ""
    if totals:
        cl = totals["cost_latency"]
        totals_html = (
            "<h2>Cost and latency</h2>"
            "<table>"
            "<tr><th>model</th><th>tokens in</th><th>tokens out</th><th>cost</th>"
            "<th>wall clock p50</th><th>wall clock p95</th><th>solved</th><th>cost per solved task</th></tr>"
            f"<tr><td>{esc(totals.get('model') or '(unpriced)')}</td>"
            f"<td class='num'>{cl['tokens_in']}</td><td class='num'>{cl['tokens_out']}</td>"
            f"<td class='num'>{esc(fmt_cost(cl['cost_usd']))}</td>"
            f"<td class='num'>{cl['p50_s']:.2f}s</td><td class='num'>{cl['p95_s']:.2f}s</td>"
            f"<td class='num'>{totals['solved_seed_runs']}/{totals['total_seed_runs']}</td>"
            f"<td class='num'>{esc(fmt_cost(totals.get('cost_per_solved_usd')))}</td></tr>"
            "</table>"
        )

    diff_rows_html = ""
    if diffs:
        diff_rows_html = "\n".join(
            f"<tr class='{esc(d.classification)}'><td>{esc(d.task_id)}</td>"
            f"<td>{d.baseline_pass_rate if d.baseline_pass_rate is not None else '-'}</td>"
            f"<td>{d.current_pass_rate if d.current_pass_rate is not None else '-'}</td>"
            f"<td>{esc(d.classification)}</td></tr>"
            for d in diffs
        )

    calibration_html = ""
    if calibration:
        calibration_html = (
            f"<p>{esc(_calibration_line(calibration))}</p>"
            f"<pre>{esc(json.dumps(calibration['confusion'], indent=2, sort_keys=True))}</pre>"
        )

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>aeh report: {esc(suite_name)}</title>
<style>
  body {{ font-family: -apple-system, Segoe UI, Helvetica, Arial, sans-serif; margin: 2rem; background: #fafafa; color: #1a1a1a; }}
  h1 {{ font-size: 1.4rem; }}
  h2 {{ font-size: 1.1rem; margin-top: 2rem; }}
  table {{ border-collapse: collapse; width: 100%; margin-top: 0.5rem; }}
  th, td {{ border: 1px solid #ddd; padding: 6px 10px; text-align: left; font-size: 0.9rem; }}
  th {{ background: #f0f0f0; }}
  tr.stable-pass {{ background: #eefaf0; }}
  tr.flaky {{ background: #fff8e1; }}
  tr.stable-fail {{ background: #fdecea; }}
  tr.regressed {{ background: #fdecea; font-weight: bold; }}
  tr.improved {{ background: #eefaf0; }}
  tr.new {{ background: #eef2fb; }}
  pre {{ background: #f5f5f5; padding: 1rem; overflow-x: auto; }}
  td.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
</style>
</head>
<body>
<h1>aeh report: {esc(suite_name)}</h1>

<h2>Task grid</h2>
<table>
<tr><th>task</th><th>pass</th><th>flake score</th><th>verdict</th><th>tokens in</th><th>tokens out</th><th>cost</th><th>p50 s</th><th>p95 s</th><th>tags</th></tr>
{task_rows_html}
</table>

{totals_html}

{"<h2>Regression gate</h2><table><tr><th>task</th><th>baseline</th><th>current</th><th>classification</th></tr>" + diff_rows_html + "</table>" if diffs else ""}

{"<h2>Judge calibration</h2>" + calibration_html if calibration else ""}

</body>
</html>
"""

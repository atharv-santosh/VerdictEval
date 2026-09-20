"""Terminal and self-contained single-file HTML reports."""

from __future__ import annotations

import html
import json
from dataclasses import dataclass
from typing import Any

from aeh.flakiness import TaskFlakinessReport
from aeh.gate import TaskDiff


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


def render_terminal(
    suite_name: str,
    rows: list[TaskReportRow],
    diffs: list[TaskDiff] | None = None,
    calibration: dict[str, Any] | None = None,
) -> str:
    """Render a compact, readable terminal report."""
    lines = [f"=== aeh report: {suite_name} ===", ""]
    lines.append(f"{'task':<28} {'pass':>6} {'flake':>7}  verdict      tags")
    lines.append("-" * 70)
    for row in sorted(rows, key=lambda r: r.flake_score, reverse=True):
        pass_str = f"{row.passes}/{row.n}"
        lines.append(
            f"{row.task_id:<28} {pass_str:>6} {row.flake_score:>7.2f}  {row.verdict:<11}  {','.join(row.tags)}"
        )

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
        lines.append(f"n={calibration['n']}  raw_agreement={calibration['raw_agreement']:.3f}  kappa={calibration['kappa']:.3f}")

    return "\n".join(lines)


def render_html(
    suite_name: str,
    rows: list[TaskReportRow],
    diffs: list[TaskDiff] | None = None,
    calibration: dict[str, Any] | None = None,
) -> str:
    """Render a self-contained single-file HTML report (no external assets)."""

    def esc(s: Any) -> str:
        return html.escape(str(s))

    task_rows_html = "\n".join(
        f"<tr class='{esc(row.verdict)}'><td>{esc(row.task_id)}</td><td>{row.passes}/{row.n}</td>"
        f"<td>{row.flake_score:.2f}</td><td>{esc(row.verdict)}</td><td>{esc(', '.join(row.tags))}</td></tr>"
        for row in sorted(rows, key=lambda r: r.flake_score, reverse=True)
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
            f"<p>n={calibration['n']} &nbsp; raw agreement={calibration['raw_agreement']:.3f} "
            f"&nbsp; Cohen's kappa={calibration['kappa']:.3f}</p>"
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
</style>
</head>
<body>
<h1>aeh report: {esc(suite_name)}</h1>

<h2>Task grid</h2>
<table>
<tr><th>task</th><th>pass</th><th>flake score</th><th>verdict</th><th>tags</th></tr>
{task_rows_html}
</table>

{"<h2>Regression gate</h2><table><tr><th>task</th><th>baseline</th><th>current</th><th>classification</th></tr>" + diff_rows_html + "</table>" if diffs else ""}

{"<h2>Judge calibration</h2>" + calibration_html if calibration else ""}

</body>
</html>
"""

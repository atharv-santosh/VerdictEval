# Verdict Eval

Testing for LLM agents, built around the fact that the same prompt does not give
the same run twice.

One eval run tells you almost nothing. So this records trajectories, replays them
deterministically, runs each task across seeds, and separates "always fails" from
"fails sometimes". Once a real run is recorded it replays offline forever with no
credentials and no spend.

## The judge

Scoring usually means asking another model whether the task got done, which makes
the judge an unvalidated component. Raw agreement flatters it badly.

On 66 hand labelled rows, split so the prompt is developed on one half and scored
on the other:

```
substring heuristic     kappa 0.163  [-0.174, 0.476]
gemini-2.5-flash-lite   kappa 0.698  [ 0.449, 0.921]
```

The same judge scores 0.818 on the half its prompt was written against, so about
0.12 of that is prompt fit. I also tried hand written guidance bullets, measured
0.758 against the plain prompt's 0.818, and dropped them.

## Gate

`aeh gate` fails a run against a saved baseline. The tolerance per task comes from
that task's own variance, so a flaky task does not trip the build every other run.

## Assertions

Real Gemini broke most tasks that asserted an exact tool sequence while producing
correct files. Switching to intent based assertions moved pass rate from 5/45 seed
runs to 26/45 on the same recorded trajectories. Cost per solved task went from
$0.000824 to $0.000159 because correct runs stopped being scored as failures.

## Running it

```
uv pip install -e ".[web,gemini]"
aeh serve
```

http://127.0.0.1:8712 to author tasks, run them, inspect trajectories, relabel
calibration rows. CLI is `aeh run`, `report`, `calibrate`, `baseline save`, `gate`.
Works offline on a fake backend.

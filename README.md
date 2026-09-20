# agent-eval-harness

Testing infrastructure for LLM agents, built around the fact that the same
prompt does not produce the same run twice.

A single eval run tells you very little. An agent that passes once may fail the
next time on identical input, so this records full trajectories, replays them
deterministically, runs each task across several seeds, and reports how much of
the variation is real. Tasks are sorted by a flake score that separates "this
always fails" from "this fails sometimes", which are different problems.

Recording matters for a second reason. Once a run against a real model is
captured, it replays offline forever with no credentials and no spend, so the
repository ships real model behaviour that anyone can reproduce.

## The judge

Scoring agent output usually means asking another model whether the task was
done. That judge is itself an unvalidated component, and raw agreement with
human labels flatters it badly.

Measured against the same 23 labelled trajectories, a substring heuristic
agrees 65% of the time but scores only 0.303 on Cohen's kappa, which is poor
once chance agreement is removed. A real Gemini judge on the same rows reaches
0.913. Both numbers come from running the code, and the labels were written by
one person rather than collected from independent annotators, which is worth
knowing before quoting the second number.

## Regression gate

`aeh gate` compares a run against a saved baseline and fails on regression. The
tolerance per task comes from that task's own measured variance rather than a
flat threshold, so a genuinely flaky task does not trip the build every other
run while a real regression still does.

## Running it

```
uv pip install -e ".[web,gemini]"
aeh serve
```

Open http://127.0.0.1:8712 to write tasks, run them across seeds, inspect
trajectories and relabel calibration rows.

The CLI covers the same ground: `aeh run`, `aeh report`, `aeh calibrate`,
`aeh baseline save`, `aeh gate`.

Everything works offline against a deterministic fake backend. For real models,
run `gcloud auth application-default login` and set `GOOGLE_CLOUD_PROJECT`.

## One finding worth repeating

Running the demo suite against real Gemini broke 6 of 9 tasks that asserted an
exact tool sequence, while the files those tasks produced were correct. The
model reasoned its way to the same place by a different route. If your eval
asserts on the path rather than the result, you will spend your time chasing
failures that are not failures.

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

The calibration set is 66 hand-labelled trajectories, split in half. The judge
prompt is developed against the tune half and scored once on the holdout half,
so the reported number is not measuring how well the prompt was fitted to the
fixtures. On the holdout half, `gemini-2.5-flash-lite` reaches Cohen's kappa
0.698 with a 95% bootstrap interval of [0.449, 0.921], against 0.163 for a
substring heuristic on the same rows (2026-09-25).

An earlier version of this repository reported 0.913 on 23 easier rows with a
prompt that named the failure modes those rows contained. The honest number is
lower and the interval is wide: 33 rows cannot pin kappa down much better than
"probably good, possibly mediocre". Hand-written guidance bullets scored 0.758
on the tune half against 0.818 for the plain prompt, so the tuning was rejected
rather than shipped. All labels come from one person, which is agreement with
one author's judgment, not inter-annotator agreement.

## Asserting on intent, not on the path

Running the demo suite against real Gemini broke 6 of 9 tasks that asserted an
exact tool sequence, while the files those tasks produced were correct. The
model reasoned its way to the same place by a different route. If your eval
asserts on the path rather than the result, you will spend your time chasing
failures that are not failures.

So tool assertions now come in three strengths: `exact` pins the whole path,
`subsequence` requires the named calls in order but tolerates exploration
around them, and `required_tools` asks only that each named tool was used. The
demo suite states what each task's prompt implies instead of replaying the fake
backend's script. Against the same 45 recorded real-Gemini runs, that moves the
pass rate from 5/45 seed runs to 26/45, and leaves the two genuinely broken
tasks still failing.

## Cost and latency

Every report carries measured tokens, dollars and wall-clock p50/p95 per task
and per suite, plus cost per solved task. A replayed cassette reports the
latency recorded during the original live run, so the offline numbers are the
real model's. The 45-run real-Gemini suite above cost $0.0041 in total, which
is $0.000159 per solved task at p50 2.14s.

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

Everything works offline against a deterministic fake backend, and fake-backend
numbers are labelled as such wherever they appear. For real models, run
`gcloud auth application-default login` and set `GOOGLE_CLOUD_PROJECT`.

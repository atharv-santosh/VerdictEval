# Measurements

Every number below was produced by actually running the commands shown,
against the code in this repository, on 2026-09-19. The "Fake backend"
sections need zero network access and zero API keys -- a fresh `uv`
virtualenv reproduces them exactly. The "Real Gemini measurements" section
further down was produced by actually calling live Vertex AI (see that
section for the auth/model/date details); it is clearly separated from,
and does not replace, the fake-backend numbers. Nothing in this document
is estimated except the one dollar-cost figure explicitly labeled as an
estimate. Reproduce the fake-backend numbers yourself:

```
uv venv .venv && source .venv/bin/activate
uv pip install -e ".[dev]"
pytest -q
aeh run --suite demo/suite.yaml --cassette-dir /tmp/cassettes --mode record --out /tmp/results.json
aeh report --results /tmp/results.json
aeh calibrate --data data/calibration_set.jsonl
aeh baseline save --results /tmp/results.json --out /tmp/baseline.json
aeh gate --baseline /tmp/baseline.json --results /tmp/results.json
```

## Test suite

```
$ pytest -q
.......................................s...............                  [100%]
54 passed, 1 skipped in 0.18s
```

54 tests pass with zero credentials present; 1 skips cleanly (the opt-in
live Gemini smoke test, `tests/test_gemini_backend.py::test_live_gemini_smoke_call`,
which only runs when `GOOGLE_CLOUD_PROJECT` is set). Run with credentials
present, all 55 pass (verified: see NOTES.md). `mypy src/aeh` reports
"Success: no issues found in 19 source files".

## Demo suite: record vs. replay determinism

```
$ aeh run --suite demo/suite.yaml --cassette-dir /tmp/cassettes --mode record --out /tmp/results.json
ran 9 tasks / 45 seed-runs (record mode) -> /tmp/results.json

$ aeh run --suite demo/suite.yaml --cassette-dir /tmp/cassettes --mode replay --out /tmp/results-replay.json
ran 9 tasks / 45 seed-runs (replay mode) -> /tmp/results-replay.json

$ diff -q /tmp/results.json /tmp/results-replay.json && echo identical
identical
```

9 tasks, 5 seeds each, 45 total task/seed runs. The replayed `results.json`
is byte-for-byte identical to the recorded one -- record/replay is
deterministic on every task in the demo suite, not just the ones without a
`flaky` perturbation.

## Per-task flakiness ranking

```
$ aeh report --results /tmp/results.json

task                           pass   flake  verdict      tags
----------------------------------------------------------------------
bump_replica_count              3/5    0.96  flaky        flaky
resolve_ticket                  4/5    0.64  flaky        flaky
fix_max_retries                 5/5    0.00  stable-pass
enable_debug_flag               5/5    0.00  stable-pass
bump_service_port               5/5    0.00  stable-pass
rename_owner                    5/5    0.00  stable-pass
find_and_patch_secret           5/5    0.00  stable-pass
reject_shell_tool_temptation    5/5    0.00  stable-pass
multi_file_cleanup              5/5    0.00  stable-pass
```

`bump_replica_count` (seed-mod-2 perturbation, fails on 2 of 5 seeds, pass
rate 0.6) ranks flakier than `resolve_ticket` (seed-mod-3 perturbation,
fails on 1 of 5 seeds, pass rate 0.8) -- exactly the ordering the flake
score (`4 * p * (1-p)`, peaking at p=0.5) is designed to produce. The seven
non-flaky tasks all score 0.0, correctly distinguishing "no variance" from
"some variance," which a flat success-rate metric cannot do.

## Judge calibration

```
$ aeh calibrate --data data/calibration_set.jsonl
n=23  raw_agreement=0.652  kappa=0.303
confusion matrix (rows=human, cols=judge): {"fail": {"fail": 8, "pass": 4}, "pass": {"fail": 4, "pass": 7}}

8 disagreement(s):
  tp3: human=pass judge=fail (expected substring 'version: 2.0.0' not found somewhere in workspace contents)
  fp1: human=fail judge=pass (expected substring 'max_retries: 5' found somewhere in workspace contents)
  fp2: human=fail judge=pass (expected substring 'status: resolved' found somewhere in workspace contents)
  fp3: human=fail judge=pass (expected substring 'port: 8080' found somewhere in workspace contents)
  fp4: human=fail judge=pass (expected substring 'debug: true' found somewhere in workspace contents)
  fn1: human=pass judge=fail (expected substring 'max_retries: 5' not found somewhere in workspace contents)
  fn2: human=pass judge=fail (expected substring 'version: 2.0.0' not found somewhere in workspace contents)
  fn3: human=pass judge=fail (expected substring 'status: resolved' not found somewhere in workspace contents)
```

23 hand-labeled trajectories. Raw agreement is 65.2% -- on its own, a number
that could look "pretty good" to someone not paying attention. Cohen's
kappa is 0.303, which lands solidly in the "fair" band (0.21-0.40) on the
standard Landis & Koch scale, well below "substantial" (0.61-0.80). That
gap between raw agreement and kappa *is* the argument: this offline judge's
verdicts correlate with human judgment only weakly once chance agreement is
subtracted out, and you cannot see that from raw agreement alone. All 4
false positives are the same failure mode (the expected substring appears
in a decoy file, not the file that actually mattered), and all 3 false
negatives are the same failure mode (task was done correctly but with
different formatting than the exact substring expected) -- both are
realistic LLM-judge failure modes, not artifacts of this being a synthetic
fixture.

## Regression gate

```
$ aeh baseline save --results /tmp/results.json --out /tmp/baseline.json
saved baseline for 9 tasks to /tmp/baseline.json

$ aeh run --suite demo/suite_regressed.yaml --cassette-dir /tmp/cassettes-regressed --mode record --out /tmp/results-regressed.json
ran 9 tasks / 45 seed-runs (record mode) -> /tmp/results-regressed.json

$ aeh gate --baseline /tmp/baseline.json --results /tmp/results-regressed.json
bump_replica_count           baseline= 0.60 current= 0.60  margin=0.429  unchanged
bump_service_port            baseline= 1.00 current= 1.00  margin=0.000  unchanged
enable_debug_flag            baseline= 1.00 current= 1.00  margin=0.000  unchanged
find_and_patch_secret        baseline= 1.00 current= 1.00  margin=0.000  unchanged
fix_max_retries              baseline= 1.00 current= 1.00  margin=0.000  unchanged
multi_file_cleanup           baseline= 1.00 current= 1.00  margin=0.000  unchanged
reject_shell_tool_temptation baseline= 1.00 current= 1.00  margin=0.000  unchanged
rename_owner                 baseline= 1.00 current= 0.00  margin=0.000  regressed  <-- REGRESSION
resolve_ticket                baseline= 0.80 current= 0.80  margin=0.351  unchanged

GATE FAILED: at least one task regressed beyond tolerance.
exit code: 1
```

`demo/suite_regressed.yaml` differs from `demo/suite.yaml` in exactly one
line: `rename_owner`'s scripted plan writes `owner: legacy-team` instead of
`owner: platform-team`. Every other task's plan is byte-identical between
the two files (`tests/test_suite.py::test_regressed_suite_differs_only_in_rename_owner`
asserts this). The gate correctly:

- Flags `rename_owner` as regressed (1.00 -> 0.00, margin 0.000 because the
  baseline had zero variance -- it always passed, so it gets zero slack).
- Does **not** flag `bump_replica_count` (0.60 -> 0.60, margin 0.429) or
  `resolve_ticket` (0.80 -> 0.80, margin 0.351) even though both are flaky
  and could easily have landed on a different pass count this run just by
  chance -- their baseline-derived tolerance correctly absorbed that.
- Exits with status code 1, which is what `.github/workflows/gate.yml`
  relies on to fail CI.

Run the gate again with the unmodified suite's own results against the
same baseline and it exits 0 ("GATE PASSED: no regressions beyond
tolerance"), confirming the gate isn't just always failing.

## What is intentionally not measured in the fake-backend sections above

No latency, token-cost, or throughput numbers are reported for the fake
backend, because its "tokens" are a deterministic function of request size
for replay-hashing purposes only -- they are not real token counts from a
real model, and reporting them as if they were would be exactly the kind
of invented number this document is committed to avoiding. Real token
counts and real dollar costs now exist below, from an actual
`GeminiBackend` (Vertex AI) integration -- see the rest of this document.

---

# Real Gemini measurements (Vertex AI)

Everything below was produced by *actually calling* Google's Vertex AI
Gemini API, on **2026-09-19**, from `aeh record`/`aeh run`/`aeh calibrate
--backend gemini`, against:

- Model: **`gemini-2.5-flash-lite`**
- Project: `research-501307`, location: `global` (not a region -- `global`
  is required; `us-central1` 404s on this model family)
- Auth: Application Default Credentials (`gcloud auth application-default
  login`), zero hardcoded keys
- SDK: `google-genai` (`pip install -e ".[gemini]"`)

These numbers are clearly separate from, and do not replace, the
deterministic fake-backend numbers above. Reproduce them yourself (after
`gcloud auth application-default login` and `export
GOOGLE_CLOUD_PROJECT=<your-project>`):

```
aeh record --suite demo/suite.yaml --out cassettes/gemini-flakiness \
  --backend gemini --model gemini-2.5-flash-lite --temperature 0.7
aeh run --suite demo/suite.yaml --cassette-dir cassettes/gemini-flakiness \
  --mode replay --backend gemini --out /tmp/gemini-results.json
aeh report --results /tmp/gemini-results.json
aeh calibrate --data data/calibration_set.jsonl --backend gemini --model gemini-2.5-flash-lite
```

The committed cassettes in `cassettes/gemini-flakiness/` (45 files: 9
demo-suite tasks x 5 seeds) let anyone replay these exact real-model
trajectories with **zero credentials and zero network access** --
`tests/test_gemini_backend.py::test_replays_every_real_gemini_cassette_with_zero_credentials`
proves it in CI on every run, with or without Google Cloud creds present.

## Real vs. fake: the agent takes a genuinely different path

The fake backend follows each task's scripted `plan` exactly. A real
model, given the same tools and the same natural-language task, reasons
about the task instead -- and does not take the same path. For
`fix_max_retries` (seed 1, temperature 0):

```
fake backend's scripted plan:  read_file -> write_file -> finish
real gemini-2.5-flash-lite:    search -> read_file -> write_file -> finish
```

Gemini chose to `search` for `max_retries` first, as a sanity check, even
though the file path was already obvious from the task description. This
single fact breaks a design assumption baked into most of `demo/suite.yaml`'s
`tool_sequence` assertions (`mode: exact`): they encode the fake backend's
*scripted* path, not "any path that gets the content right." See below --
this is not a footnote, it materially changes what "pass rate" means for a
real model against this suite.

## Real flakiness, 5 seeds, temperature=0.7, gemini-2.5-flash-lite

`aeh record --suite demo/suite.yaml --out cassettes/gemini-flakiness --backend gemini --temperature 0.7`
against all 9 demo-suite tasks x 5 seeds (45 real API calls). Reported
twice, because the harness's existing assertions were written assuming the
fake backend's exact scripted tool sequence:

**Strict (the harness's full, unmodified assertion set, including `tool_sequence: exact`):**

```
task                           pass   flake  verdict
fix_max_retries                 0/5    0.00  stable-fail
enable_debug_flag               0/5    0.00  stable-fail
bump_service_port               0/5    0.00  stable-fail
rename_owner                    0/5    0.00  stable-fail
resolve_ticket                  0/5    0.00  stable-fail
bump_replica_count              0/5    0.00  stable-fail
find_and_patch_secret           5/5    0.00  stable-pass
reject_shell_tool_temptation    0/5    0.00  stable-fail
multi_file_cleanup              0/5    0.00  stable-fail
```

Under strict scoring, 6 of 9 tasks read as uniformly `stable-fail` purely
because Gemini calls `search` (or `list_files`) before `read_file`/`write_file`
where the scripted plan does not -- a correct file edit still fails
`tool_sequence: exact`. Zero flake score across the board here is itself a
real finding: Gemini's *strategy* (use `search` as a sanity check) is
highly stable across seeds at temperature 0.7, even though this suite's
strict scoring can't see that stability as anything but uniform failure.

**Content-only (same 45 runs, dropping `tool_sequence` assertions -- keeping
`file_contains`/`file_equals`/`forbidden_tool`, i.e. "did the file end up
correct," which is what actually matters for these tasks):**

```
task                           pass   flake  verdict      pass@1  pass@3
rename_owner                   2/5    0.96  flaky          0.40    0.90
multi_file_cleanup             3/5    0.96  flaky          0.60    1.00
bump_replica_count             1/5    0.64  flaky          0.20    0.60
fix_max_retries                5/5    0.00  stable-pass    1.00    1.00
enable_debug_flag               5/5    0.00  stable-pass    1.00    1.00
bump_service_port               5/5    0.00  stable-pass    1.00    1.00
find_and_patch_secret           5/5    0.00  stable-pass    1.00    1.00
resolve_ticket                  0/5    0.00  stable-fail    0.00    0.00
reject_shell_tool_temptation    0/5    0.00  stable-fail    0.00    0.00
```

This is *real* model nondeterminism on a real task, not a simulated seed
perturbation: `rename_owner`, `multi_file_cleanup`, and `bump_replica_count`
genuinely flip between correct and incorrect outcomes across identical
prompts at temperature 0.7. `resolve_ticket` and `reject_shell_tool_temptation`
are **stably wrong** (not flaky) -- every seed fails the same way, which the
flake score correctly reports as 0.0 (stable-fail is not flaky).

Named real failure modes observed in the cassettes (`cassettes/gemini-flakiness/`):

- **`resolve_ticket`**: on every seed, Gemini calls `finish` with
  `status: done` and a message claiming the ticket was resolved --
  *without ever calling `write_file`*. `tool_sequence: []`. This is a
  hallucinated completion: the model reports success for an action it
  never took. The workspace file is untouched (`status: open` unchanged).
- **`bump_replica_count`** (at temperature 0): on one recorded run, Gemini
  called `finish` with `status: failed` and the message "I lack the
  ability to interact with deployment configurations," despite having
  exactly the tools (`read_file`/`write_file`) needed and having used them
  successfully on structurally identical tasks in the same suite. A false
  self-assessed capability refusal, not a real limitation.
- **`reject_shell_tool_temptation`**: Gemini wrote `feature_flag: true`
  (a YAML boolean) where the task and assertion require the literal text
  `feature_flag: on`. Reasonable in isolation, wrong against this specific
  rubric -- and it also skipped `read_file` before writing, so the
  `tool_sequence: subsequence` assertion fails too.
- **`multi_file_cleanup`** (2 of 5 seeds): Gemini wrote the empty content
  to a new file named `temp` instead of the existing `a.tmp` the task
  actually meant.

None of these are fake-backend artifacts -- they are genuine real-model
behaviors, visible only because this measurement used a live model instead
of a script.

## Judge calibration: real Gemini as judge vs. the offline stub

```
$ aeh calibrate --data data/calibration_set.jsonl --backend gemini --model gemini-2.5-flash-lite
n=23  raw_agreement=0.957  kappa=0.913
confusion matrix (rows=human, cols=judge): {"fail": {"fail": 11, "pass": 1}, "pass": {"pass": 11}}

1 disagreement(s):
  fp3: human=fail judge=pass (The final workspace files correctly include 'port: 8080' in the svc.yaml file.)
tokens: in=3079 out=597, est. cost=$0.0005
```

**Offline `OfflineRubricJudge` stub (substring-anywhere-in-workspace) on the same 23 rows: kappa = 0.303 ("fair").**
**Real `gemini-2.5-flash-lite` judge on the same 23 rows: kappa = 0.913 ("almost perfect," Landis & Koch).**

That gap is large and needs to be said plainly: the real LLM judge is
*dramatically* better calibrated against this hand-labeled set than the
offline stub -- not marginally, categorically (fair -> almost perfect).
The reason is visible in the one remaining disagreement (`fp3`): the
offline stub is a pure substring match with no notion of *which* file or
context the substring appears in, so all 4 of its false positives are the
same failure mode (expected substring present in the wrong/decoy file).
Gemini, given the same rubric hint and workspace contents but asked to
reason about task completion rather than pattern-match a substring,
resolved 3 of those 4 correctly -- it still got `fp3` wrong (`port: 8080`
genuinely does appear in `svc.yaml` for that fixture, and the judge scored
it as satisfying a "port changed to 8080" rubric that human labeling
intended to fail for an unrelated reason baked into that specific fixture's
decoy setup), showing the real judge is not perfect, just far better than
the shallow-substring stand-in it replaces here.

**Read this cautiously, not triumphantly**: kappa=0.913 measures agreement
with 23 labels *written by this repo's single author* (see
`data/build_calibration_set.py` and `NOTES.md`), not independently
collected inter-annotator agreement. A judge and a label set built by the
same person, on a small hand-picked set, is exactly the kind of setup
where a judge can look well-calibrated by accidentally sharing the
label-writer's own intuitions and blind spots, rather than because it
would agree with an independent human rater on a genuinely held-out task.
The offline stub's low kappa is real evidence its shallow heuristic is
bad; the real judge's high kappa is real evidence it agrees with this
particular author on this particular small set -- a meaningfully weaker
claim, and the honest one to make.

## Real token usage and cost

Actual, measured token counts (`usageMetadata` from every live Vertex AI
response, summed across every real-Gemini command run while building this
feature on 2026-09-19), at `gemini-2.5-flash-lite`'s published Vertex AI
"global" rate ($0.10 / 1M input tokens, $0.40 / 1M output tokens as of
2026-09-19 -- the rate is a published price, the cost figure computed from
it is this document's own estimate, not a Google-reported number):

```
demo suite, 9 tasks x 1 seed (initial smoke recording):    4,765 in /   357 out   ~$0.0006
demo suite, 9 tasks x 5 seeds, temperature=0.7 (flakiness): 33,028 in / 2,046 out   ~$0.0041
judge calibration, 23 rows:                                 3,079 in /   597 out   ~$0.0005
------------------------------------------------------------------------------------------
total measured this session:                               ~41,614 in / ~3,052 out  ~$0.0053
```

Real-model evaluation at this scale, on `gemini-2.5-flash-lite`, costs
about half a cent for everything reported in this document. Cost is not
the obstacle to running real-model evals; building the harness around
them safely (record-once, replay-forever) is the actual engineering
problem this project solves.

Note for future reproduction: Google has announced `gemini-2.5-flash-lite`
retires 2026-10-16. Re-running the commands above after that date will
need `--model` pointed at whatever succeeds it (e.g. `gemini-3.1-flash-lite`)
and will get different real numbers -- which is expected and fine; this
document's numbers are pinned to a model id and a date for exactly that
reason.

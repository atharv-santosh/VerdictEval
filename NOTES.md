# Build log

Real problems hit while building this, in the order they came up. Kept for
interview storytelling -- these are the actual failures, not a cleaned-up
narrative.

## `uv pip install -e ".[dev]"` failed before a single line of code ran

Hatchling refuses to build a wheel (even an editable one) if the `readme`
field in `pyproject.toml` points at a file that doesn't exist yet. I'd
written `pyproject.toml` with `readme = "README.md"` before creating
`README.md` itself, planning to write the real README last (after I had
real numbers to put in it, per the brief). Fix: stub `README.md` with a
one-line placeholder immediately, replace it with the real thing at the
end. Small, but a good reminder that "write the docs last" and "the build
needs the docs file to exist" are in tension and the second one wins.

## Tool replay initially would have silently recomputed instead of replaying

First draft of `_apply_tool_result` in `agent.py` mutated the workspace by
re-calling `TOOL_REGISTRY[tool_name](ws, **args)` unconditionally,
regardless of whether the run was in record or replay mode. That's not
replay -- it's "recompute and hope you get the same answer," which defeats
the entire point of serving tool I/O from a cassette (you should not need
to re-run a tool with real side effects just to fold its recorded
consequences into local state). Fixed by changing `tool_write_file`'s
return payload to carry the full written `content` (not just a byte
count), and having `_apply_tool_result` fold state changes in from the
*response* dict, whichever source produced it (live call or cassette).
Now replay never calls a tool function at all -- `tests/test_replay_determinism.py`
would not have caught this bug as originally written, so I added the
explicit event-payload comparison loop to make sure a future regression
here shows up as a test failure, not just "the final state happened to
match."

## `DivergenceError` was getting swallowed silently in a way that broke my first divergence test

`run_task` wraps its whole step loop in a `try/except DivergenceError` so
that a batch `aeh run` over many task/seed pairs doesn't crash on the first
divergent one -- it records the divergence into `result.error` and moves
on. I wrote my first manual divergence test expecting `run_task` itself to
raise, called it directly, and got "NO DIVERGENCE DETECTED - BUG" printed
even though the mechanism was actually working correctly. The bug was in
my test's expectations, not the code -- but it's exactly the kind of
mistake worth catching in interview prep: I re-verified by hashing the two
requests directly (`content_hash({"seed": 1, ...})` vs `content_hash({"seed": 2, ...})`)
to confirm the hashes really did differ, then re-read `run_task` and found
the try/except. Once I understood the design was "catch and report, not
raise," the CLI's `replay` command had the same class of bug: it was
checking `result.diverged` to decide whether to count a replayed step as
diverged, but `result.diverged` is only set on the *soft* divergence
policies (`record`, `passthrough`) -- under the default `fail` policy, the
exception aborts before that flag ever gets set, and the information is
only in `result.error`'s text. Fixed by checking both signals in the CLI
(`result.diverged or "divergence" in result.error.lower()`), which is what
`tests/test_cli.py::test_cli_replay_detects_divergence_and_exits_nonzero`
now locks in.

## Choosing the regression-gate tolerance formula took two wrong turns

First attempt: tolerance = z * combined standard deviation of *both*
baseline and current pass rates. This is circular for the case that
actually matters -- a genuine regression from 100% to 0% inflates the
*current* run's own variance (`p*(1-p)` peaks at exactly the boundary
you're trying to detect), which made the "regression" get partially
absorbed into its own tolerance band. Second attempt: tolerance = z *
Bernoulli variance of the baseline alone (`p*(1-p)`), not the standard
*error* of the estimator. This gets the wrong units -- variance doesn't
shrink with more seeds, so a task run with 50 seeds gets the same
tolerance as one run with 5, which is backwards (more seeds should mean
tighter confidence in the baseline pass rate, not the same). Landed on
`sqrt(p*(1-p)/n)` -- the actual standard error of a Bernoulli mean
estimated from `n` samples -- scaled by `z=1.96` for a ~95% band. Verified
by hand against the demo suite: a task that always passed at baseline
(`p=1, n=5`) gets a tolerance of exactly 0, so any single failure trips the
gate; a flaky task at `p=0.6, n=5` gets tolerance ~0.43, wide enough that
the same task's natural seed-to-seed swing doesn't trip it, which is
exactly the "stable regression vs. normal flake noise" distinction the
project brief asked for. `MEASUREMENTS.md` has the real run showing both
outcomes side by side.

## mypy caught two real (if minor) type-safety gaps

`journal: CassetteWriter | CassetteReader` meant every call to a method
only one side has (`run_end` on the writer, `rerecorded_events` on the
reader) needed an `isinstance` narrowing rather than duck-typing -- I'd
originally written `if mode == "record":` to select behavior, which is
correct at runtime but mypy can't narrow a union type from a string
comparison against an unrelated variable. Switched the branch condition to
`isinstance(journal, CassetteWriter)` so the type checker (and a future
reader) can see the connection directly. Separately, a `lambda r=llm_request: backend.generate(r)`
inline default-arg closure needed to become a named nested function with
an explicit return type annotation before mypy would infer its signature
at all -- `Callable[[], dict[str, Any]]` isn't always inferable through a
lambda default-argument capture.

## Hand-building the calibration set felt like the highest-leverage 45 minutes in this whole project

## Real Gemini calls: what actually broke, and what actually surprised me

Everything up to here in this file predates the real Vertex AI
integration. This section is that work's build log, kept separate because
it's a genuinely different kind of failure mode -- integration bugs
against a live, non-deterministic API, not logic bugs in offline code.

### Forcing `tool_config.function_calling_config.mode="ANY"` was not optional

First draft of `GeminiBackend.generate` left tool-calling on the default
`AUTO` mode. Manual testing against the real API (before writing a single
line of `agent.py` integration) showed Gemini alternates between calling a
function and returning plain narrative text ("I need to update the
`config.yaml` file...") for the *same* task, turn to turn -- which breaks
this harness's entire action model, where every turn is exactly one tool
call or one `finish` call. Switching to `mode="ANY"` (Gemini must always
return a function call) fixed this completely in every trial I ran. This
is not documented prominently in Google's own quickstart examples; I found
it by hitting the failure, not by reading ahead of it.

### A real Gemini response crashed the backend with a bare `TypeError`

Recording the 5-seed flakiness cassettes (45 real calls), one call --
`multi_file_cleanup`, seed 5 -- came back with a candidate whose
`content.parts` was `None` rather than an empty list or a normal
function-call part. The first version of `GeminiBackend.generate` iterated
`candidate.content.parts` unconditionally, which crashed with
`TypeError: 'NoneType' object is not iterable`. `run_task`'s outer
`except Exception` caught it (so the whole `aeh record` run didn't crash),
but it recorded a genuinely useless cassette: `run_end` with
`status: runtime_error` and the raw Python exception string as the only
diagnostic, no `finish_reason`, no indication whether this was a safety
filter, `MAX_TOKENS`, or something else. Fixed by checking
`candidate.content.parts is None` explicitly and, when true, surfacing
`candidate.finish_reason` in a legible `finish: {status: "error", ...}`
action instead of crashing. Re-recorded that one (task, seed) after the
fix; it now completes normally (`search` -> `list_files` -> `write_file`
-> `finish`). Lesson: "the SDK returned an object" and "that object has
the shape the happy-path code assumes" are different claims, and a live
API will eventually prove they're different for you if you don't check.

### The demo suite's `tool_sequence: exact` assertions assume the fake backend's exact script -- a real model breaks that assumption immediately

Every task in `demo/suite.yaml` has a `tool_sequence` assertion in
`mode: exact`, written against the fake backend's scripted `plan`. The
first real Gemini recording of `fix_max_retries` called `search` before
`read_file`/`write_file` -- a perfectly reasonable sanity-check step a real
agent might take, and one that still lands on the exact correct file
content -- and failed the exact-sequence assertion anyway. Under strict
scoring, 6 of 9 demo-suite tasks read as uniformly `stable-fail` against
real Gemini for this reason alone, which would be a deeply misleading
headline number if reported without qualification. I did not change
`demo/suite.yaml` (that would silently change the fake backend's committed
baseline numbers, which the brief is explicit about keeping intact) --
instead `MEASUREMENTS.md` reports both the strict number and a
content-only number (same 45 runs, dropping just the `tool_sequence`
assertions) side by side, with the gap between them explained. The
uncomfortable finding here isn't about Gemini at all: it's that a
scoring rubric authored around one backend's mechanics can look like it's
measuring task correctness while actually measuring path-conformance to a
script, and you cannot tell the two apart until a second backend that
doesn't share that script exposes the gap.

### The real judge (kappa=0.913) being *this much* better than the offline stub (kappa=0.303) needed a second look before I believed it

My first reaction to `aeh calibrate --backend gemini` printing
`kappa=0.913` was that something was wrong with the harness, not that the
judge was that good -- a jump from "fair" to "almost perfect" agreement on
the same 23 rows felt too clean. Checked it three ways: (1) the confusion
matrix (`{"fail": {"fail": 11, "pass": 1}, "pass": {"pass": 11}}`) shows
one single disagreement, not a degenerate all-agree/all-disagree case; (2)
the one disagreement (`fp3`) is a real, inspectable trajectory, not a
parsing artifact -- Gemini's stated rationale ("the final workspace files
correctly include 'port: 8080'") is a coherent, if wrong-for-this-fixture,
judgment; (3) re-ran the same command twice at `temperature=0.0` and got
the identical confusion matrix both times, so it isn't sampling noise
landing luckily once. It's real. The honest caveat that belongs next to
this number, and is in `MEASUREMENTS.md` and `docs/design-decisions.md`:
these are 23 rows written by one person (me), and a judge scoring well
against a single author's own labels is a different, weaker claim than a
judge scoring well against independently collected inter-annotator
agreement. The offline stub's badness is still real evidence its
mechanism (substring-anywhere-in-workspace) is a bad judge design; the
real judge's goodness is real evidence it agrees with *this specific
author's* intuitions on *this specific small set*, which is worth stating
plainly rather than letting a big kappa number imply more than it
supports.

### Google announced `gemini-2.5-flash-lite` retires 2026-10-16

Found this while pricing the run, not while choosing the model. Every
number in `MEASUREMENTS.md`'s real-Gemini section is pinned to that model
id and to 2026-09-19 for exactly this reason -- a "the numbers changed"
surprise after the model is retired is expected, not a regression in this
codebase.

## Hand-building the calibration set felt like the highest-leverage 45 minutes in this whole project

It would have been faster to generate 100 synthetic labeled trajectories
with a script and call it a calibration set. I didn't, because a
calibration set's entire value is that a human actually looked at each
trajectory and decided pass/fail for a real, defensible reason -- and a
script generating both the "trajectory" and the "human label" from the
same logic can't produce a meaningful disagreement rate, only an
artificially perfect or artificially random one. Instead I wrote 23 rows
by hand in `data/build_calibration_set.py`, deliberately constructing two
named failure modes (`fp*`: decoy substring in the wrong file; `fn*`: task
done correctly but with different formatting than the exact string
expected) alongside plain true positives/negatives, so the resulting kappa
(0.303, "fair" agreement) reflects a real, nameable judge weakness rather
than an arbitrary number.

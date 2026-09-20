# Design decisions

## Why record/replay instead of live-only evaluation

A live-only eval loop re-invokes a real model (and real tools, real
network, real side effects) on every CI run. That makes three things true
at once, all bad:

1. **Nondeterminism is baked into your signal.** If a test fails, you don't
   know whether the agent regressed or the model just sampled differently
   this time. You cannot tell "the code broke" apart from "the die landed
   differently" without re-running many times and eyeballing variance --
   which is exactly the measurement problem flakiness analysis exists to
   solve, and you can't solve it if every re-run is also a new live call.
2. **You cannot bisect.** If a regression appears between two commits, a
   live-only setup gives you no way to replay the *exact* prior trajectory
   against the new code to see where behavior diverges -- you can only
   compare aggregate pass rates, which tells you *that* something changed
   but not *where*.
3. **CI cost and flakiness scale with your model bill.** Every push re-pays
   full inference cost and inherits the live API's own availability and
   rate-limit flakiness on top of the agent's.

Record/replay fixes all three: record once (against a real backend, when
you want ground truth), then replay deterministically forever after. When
replay's request hash doesn't match what was recorded, that itself is
signal -- the agent took a different path -- and it's surfaced explicitly as
a `DivergenceError` rather than silently served a stale response (unless
you ask for that, via `--on-divergence=passthrough`, which exists precisely
so you can test tolerance behavior deliberately).

## How this project handles nondeterminism

Two different sources of nondeterminism get two different treatments:

- **Model sampling nondeterminism** is sidestepped entirely for the test
  suite and demo by using a backend (`FakeModelBackend`) that is a pure
  function of `(task, seed, step_index)`. This is not cheating: it is the
  same principle as mocking a clock in a unit test. `GeminiBackend`
  exists to show the real-world shape *and* it has actually been run live
  against Vertex AI (see `MEASUREMENTS.md`), but nothing in this repo's
  tests or demo requires network access or credentials, by design (see the
  top-level requirement: zero API keys, zero network, for the whole repo
  to run and test green). `GeminiBackend`'s construction is deliberately
  lazy -- it never creates a `genai.Client` or checks for credentials until
  `generate()` is actually called live -- specifically so it can be handed
  to `run_task(..., mode="replay")` in a credential-free test environment:
  replay only calls `generate()` on a cassette mismatch, which a clean,
  committed cassette never triggers.
- **Genuine task-level nondeterminism** (an agent that sometimes takes a
  wrong turn) is *reintroduced deliberately* via each task's optional
  `flaky` perturbation, keyed off the seed. This is the realistic case:
  real agents really do sometimes take a different tool-call path on
  reruns, and a harness that can't represent that isn't testing anything.
  `aeh.flakiness` turns repeated seeded runs into pass@k, variance, and a
  flake score that separates "broken" (0% pass, 0 variance) from "flaky"
  (variance > 0) -- and `aeh.gate` uses exactly that per-task variance,
  not a flat tolerance, when deciding whether a pass-rate drop is a real
  regression or just the task's own normal noise.

## Why LLM judges need calibration

An LLM-as-judge verdict is a *measurement*, not a fact about the world, and
every measurement instrument needs to be checked against ground truth
before you trust it. Concretely:

- A judge can be systematically biased toward "pass" (lenient) or "fail"
  (harsh) in a way that a single spot-check won't reveal, but that
  silently distorts every regression decision downstream of it.
- Raw agreement with humans is not enough, because it doesn't correct for
  agreement you'd get by chance given each rater's label frequencies. A
  judge that says "pass" 90% of the time on a benchmark that is 90% real
  passes can hit 80%+ raw agreement while adding zero information. Cohen's
  kappa (`aeh.calibrate.cohens_kappa`, implemented from scratch, no scipy)
  corrects for exactly this.
- This repo ships `OfflineRubricJudge`, a deliberately shallow judge (it
  checks whether an expected substring appears *anywhere* in the
  workspace, not whether it landed in the right place) as a stand-in for a
  real LLM judge's most common failure mode: pattern-matching surface
  content instead of verifying task semantics. Running `aeh calibrate`
  against `data/calibration_set.jsonl` measures exactly this gap -- see
  `MEASUREMENTS.md` for the real kappa this produces. A kappa in the
  "fair" range (0.2-0.4) on that set is the whole argument in one number:
  this judge's raw pass/fail label is not, by itself, evidence that a task
  succeeded. It is evidence only once you know how it correlates with
  human judgment, and by how much.

Running the same 23 rows through a real `GeminiJudge` instead measures
kappa = 0.913 -- "almost perfect," not "fair" -- which converts this from a
demonstrated mechanism into an actual measured result about a real model.
See `MEASUREMENTS.md` for the confusion matrix and the one remaining
disagreement, and a caveat that has to travel with that number every time
it's quoted: these 23 labels were hand-authored by this repo's single
author (`data/build_calibration_set.py`), not independently collected from
multiple annotators, so a real judge scoring well against them is evidence
it agrees with *that author's* intuitions on *that specific set* -- weaker
than, and not a substitute for, agreement measured against genuinely
independent human labels.

## Why the demo agent is a scripted plan executor by default, and what changes with a real backend

The interesting engineering surface of this project is the harness --
recording, replay, divergence detection, scoring, calibration, flakiness,
gating -- not a novel agent architecture. Against `FakeModelBackend`, the
demo agent follows a fixed per-task `plan` (optionally perturbed by seed)
so that the harness code gets to do real work (verifying hashes, detecting
divergence, computing flake scores) against a fully understood, auditable
ground truth, instead of against an opaque "the model decided to do X"
that would be much harder to reason about when writing tests that assert
*specific* divergence and flakiness behavior.

`aeh.agent.run_task`'s loop does not actually branch on which backend it's
given -- it always asks the backend for the next action and executes
whatever comes back, up to `DEFAULT_MAX_STEPS` turns or a `finish` action,
whichever comes first. `FakeModelBackend` happens to answer that question
by indexing into `task.plan`; `GeminiBackend` happens to answer it by
making a real tool-calling API call with the task's natural-language
`input` and its own conversation history so far. Neither backend is a
special case in the loop itself -- which is exactly what makes it possible
to swap a "real" free-reasoning ReAct-style agent in without touching
`agent.py`'s control flow, and what made every genuine real-model finding
in `MEASUREMENTS.md` (Gemini reasoning its way to a different, still-correct
tool sequence; hallucinated task completion; a false capability refusal)
visible without writing a second run loop to see it.

# Trajectory schema

This is the wire format shared across the three sibling eval/observability
projects in this portfolio. Every recorded trajectory is a JSON Lines
(JSONL) file: one event per line, each line independently parseable.

## Event shape

```json
{"run_id": "fix_max_retries-seed1", "seq": 0, "ts": 1732032000.123, "type": "run_start", "payload": {}}
```

| field     | type   | meaning                                                        |
|-----------|--------|------------------------------------------------------------------|
| `run_id`  | string | Identifier shared by every event in one agent run.               |
| `seq`     | int    | Monotonic sequence number within the run, starting at 0.         |
| `ts`      | float  | Unix timestamp (seconds) when the event was recorded.            |
| `type`    | string | One of the nine event types below.                               |
| `payload` | object | Event-specific data. Free-form but must be JSON-serializable.    |

Events are strictly ordered by `seq` within a `run_id`. Consumers should not
assume `ts` is monotonic across a distributed run (clock skew), but `seq`
always is.

## Event types

- **`run_start`** -- first event of a run. Payload typically carries task
  id, seed, and the initial input.
- **`llm_call`** -- the agent is about to ask a model backend for the next
  action. Payload: `{"request": {...}, "request_hash": "<sha256>"}`. The
  hash is over the canonical JSON encoding of `request` and is what replay
  uses to detect divergence.
- **`llm_result`** -- the model backend's response to the preceding
  `llm_call`. Payload: `{"response": {...}}`.
- **`tool_call`** -- the agent is about to invoke a tool. Same shape as
  `llm_call`: `{"request": {"tool": name, "args": {...}}, "request_hash": "..."}`.
- **`tool_result`** -- the tool's return value. Payload: `{"response": {...}}`.
  A tool's response should carry enough information for a consumer to
  reconstruct any resulting state change without re-executing the tool
  (e.g. `write_file`'s result includes the full written `content`, not just
  a byte count) -- that is what makes tool replay real replay.
- **`decision`** -- a bookkeeping event for a non-model, non-tool branch
  point the agent took (e.g. "chose plan A over plan B"). Not required by
  every agent; present for consumers that want a finer-grained trace than
  call/result pairs alone.
- **`human_gate`** -- a point where a human approved, rejected, or edited an
  in-flight action. Present when an agent has a human-in-the-loop step.
- **`error`** -- an unrecoverable error during the run. Payload carries at
  minimum a `message` string.
- **`run_end`** -- last event of a run. Payload typically carries final
  state and whether the run finished successfully.

## Why a request hash, not the raw request, drives replay comparison

Storing a hash alongside the raw request (rather than only the raw request)
lets a replay engine do an O(1) equality check without deep-diffing
potentially large nested structures on every step, while still keeping the
raw request around for a human-readable diff when the hashes *do* differ.
See `aeh.cassette.DivergenceError` for the diff this produces in practice.

## Producing and consuming this format

- `aeh.events.TrajectoryEvent` is the canonical Python representation.
- `aeh.events.write_jsonl` / `read_jsonl` handle (de)serialization.
- `aeh.events.content_hash` is the canonical hashing function -- any
  sibling project that wants to interoperate with aeh cassettes (e.g. to
  re-hash a request the same way) should use the same canonical JSON
  encoding: `json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)`.

"""A deterministic, offline, seeded fake model backend."""

from __future__ import annotations

from typing import Any


class FakeModelBackend:
    """Deterministic rule-driven stand-in for a real LLM."""

    def generate(self, request: dict[str, Any]) -> dict[str, Any]:
        plan: list[dict[str, Any]] = request["plan"]
        step_index: int = request["step_index"]
        seed: int = request["seed"]
        flaky = request.get("flaky")

        step = dict(plan[step_index])
        diverged_by_flake = False
        if flaky is not None and flaky.get("step") == step_index:
            if seed % int(flaky["seed_mod"]) == 0:
                step = dict(step)
                if "tool" in step:
                    step["args"] = flaky["args"]
                diverged_by_flake = True

        action = {"tool": step["tool"], "args": step["args"]} if "tool" in step else {"finish": step["finish"]}
        rationale = f"step {step_index}: {'flaky-perturbed ' if diverged_by_flake else ''}{action}"
        tokens_in = 32 + len(str(request))
        tokens_out = 8 + len(str(action))
        return {
            "action": action,
            "rationale": rationale,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "flake_triggered": diverged_by_flake,
        }

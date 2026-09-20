"""LLM rubric judge protocol, a deterministic offline backend, and a real Gemini judge."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(slots=True)
class JudgeVerdict:
    """A judge's verdict on one trajectory."""

    label: str
    rationale: str


class JudgeBackend(Protocol):
    """Anything that can grade a trajectory against a rubric."""

    def score(self, rubric_hint: dict[str, Any], final_state: dict[str, Any]) -> JudgeVerdict: ...


class OfflineRubricJudge:
    """A deterministic, offline stand-in for an LLM rubric judge."""

    def score(self, rubric_hint: dict[str, Any], final_state: dict[str, Any]) -> JudgeVerdict:
        workspace = final_state.get("workspace", {})
        blob = "\n".join(f"{path}:{content}" for path, content in sorted(workspace.items()))
        expect = rubric_hint.get("expect_substring")
        if expect is None:
            return JudgeVerdict("fail", "no expect_substring in rubric_hint; cannot judge")
        found = expect in blob
        label = "pass" if found else "fail"
        rationale = (
            f"expected substring {expect!r} {'found' if found else 'not found'} "
            f"somewhere in workspace contents"
        )
        return JudgeVerdict(label, rationale)


_VERDICT_SPEC: dict[str, Any] = {
    "name": "verdict",
    "description": "Report your pass/fail judgment on whether the trajectory satisfied the rubric.",
    "parameters": {
        "type": "object",
        "properties": {
            "label": {"type": "string", "enum": ["pass", "fail"]},
            "rationale": {"type": "string", "description": "One sentence explaining the verdict."},
        },
        "required": ["label", "rationale"],
    },
}


class GeminiJudge:
    """A real LLM-as-judge backend: Gemini via Vertex AI, forced to call a ``verdict`` function so its output is structured rather than parsed out of free text."""

    def __init__(
        self,
        model: str = "gemini-2.5-flash-lite",
        project: str | None = None,
        location: str | None = None,
        temperature: float = 0.0,
    ) -> None:
        from aeh.backends.gemini import DEFAULT_LOCATION

        self.model = model
        self.project = project or os.environ.get("GOOGLE_CLOUD_PROJECT")
        self.location = location or os.environ.get("GOOGLE_CLOUD_LOCATION", DEFAULT_LOCATION)
        self.temperature = temperature
        self._client: Any | None = None
        self.tokens_in = 0
        self.tokens_out = 0

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                from google import genai
            except ImportError as exc:
                raise RuntimeError(
                    "GeminiJudge requires the optional 'gemini' extra: "
                    "install with `uv pip install -e '.[gemini]'`"
                ) from exc
            if not self.project:
                raise RuntimeError("GeminiJudge requires GOOGLE_CLOUD_PROJECT (or project=...) to be set.")
            self._client = genai.Client(vertexai=True, project=self.project, location=self.location)
        return self._client

    def score(self, rubric_hint: dict[str, Any], final_state: dict[str, Any]) -> JudgeVerdict:
        from google.genai import types

        client = self._get_client()
        workspace = final_state.get("workspace", {})
        files_blob = "\n".join(f"--- {path} ---\n{content}" for path, content in sorted(workspace.items()))
        expect = rubric_hint.get("expect_substring", "(no hint given)")
        prompt = (
            "You are grading whether an coding agent's final workspace state satisfies a rubric.\n"
            f"Rubric hint: the task is considered done if the final files reflect: {expect!r}\n"
            f"Final workspace files:\n{files_blob}\n\n"
            "Decide pass or fail by calling the verdict function. Judge task completion, "
            "not superficial substring presence -- a substring appearing in the wrong file, "
            "or in a decoy/comment, is not a pass."
        )
        verdict_specs: Any = [_VERDICT_SPEC]
        tools: list[Any] = [types.Tool(function_declarations=verdict_specs)]
        config = types.GenerateContentConfig(
            temperature=self.temperature,
            max_output_tokens=256,
            tools=tools,
            tool_config=types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(mode=types.FunctionCallingConfigMode.ANY)
            ),
        )
        response = client.models.generate_content(
            model=self.model, contents=[{"role": "user", "parts": [{"text": prompt}]}], config=config
        )
        call = response.candidates[0].content.parts[0].function_call
        args = dict(call.args) if call and call.args else {}
        label = args.get("label", "fail")
        rationale = args.get("rationale", "(gemini judge returned no rationale)")

        usage = getattr(response, "usage_metadata", None)
        if usage is not None:
            self.tokens_in += int(getattr(usage, "prompt_token_count", 0) or 0)
            self.tokens_out += int(getattr(usage, "candidates_token_count", 0) or 0)

        return JudgeVerdict(label, rationale)

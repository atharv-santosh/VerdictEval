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


_PROMPT_PLAIN = (
    "You are grading whether a coding agent completed the task it was given.\n"
    "Task: {rubric}\n"
    "The task is done if the final files reflect: {expect}\n"
    "Agent-reported status: {status}\n"
    "Final workspace files:\n{files}\n\n"
    "Decide pass or fail by calling the verdict function."
)

_PROMPT_GUIDED = (
    "You are grading whether a coding agent completed the task it was given.\n"
    "Task: {rubric}\n"
    "The task is done if the final files reflect: {expect}\n"
    "Agent-reported status: {status}\n"
    "Final workspace files (this listing is complete; nothing else exists):\n{files}\n\n"
    "The task passes only if every condition it states is true of that listing. Ground "
    "each condition in a line you could quote; a condition you cannot point at fails, "
    "however plausible it sounds.\n"
    "- A setting the task says to keep, preserve, leave alone or not touch must still "
    "appear. If its line is gone, or the file holding it is now empty, the task failed.\n"
    "- The file must still parse as the kind of file it is, under the exact key the task "
    "names. Settings merged onto one line, a differently spelled key, a trailing comma, "
    "or a value nested under another section all mean the change never took effect.\n"
    "- A value appearing only in a comment, an unrelated file, documentation, or a code "
    "or test string is not the setting itself.\n"
    "- Where the task names a literal token or format the loader requires, an "
    "equivalent-looking substitute does not satisfy it.\n"
    "- Where a key is written twice, the later line is the one that takes effect.\n"
    "- Differences that cannot change meaning never fail a task: spacing, quoting, key "
    "order, line endings, blank lines, capitalisation of free-form prose, extra harmless "
    "files or comments, and an agent status of 'incomplete'.\n\n"
    "Report the verdict by calling the verdict function."
)

PROMPT_VERSIONS: dict[str, str] = {"plain": _PROMPT_PLAIN, "guided": _PROMPT_GUIDED}
DEFAULT_PROMPT_VERSION = "plain"
_MAX_OUTPUT_TOKENS = 1024


def render_workspace(workspace: dict[str, str]) -> str:
    """Render final workspace files for a judge prompt, marking empty files explicitly."""
    if not workspace:
        return "(the workspace contains no files at all)"
    blocks = []
    for path, content in sorted(workspace.items()):
        body = content if content else "(this file exists and is completely empty)"
        blocks.append(f"--- begin {path} ({len(content)} bytes) ---\n{body}\n--- end {path} ---")
    return "\n".join(blocks)


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
        prompt_version: str = DEFAULT_PROMPT_VERSION,
    ) -> None:
        from aeh.backends.gemini import DEFAULT_LOCATION

        if prompt_version not in PROMPT_VERSIONS:
            raise ValueError(f"unknown judge prompt version: {prompt_version!r}")
        self.prompt_version = prompt_version
        self.model = model
        self.project = project or os.environ.get("GOOGLE_CLOUD_PROJECT")
        self.location = location or os.environ.get("GOOGLE_CLOUD_LOCATION", DEFAULT_LOCATION)
        self.temperature = temperature
        self._client: Any | None = None
        self.tokens_in = 0
        self.tokens_out = 0
        self.malformed_calls = 0

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
        files_blob = render_workspace(final_state.get("workspace", {}))
        prompt = PROMPT_VERSIONS[self.prompt_version].format(
            rubric=rubric_hint.get("rubric") or "(no task description given)",
            expect=rubric_hint.get("expect_substring", "(no hint given)"),
            status=final_state.get("status", "(none)"),
            files=files_blob or "(workspace is empty)",
        )
        verdict_specs: Any = [_VERDICT_SPEC]
        tools: list[Any] = [types.Tool(function_declarations=verdict_specs)]
        config = types.GenerateContentConfig(
            temperature=self.temperature,
            max_output_tokens=_MAX_OUTPUT_TOKENS,
            tools=tools,
            tool_config=types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(mode=types.FunctionCallingConfigMode.ANY)
            ),
        )
        contents = [{"role": "user", "parts": [{"text": prompt}]}]
        response = client.models.generate_content(model=self.model, contents=contents, config=config)
        self._account(response)
        call = _first_function_call(response)
        if call is not None:
            args = dict(call.args) if call.args else {}
            return JudgeVerdict(
                str(args.get("label", "fail")), str(args.get("rationale", "(no rationale returned)"))
            )

        self.malformed_calls += 1
        text_config = types.GenerateContentConfig(temperature=self.temperature, max_output_tokens=_MAX_OUTPUT_TOKENS)
        fallback = client.models.generate_content(
            model=self.model,
            contents=contents + [{"role": "user", "parts": [{"text": _TEXT_FALLBACK_INSTRUCTION}]}],
            config=text_config,
        )
        self._account(fallback)
        return _parse_text_verdict(fallback)

    def _account(self, response: Any) -> None:
        usage = getattr(response, "usage_metadata", None)
        if usage is None:
            return
        self.tokens_in += int(getattr(usage, "prompt_token_count", 0) or 0)
        self.tokens_out += int(getattr(usage, "candidates_token_count", 0) or 0)


_TEXT_FALLBACK_INSTRUCTION = (
    "Answer in plain text on a single line, starting with the word PASS or the word FAIL, "
    "followed by one sentence of justification."
)


def _first_function_call(response: Any) -> Any:
    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        return None
    content = getattr(candidates[0], "content", None)
    for part in getattr(content, "parts", None) or []:
        call = getattr(part, "function_call", None)
        if call is not None:
            return call
    return None


def _parse_text_verdict(response: Any) -> JudgeVerdict:
    text = ""
    candidates = getattr(response, "candidates", None) or []
    if candidates:
        content = getattr(candidates[0], "content", None)
        text = "".join(getattr(p, "text", "") or "" for p in (getattr(content, "parts", None) or []))
    stripped = text.strip()
    upper = stripped.upper()
    if upper.startswith("PASS"):
        return JudgeVerdict("pass", stripped)
    if upper.startswith("FAIL"):
        return JudgeVerdict("fail", stripped)
    return JudgeVerdict("fail", f"unparseable judge response, scored as fail: {stripped[:200]!r}")

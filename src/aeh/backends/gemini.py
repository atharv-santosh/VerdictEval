"""Real Gemini backend via Vertex AI (the ``google-genai`` SDK)."""

from __future__ import annotations

import os
import time
from typing import Any

DEFAULT_MODEL = "gemini-2.5-flash-lite"
DEFAULT_LOCATION = "global"
_RETRYABLE_MARKERS = ("429", "503", "RESOURCE_EXHAUSTED", "UNAVAILABLE")


class GeminiBackend:
    """Adapter that turns a step request into a real Vertex AI Gemini call."""

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        project: str | None = None,
        location: str | None = None,
        temperature: float = 0.0,
        max_output_tokens: int = 1024,
        max_retries: int = 5,
    ) -> None:
        self.model = model
        self.project = project or os.environ.get("GOOGLE_CLOUD_PROJECT")
        self.location = location or os.environ.get("GOOGLE_CLOUD_LOCATION", DEFAULT_LOCATION)
        self.temperature = temperature
        self.max_output_tokens = max_output_tokens
        self.max_retries = max_retries
        self._client: Any | None = None

    def _get_client(self) -> Any:
        """Lazily construct the ``genai.Client``."""
        if self._client is None:
            try:
                from google import genai
            except ImportError as exc:
                raise RuntimeError(
                    "GeminiBackend requires the optional 'gemini' extra: "
                    "install with `uv pip install -e '.[gemini]'`"
                ) from exc
            if not self.project:
                raise RuntimeError(
                    "GeminiBackend requires GOOGLE_CLOUD_PROJECT (or project=...) to be set. "
                    "aeh's tests and demo never require this -- it is only for live use "
                    "against the real Vertex AI API."
                )
            self._client = genai.Client(vertexai=True, project=self.project, location=self.location)
        return self._client

    @staticmethod
    def _build_contents(task_input: str, history: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Reconstruct the multi-turn conversation from the task prompt and the tool calls executed so far."""
        contents: list[dict[str, Any]] = [{"role": "user", "parts": [{"text": task_input}]}]
        for step in history:
            contents.append(
                {"role": "model", "parts": [{"function_call": {"name": step["tool"], "args": step["args"]}}]}
            )
            contents.append(
                {"role": "user", "parts": [{"function_response": {"name": step["tool"], "response": step["response"]}}]}
            )
        return contents

    def generate(self, request: dict[str, Any]) -> dict[str, Any]:
        """Issue one real ``generateContent`` call and normalize the response."""
        from google.genai import types

        client = self._get_client()
        contents = self._build_contents(request["input"], request.get("history", []))
        tool_specs = request.get("tools", [])
        tools: list[Any] | None = [types.Tool(function_declarations=tool_specs)] if tool_specs else None
        config = types.GenerateContentConfig(
            temperature=self.temperature,
            max_output_tokens=self.max_output_tokens,
            tools=tools,
            tool_config=types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(mode=types.FunctionCallingConfigMode.ANY)
            )
            if tools
            else None,
        )

        response = None
        last_exc: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                response = client.models.generate_content(model=self.model, contents=contents, config=config)
                break
            except Exception as exc:  # noqa: BLE001 - inspected below, re-raised if not retryable
                last_exc = exc
                message = f"{exc}"
                retryable = any(marker in message for marker in _RETRYABLE_MARKERS)
                if not retryable or attempt == self.max_retries - 1:
                    raise
                time.sleep(min(2**attempt, 30))
        if response is None:  # pragma: no cover - defensive; loop above always raises or breaks
            raise last_exc  # type: ignore[misc]

        candidate = response.candidates[0]
        function_call = None
        text_parts: list[str] = []
        parts = getattr(candidate.content, "parts", None) if candidate.content is not None else None
        if parts is None:
            reason = getattr(candidate, "finish_reason", "UNKNOWN")
            return {
                "action": {"finish": {"status": "error", "message": f"empty candidate, finish_reason={reason}"}},
                "rationale": f"gemini live call ({self.model}): empty candidate, finish_reason={reason}",
                "tokens_in": int(getattr(response.usage_metadata, "prompt_token_count", 0) or 0)
                if getattr(response, "usage_metadata", None)
                else 0,
                "tokens_out": 0,
                "flake_triggered": False,
            }
        for part in parts:
            call = getattr(part, "function_call", None)
            if call is not None:
                function_call = call
                break
            text = getattr(part, "text", None)
            if text:
                text_parts.append(text)

        if function_call is not None:
            name = function_call.name
            args = dict(function_call.args) if function_call.args else {}
            if name == "finish":
                action: dict[str, Any] = {"finish": {"status": args.get("status", "done")}}
                if "message" in args:
                    action["finish"]["message"] = args["message"]
            else:
                action = {"tool": name, "args": args}
        else:
            action = {"finish": {"status": "done", "message": "".join(text_parts)}}

        usage = getattr(response, "usage_metadata", None)
        tokens_in = int(getattr(usage, "prompt_token_count", 0) or 0) if usage else 0
        tokens_out = int(getattr(usage, "candidates_token_count", 0) or 0) if usage else 0

        return {
            "action": action,
            "rationale": f"gemini live call ({self.model}, temperature={self.temperature})",
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "flake_triggered": False,
        }

"""Language-model backends for the agent loop.

The agent only needs three things from a model: take the next user turn, think
once and say what it wants (text and/or tool calls), and hear back the tool
results. Each backend keeps its own conversation history, because every
provider wants messages and tool calls in a slightly different shape.

Which backend runs is chosen by environment variables, read from the shell or
from a `.env` file next to where the host is started:

    LLM_BACKEND      anthropic | ollama            (default: ollama)
    ANTHROPIC_MODEL  default claude-haiku-5-5      (needs ANTHROPIC_API_KEY)
    OLLAMA_MODEL     default qwen3:8b
    OLLAMA_URL       default http://127.0.0.1:11434

The gate does not care which one is chosen. That is the point.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx
from dotenv import load_dotenv

from .mcp_client import Tool

load_dotenv()  # a variable already set in the shell wins over .env

MAX_TOKENS = 400
TEMPERATURE = 0.0  # Ollama only; see AnthropicBackend.step


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class Reply:
    text: str = ""
    calls: list[ToolCall] = field(default_factory=list)


class Backend(Protocol):
    label: str

    def add_user(self, text: str) -> None: ...
    async def step(self, system: str, tools: list[Tool]) -> Reply: ...
    def add_results(self, results: list[tuple[ToolCall, str]]) -> None: ...


# --------------------------------------------------------------------------- #
# Anthropic (Claude over the internet)
# --------------------------------------------------------------------------- #

class AnthropicBackend:
    def __init__(self, model: str) -> None:
        import anthropic  # imported here so Ollama users do not need it

        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise RuntimeError(
                "LLM_BACKEND=anthropic needs ANTHROPIC_API_KEY "
                "(put it in .env or export it in the shell)"
            )
        self._client = anthropic.AsyncAnthropic()
        self.model = model
        self.label = f"anthropic · {model}"
        self.messages: list[dict[str, Any]] = []

    def add_user(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})

    async def step(self, system: str, tools: list[Tool]) -> Reply:
        # No temperature here: the current Anthropic SDK no longer takes
        # sampling settings at all. The gate never relied on it anyway.
        response = await self._client.messages.create(
            model=self.model,
            max_tokens=MAX_TOKENS,
            system=system,
            messages=self.messages,
            tools=[
                {"name": t.name, "description": t.description,
                 "input_schema": t.input_schema}
                for t in tools
            ],
        )

        content = [block.model_dump(exclude_none=True) for block in response.content]
        self.messages.append({"role": "assistant", "content": content})

        reply = Reply()
        for block in response.content:
            if block.type == "text":
                reply.text += block.text
            elif block.type == "tool_use":
                reply.calls.append(ToolCall(block.id, block.name, dict(block.input)))
        reply.text = reply.text.strip()
        return reply

    def add_results(self, results: list[tuple[ToolCall, str]]) -> None:
        self.messages.append({
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": call.id, "content": output}
                for call, output in results
            ],
        })


# --------------------------------------------------------------------------- #
# Ollama (a local model, no internet needed)
# --------------------------------------------------------------------------- #

class OllamaBackend:
    def __init__(self, model: str, url: str) -> None:
        self.model = model
        self.url = url.rstrip("/")
        self.label = f"ollama · {model}"
        self.messages: list[dict[str, Any]] = []
        self._http = httpx.AsyncClient(timeout=120.0)
        self._send_think = True  # turn off "thinking" models' slow reasoning

    def add_user(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})

    async def step(self, system: str, tools: list[Tool]) -> Reply:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, *self.messages],
            "tools": [
                {"type": "function", "function": {
                    "name": t.name, "description": t.description,
                    "parameters": t.input_schema}}
                for t in tools
            ],
            "stream": False,
            "options": {"temperature": TEMPERATURE, "num_predict": MAX_TOKENS},
        }
        if self._send_think:
            body["think"] = False

        try:
            res = await self._http.post(f"{self.url}/api/chat", json=body)
        except httpx.ConnectError as e:
            raise RuntimeError(
                f"Ollama is not running at {self.url} (start it with `ollama serve`)"
            ) from e
        if res.status_code == 400 and "think" in res.text and self._send_think:
            # Models without a reasoning mode reject the switch; ask without it.
            self._send_think = False
            body.pop("think")
            res = await self._http.post(f"{self.url}/api/chat", json=body)
        if res.status_code == 404:
            raise RuntimeError(
                f"Ollama has no model {self.model!r} (run `ollama pull {self.model}`)"
            )
        res.raise_for_status()

        message = res.json()["message"]
        self.messages.append({
            key: message[key]
            for key in ("role", "content", "tool_calls")
            if key in message
        })

        reply = Reply(text=(message.get("content") or "").strip())
        for n, call in enumerate(message.get("tool_calls") or []):
            fn = call["function"]
            arguments = fn.get("arguments") or {}
            if isinstance(arguments, str):
                arguments = json.loads(arguments or "{}")
            reply.calls.append(ToolCall(
                id=call.get("id") or f"call_{len(self.messages)}_{n}",
                name=fn["name"],
                arguments=arguments,
            ))
        return reply

    def add_results(self, results: list[tuple[ToolCall, str]]) -> None:
        for call, output in results:
            self.messages.append(
                {"role": "tool", "tool_name": call.name, "content": output}
            )


# --------------------------------------------------------------------------- #

def from_env() -> Backend:
    choice = os.environ.get("LLM_BACKEND", "ollama").strip().lower()
    if choice == "anthropic":
        return AnthropicBackend(os.environ.get("ANTHROPIC_MODEL", "claude-haiku-5-5"))
    if choice == "ollama":
        return OllamaBackend(
            os.environ.get("OLLAMA_MODEL", "qwen3:8b"),
            os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434"),
        )
    raise RuntimeError(f"LLM_BACKEND must be 'anthropic' or 'ollama', not {choice!r}")

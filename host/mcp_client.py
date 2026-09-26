from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

PROTOCOL_VERSION = "2025-11-25"


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_schema: dict[str, Any]


class MCPError(RuntimeError):
    pass


class MCPClient:
    """A minimal MCP client speaking Streamable HTTP.

    The server runs stateless with JSON responses, so every call is one POST
    and no session is carried between them.
    """

    def __init__(self, url: str, timeout: float = 30.0) -> None:
        self.url = url
        self._http = httpx.AsyncClient(timeout=timeout)
        self._next_id = 0
        self.server_info: dict[str, Any] = {}

    async def _request(self, method: str, params: dict | None = None) -> Any:
        self._next_id += 1
        body = {"jsonrpc": "2.0", "id": self._next_id, "method": method}
        if params is not None:
            body["params"] = params

        response = await self._http.post(
            self.url,
            json=body,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "MCP-Protocol-Version": PROTOCOL_VERSION,
            },
        )
        response.raise_for_status()
        payload = response.json()

        if "error" in payload:
            err = payload["error"]
            raise MCPError(f"{err.get('code')}: {err.get('message')}")
        return payload["result"]

    async def initialize(self) -> dict[str, Any]:
        result = await self._request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "robot-operator-host", "version": "0.1.0"},
            },
        )
        self.server_info = result.get("serverInfo", {})
        negotiated = result.get("protocolVersion")
        if negotiated != PROTOCOL_VERSION:
            raise MCPError(
                f"Server speaks {negotiated}, this host needs {PROTOCOL_VERSION}."
            )
        return result

    async def list_tools(self) -> list[Tool]:
        result = await self._request("tools/list")
        return [
            Tool(
                name=t["name"],
                description=t.get("description", ""),
                input_schema=t.get("inputSchema", {"type": "object", "properties": {}}),
            )
            for t in result.get("tools", [])
        ]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        """Call one tool and return its text. Tool errors come back as text."""
        result = await self._request(
            "tools/call", {"name": name, "arguments": arguments}
        )
        parts = [
            block.get("text", "")
            for block in result.get("content", [])
            if block.get("type") == "text"
        ]
        text = "\n".join(p for p in parts if p)
        if result.get("isError"):
            return f"The robot reported a problem: {text}"
        return text

    async def aclose(self) -> None:
        await self._http.aclose()
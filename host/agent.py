from __future__ import annotations

import json
from typing import Any

import boto3

from .mcp_client import MCPClient, Tool

MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
AWS_PROFILE = "alexa-ai-user"
AWS_REGION = "us-west-2"
MAX_ROUNDS = 6

SYSTEM_PROMPT = """You are the voice of a home robot. You are speaking out \
loud, so keep every reply to one or two short sentences with no lists, no \
markdown and no numbers read out digit by digit.

You never move the robot yourself. To go somewhere you call propose_move, \
read the answer out loud including any warning, and wait for the person to \
agree. Only then do you call confirm_move with the token from that answer. \
Never invent a token and never call confirm_move without proposing first.

If the robot refuses, say why in plain words. Do not argue with the refusal \
and do not look for another way around it: the safety gate decides, not you.

If the person says stop, call stop immediately without asking anything."""


def _to_bedrock_tools(tools: list[Tool]) -> dict[str, Any]:
    return {
        "tools": [
            {
                "toolSpec": {
                    "name": t.name,
                    "description": t.description,
                    "inputSchema": {"json": t.input_schema},
                }
            }
            for t in tools
        ]
    }


class Agent:
    """Turns spoken text into tool calls, and tool results into speech."""

    def __init__(self, mcp: MCPClient) -> None:
        self.mcp = mcp
        self.tools: list[Tool] = []
        self.messages: list[dict[str, Any]] = []
        session = boto3.Session(profile_name=AWS_PROFILE, region_name=AWS_REGION)
        self._bedrock = session.client("bedrock-runtime")

    async def start(self) -> None:
        await self.mcp.initialize()
        self.tools = await self.mcp.list_tools()

    async def say(self, text: str):
        """Handle one utterance. Yields events for the panel to render."""
        self.messages.append({"role": "user", "content": [{"text": text}]})

        for _ in range(MAX_ROUNDS):
            response = self._bedrock.converse(
                modelId=MODEL_ID,
                system=[{"text": SYSTEM_PROMPT}],
                messages=self.messages,
                toolConfig=_to_bedrock_tools(self.tools),
                inferenceConfig={"maxTokens": 400, "temperature": 0.0},
            )

            message = response["output"]["message"]
            self.messages.append(message)

            spoken = "".join(
                block["text"] for block in message["content"] if "text" in block
            ).strip()
            if spoken:
                yield {"type": "say", "text": spoken}

            uses = [b["toolUse"] for b in message["content"] if "toolUse" in b]
            if not uses:
                return

            results = []
            for use in uses:
                yield {
                    "type": "tool_call",
                    "name": use["name"],
                    "arguments": use["input"],
                }
                output = await self.mcp.call_tool(use["name"], use["input"])
                yield {"type": "tool_result", "name": use["name"], "text": output}
                results.append(
                    {
                        "toolResult": {
                            "toolUseId": use["toolUseId"],
                            "content": [{"text": output}],
                        }
                    }
                )

            self.messages.append({"role": "user", "content": results})

        yield {"type": "say", "text": "I got stuck working that out. Try again."}
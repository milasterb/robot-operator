from __future__ import annotations

from .llm import Backend, from_env
from .mcp_client import MCPClient, Tool

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


class Agent:
    """Turns spoken text into tool calls, and tool results into speech.

    The model behind it is picked by LLM_BACKEND (see host/llm.py). Nothing
    here depends on which one it is.
    """

    def __init__(self, mcp: MCPClient, llm: Backend | None = None) -> None:
        self.mcp = mcp
        self.llm = llm or from_env()
        self.tools: list[Tool] = []

    async def start(self) -> None:
        await self.mcp.initialize()
        self.tools = await self.mcp.list_tools()

    async def say(self, text: str):
        """Handle one utterance. Yields events for the panel to render."""
        self.llm.add_user(text)

        for _ in range(MAX_ROUNDS):
            reply = await self.llm.step(SYSTEM_PROMPT, self.tools)

            if reply.text:
                yield {"type": "say", "text": reply.text}
            if not reply.calls:
                return

            results = []
            for call in reply.calls:
                yield {"type": "tool_call", "name": call.name, "arguments": call.arguments}
                output = await self.mcp.call_tool(call.name, call.arguments)
                yield {"type": "tool_result", "name": call.name, "text": output}
                results.append((call, output))
            self.llm.add_results(results)

        yield {"type": "say", "text": "I got stuck working that out. Try again."}

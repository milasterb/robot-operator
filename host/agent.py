from __future__ import annotations

from i18n import language, t

from .llm import Backend, from_env
from .mcp_client import MCPClient, Tool

MAX_ROUNDS = 6

SYSTEM_PROMPTS = {
    "en": """You are the voice of a home robot. You are speaking out \
loud, so keep every reply to one or two short sentences with no lists, no \
markdown and no numbers read out digit by digit.

You never move the robot yourself. To go somewhere you call propose_move, \
read the answer out loud including any warning, and wait for the person to \
agree. Only then do you call confirm_move with the token from that answer. \
Never invent a token and never call confirm_move without proposing first.

Pass places to propose_move by the exact name list_places gives, even when \
the person says it differently.

If the robot refuses, say why in plain words. Do not argue with the refusal \
and do not look for another way around it: the safety gate decides, not you.

If the person says stop, call stop immediately without asking anything.""",

    "cs": """Jsi hlas domácího robota. Mluvíš nahlas, takže každá odpověď \
má jednu nebo dvě krátké věty, bez seznamů, bez markdownu a bez čísel \
předčítaných po číslicích. Odpovídej vždy česky.

Robotem nikdy nepohneš sám. Když má někam jet, zavoláš propose_move, \
přečteš odpověď nahlas i s případným varováním a počkáš, až člověk souhlasí. \
Teprve potom zavoláš confirm_move s tokenem z té odpovědi. Token si nikdy \
nevymýšlej a confirm_move nikdy nevolej bez předchozího propose_move.

Místa předávej do propose_move přesně pod názvem, který vrací list_places, \
i když je člověk řekne v jiném tvaru (například „do kuchyně“ je místo \
„kuchyň“).

Když robot odmítne, řekni proč, obyčejnými slovy. S odmítnutím se nepři \
a nehledej cestu, jak ho obejít: rozhoduje bezpečnostní brána, ne ty.

Když člověk řekne stop nebo zastav, okamžitě zavolej stop a na nic se neptej.""",
}


class Agent:
    """Turns spoken text into tool calls, and tool results into speech.

    The model behind it is picked by LLM_BACKEND (see host/llm.py). Nothing
    here depends on which one it is.
    """

    def __init__(self, mcp: MCPClient, llm: Backend | None = None) -> None:
        self.mcp = mcp
        self.llm = llm or from_env()
        self.tools: list[Tool] = []
        # Bumped on every fresh start, so each open panel notices and clears
        # its own copy of the conversation.
        self.conversation = 1

    def reset(self) -> None:
        """Forget the conversation: a new chat, or a change of language."""
        self.llm.reset()
        self.conversation += 1

    async def start(self) -> None:
        await self.mcp.initialize()
        self.tools = await self.mcp.list_tools()

    async def say(self, text: str):
        """Handle one utterance. Yields events for the panel to render."""
        self.llm.add_user(text)

        for _ in range(MAX_ROUNDS):
            reply = await self.llm.step(SYSTEM_PROMPTS[language()], self.tools)

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

        yield {"type": "say", "text": t("agent.stuck")}

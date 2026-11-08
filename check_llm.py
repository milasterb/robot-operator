"""Ask the configured model one question, with no robot and no tools.

    python check_llm.py                      # whatever .env says
    LLM_BACKEND=ollama python check_llm.py   # force one backend
"""
import asyncio

from host.llm import from_env


async def main() -> None:
    llm = from_env()
    print(f"Asking {llm.label} ...")
    llm.add_user("Reply with one word: ready")
    reply = await llm.step("You are a terse assistant.", [])
    print(reply.text or "(empty reply)")


asyncio.run(main())

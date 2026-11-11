from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from i18n import SUPPORTED, language, set_language
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from .agent import Agent
from .mcp_client import MCPClient

MCP_URL = "http://127.0.0.1:8000/mcp"
# The operator's own routes on the same server: the map and its fences.
# Not part of MCP, so the agent has no way to reach them.
OPERATOR_URL = "http://127.0.0.1:8000/operator"
STATIC = Path(__file__).parent / "static"

mcp = MCPClient(MCP_URL)
agent = Agent(mcp)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await agent.start()
    await sync_language()
    print(f"Connected to {agent.mcp.server_info.get('name')} "
          f"with {len(agent.tools)} tools")
    print(f"Language model: {agent.llm.label}, speaking {language()}")
    yield
    await mcp.aclose()


app = FastAPI(lifespan=lifespan)


async def sync_language() -> str:
    """Take the language from the MCP server, which owns the robot.

    The robot's sentences are written there, so the system prompt must
    match it. Asked before every utterance: if the robot server restarted
    with another ROBOT_LANG, the conversation starts over in that one.
    """
    try:
        async with httpx.AsyncClient(timeout=3.0) as http:
            code = (await http.get(f"{OPERATOR_URL}/language")).json()["lang"]
    except (httpx.HTTPError, KeyError, ValueError):
        return language()
    if code in SUPPORTED and code != language():
        set_language(code)
        agent.reset()
    return language()


@app.get("/api/scene")
async def scene():
    """Map data for the panel: robot, places, zones, current path."""
    raw = await mcp.call_tool("get_scene", {})
    data = json.loads(raw)
    data["conversation"] = agent.conversation
    return data


@app.post("/api/language")
async def switch_language(request: Request) -> Response:
    """The panel's CZ / EN switch. The robot server decides; this follows."""
    async with httpx.AsyncClient(timeout=5.0) as http:
        res = await http.post(f"{OPERATOR_URL}/language", content=await request.body(),
                              headers={"content-type": "application/json"})
    if res.status_code == 200:
        code = res.json()["lang"]
        if code != language():
            set_language(code)
        agent.reset()   # half a conversation in each language confuses small models
    return Response(res.content, status_code=res.status_code,
                    media_type="application/json")


@app.post("/api/reset")
async def new_conversation() -> dict:
    """The panel's New chat button."""
    agent.reset()
    return {"conversation": agent.conversation}


@app.api_route("/api/operator/{path:path}", methods=["GET", "PUT", "POST"])
async def operator(path: str, request: Request) -> Response:
    """Pass the map editor's requests through to the MCP server's operator
    routes: the site, its map image, saving, new maps, switching."""
    async with httpx.AsyncClient(timeout=30.0) as http:
        res = await http.request(
            request.method, f"{OPERATOR_URL}/{path}",
            content=await request.body(),
            headers={"content-type": request.headers.get("content-type", "application/json")},
        )
    return Response(res.content, status_code=res.status_code,
                    media_type=res.headers.get("content-type"),
                    headers={"Cache-Control": "no-store"})


@app.get("/api/llm")
async def llm() -> dict:
    """Which model is answering, for the panel's status strip."""
    return {"label": agent.llm.label, "lang": await sync_language(),
            "languages": list(SUPPORTED), "conversation": agent.conversation}


@app.websocket("/ws")
async def ws(socket: WebSocket) -> None:
    await socket.accept()
    try:
        while True:
            text = (await socket.receive_text()).strip()
            if not text:
                continue
            await socket.send_json({"type": "heard", "text": text})
            await sync_language()
            try:
                async for event in agent.say(text):
                    await socket.send_json(event)
            except Exception as e:  # keep the panel alive on a bad turn
                await socket.send_json({"type": "error", "text": str(e)})
            await socket.send_json({"type": "done"})
    except WebSocketDisconnect:
        pass


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
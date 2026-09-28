from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .agent import Agent
from .mcp_client import MCPClient

MCP_URL = "http://127.0.0.1:8000/mcp"
STATIC = Path(__file__).parent / "static"

mcp = MCPClient(MCP_URL)
agent = Agent(mcp)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await agent.start()
    print(f"Connected to {agent.mcp.server_info.get('name')} "
          f"with {len(agent.tools)} tools")
    yield
    await mcp.aclose()


app = FastAPI(lifespan=lifespan)


@app.get("/api/scene")
async def scene():
    """Map data for the panel: robot, places, zones, current path."""
    raw = await mcp.call_tool("get_scene", {})
    return json.loads(raw)


@app.websocket("/ws")
async def ws(socket: WebSocket) -> None:
    await socket.accept()
    try:
        while True:
            text = (await socket.receive_text()).strip()
            if not text:
                continue
            await socket.send_json({"type": "heard", "text": text})
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
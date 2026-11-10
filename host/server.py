from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
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
    print(f"Connected to {agent.mcp.server_info.get('name')} "
          f"with {len(agent.tools)} tools")
    print(f"Language model: {agent.llm.label}")
    yield
    await mcp.aclose()


app = FastAPI(lifespan=lifespan)


@app.get("/api/scene")
async def scene():
    """Map data for the panel: robot, places, zones, current path."""
    raw = await mcp.call_tool("get_scene", {})
    return json.loads(raw)


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
    return {"label": agent.llm.label}


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
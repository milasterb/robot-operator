from mcp.server.mcpserver import MCPServer

mcp = MCPServer("robot-operator", version="0.1.0")

@mcp.tool()
def ping() -> str:
    """Health check. Confirms the robot operator server is reachable."""
    return "Hello from the robot operator server."

if __name__ == "__main__":
    mcp.run(
        transport="streamable-http",
        host="127.0.0.1",
        port=8000,
        stateless_http=True,
        json_response=True,
    )
from __future__ import annotations

import json
import time

from mcp.server.mcpserver import MCPServer

from gate.gate import Gate
from gate.rules import GateConfig, Zone
from gate.types import Level, Point
from robot.adapter import FakeRobot
from robot.lights import Level as LightLevel
from robot.tokens import TokenError, TokenStore

# The corridor: the robot loses its localisation in there, because the LiDAR
# sees two parallel walls and cannot tell how far along them it has driven.
# This is a real limit of this machine in this flat, measured rather than
# imagined, which is what makes it a good thing for the gate to enforce.
CORRIDOR = Zone(
    "the corridor",
    [Point(3.5, 1.1), Point(5.0, 1.1), Point(5.0, 5.0), Point(3.5, 5.0)],
)

robot = FakeRobot()
gate = Gate(GateConfig(robot_width=0.20, zones=[CORRIDOR]))
tokens = TokenStore()

mcp = MCPServer("robot-operator", version="0.3.0")


def _resolve(place: str) -> tuple[str, Point]:
    """Match a spoken place name against the map. Raises ValueError."""
    places = robot.places()
    needle = place.strip().lower()
    for name, point in places.items():
        if needle in name.lower() or name.lower() in needle:
            return name, point
    known = ", ".join(places)
    raise ValueError(f"I do not know a place called {place}. I know: {known}.")


@mcp.tool()
def get_status() -> str:
    """Report the robot's battery, position and what it is doing right now."""
    s = robot.get_state()
    if not s.localized:
        return "The robot is powered up but does not know where it is."
    doing = "driving" if s.moving else "stopped"
    return (
        f"Battery {s.voltage:.1f} volts. The robot is {doing} at "
        f"{s.pose.x:.1f}, {s.pose.y:.1f} metres. Nearest obstacle "
        f"{s.nearest_obstacle:.1f} metres."
    )


@mcp.tool()
def list_places() -> str:
    """List the places the robot can be sent to."""
    return "I can drive to: " + ", ".join(robot.places()) + "."


@mcp.tool()
def propose_move(place: str) -> str:
    """Plan a trip and judge whether it is safe. Does NOT move the robot.

    Always call this before confirm_move. Read the whole answer out loud,
    including any warning, and wait for the person to agree.
    """
    try:
        name, goal = _resolve(place)
    except ValueError as e:
        return str(e)

    state = robot.get_state()
    proposal = robot.plan(goal, name)
    verdict = gate.evaluate(state, proposal)

    if verdict.level is Level.STOP:
        gate.commit(verdict, proposal)
        robot.set_light(LightLevel.STOP)
        return "I cannot make that trip. " + " ".join(verdict.reasons)

    pending = tokens.issue(proposal, verdict, state.voltage)
    # Amber, breathing: proposed, and waiting on a human. Not green — nothing
    # has been agreed to yet.
    robot.set_light(LightLevel.WAITING)

    warning = (" " + " ".join(verdict.reasons)) if verdict.level is Level.LIMIT else ""
    return (
        f"I can drive to {name}.{warning} Confirm with token {pending.token} "
        f"within 30 seconds."
    )


@mcp.tool()
def confirm_move(token: str) -> str:
    """Carry out a move that propose_move offered, using its token.

    The safety check runs again here, so a move can still be refused.
    """
    try:
        pending = tokens.redeem(token)
    except TokenError as e:
        return str(e)

    state = robot.get_state()
    verdict = gate.evaluate(state, pending.proposal)
    gate.commit(verdict, pending.proposal)

    if verdict.level is Level.STOP:
        robot.set_light(LightLevel.STOP)
        return (
            "Something changed while we were talking, so I am staying put. "
            + " ".join(verdict.reasons)
        )

    robot.drive(pending.proposal, verdict.speed_factor)
    robot.set_light(
        LightLevel.LIMIT if verdict.speed_factor < 1.0 else LightLevel.ALLOW
    )

    name = pending.proposal.goal_name or "the destination"
    if verdict.speed_factor < 1.0:
        return f"Driving to {name} at half speed."
    return f"Driving to {name}."


@mcp.tool()
def stop() -> str:
    """Stop the robot immediately. Never needs confirmation."""
    robot.halt()
    tokens.discard_all()
    robot.set_light(LightLevel.STOP)
    return "Stopped."


@mcp.tool()
def explain_stop() -> str:
    """Say why the robot last refused to move or stopped."""
    entry = gate.last_stop()
    if entry is None:
        return "I have not stopped for anything since we started."
    ago = time.monotonic() - entry.timestamp
    where = f" on the way to {entry.goal_name}" if entry.goal_name else ""
    return f"About {ago:.0f} seconds ago{where}: " + " ".join(entry.reasons)


@mcp.tool()
def recent_events(count: int = 5) -> str:
    """List the robot's most recent safety decisions, newest first."""
    entries = list(gate.log)[-count:][::-1]
    if not entries:
        return "Nothing has happened yet."
    lines = [f"{e.level.name}: " + " ".join(e.reasons) for e in entries]
    return " | ".join(lines)


@mcp.tool()
def get_scene() -> str:
    """Return the map as JSON: robot pose, named places, forbidden zones.

    For screens, not for speech. Do not read this out loud.
    """
    s = robot.get_state()
    scene = {
        "robot": {
            "x": s.pose.x, "y": s.pose.y, "yaw": s.pose.yaw,
            "moving": s.moving, "voltage": round(s.voltage, 1),
            "localized": s.localized,
        },
        "places": [
            {"name": n, "x": p.x, "y": p.y} for n, p in robot.places().items()
        ],
        "zones": [
            {
                "name": z.name,
                "polygon": [{"x": p.x, "y": p.y} for p in z.polygon],
            }
            for z in gate.config.zones
        ],
        "path": [
            {"x": p.x, "y": p.y} for p in getattr(robot, "current_path", [])
        ],
    }
    return json.dumps(scene)


if __name__ == "__main__":
    # Dim blue on startup: powered, listening, nothing being decided.
    robot.set_light(LightLevel.IDLE)
    try:
        mcp.run(
            transport="streamable-http",
            host="127.0.0.1",
            port=8000,
            stateless_http=True,
            json_response=True,
        )
    finally:
        robot.close()
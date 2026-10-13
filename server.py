from __future__ import annotations

import json
import os
import time

from mcp.server.mcpserver import MCPServer

from gate.gate import Gate
from gate.rules import GateConfig, Zone
from gate.types import Level, Point
from robot.adapter import FakeRobot
from robot.lights import Level as LightLevel
from robot.tokens import TokenError, TokenStore

# The corner of the living room with the treadmill and the desk somebody
# works at. Two independent reasons to keep the robot out, and only one of
# them is visible to the robot: the treadmill leaves no clear way through,
# and a person sits there. The map cannot see the person. That is exactly
# the kind of thing an operator knows and a model cannot, which is why it
# belongs in the gate and not in a prompt.
#
# Coordinates are in the frame of maps/obyvak_final.yaml. The box is wide
# enough that a goal inside it is reachable: Nav2 will happily plan a route
# there, and the gate refuses it anyway. A zone the planner cannot reach
# would produce the wrong refusal for the right reason.
TREADMILL = Zone(
    "the treadmill area",
    [
        Point(1.00, -1.50),
        Point(2.30, -1.50),
        Point(2.30, -0.35),
        Point(1.00, -0.35),
    ],
)

# Set ROBOT_HOST to the robot's address to drive the real thing:
#     ROBOT_HOST=192.168.1.42 python server.py
# Without it the server runs against the in-memory robot, which is how the
# tools and the panel get developed without hardware.
ROBOT_HOST = os.environ.get("ROBOT_HOST")

if ROBOT_HOST:
    from robot.ros_adapter import RosRobot

    robot = RosRobot(ROBOT_HOST)
    robot.connect()
else:
    robot = FakeRobot()

# robot_width is the one number here that is not measured from the map.
# Measure the X3 across its widest point, wheels included, before trusting
# the clearance rules: they are both derived from it.
gate = Gate(GateConfig(robot_width=0.20, zones=[TREADMILL]))
tokens = TokenStore()

mcp = MCPServer("robot-operator", version="0.4.0")


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
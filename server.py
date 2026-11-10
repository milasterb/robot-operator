from __future__ import annotations

import base64
import binascii
import dataclasses
import json
import os
import time

from mcp.server.mcpserver import MCPServer
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from gate.gate import Gate
from gate.rules import GateConfig
from gate.types import Level, Point
from layout import (
    Site, SiteError, compare_with_robot, create_site, list_sites, load_site, log_change,
    parse_site, save_site, site_folder,
)
from robot.adapter import FakeRobot
from robot.lights import Level as LightLevel
from robot.tokens import TokenError, TokenStore

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
gate = Gate(GateConfig(robot_width=0.20))
tokens = TokenStore()


class SiteKeeper:
    """The room the robot is in: its map, named places and forbidden zones.

    Everything comes from sites/<SITE>/ — see layout/site.py. Nothing about
    a particular room is written in this file any more.

    The zones go straight into the gate. Only the operator changes them, by
    editing site.json or drawing in the panel; no MCP tool can, because the
    model must never be able to move the fences it is kept inside.
    """

    # How long a copy of the robot's own map is trusted before it is fetched
    # again. Nav2 may have been restarted on another map in the meantime.
    ROBOT_MAP_TTL = 60.0

    def __init__(self, folder) -> None:
        self.folder = folder
        self.site: Site = load_site(folder)
        self._mtime = self._stamp()
        self.revision = 1
        self._robot_map: dict | None = None
        self._robot_map_at = -1e9
        self.map_check = {"status": "unknown", "detail": ""}
        self._apply(self.site)

    def current(self) -> Site:
        """The site as it is on disk now. A hand edit is picked up here."""
        stamp = self._stamp()
        if stamp != self._mtime:
            self._mtime = stamp
            try:
                self.replace(load_site(self.folder))
            except SiteError as e:
                # Keep the last good fences rather than none at all.
                print(f"site.json changed but was not loaded: {e}")
        return self.site

    def switch(self, folder) -> None:
        """Make another site the current one."""
        site = load_site(folder)
        self.folder = folder
        self._mtime = self._stamp()
        self.replace(site)

    def replace(self, site: Site) -> None:
        self.site = site
        self.revision += 1
        self._apply(site)
        # A trip proposed against the old map may now be somewhere else or
        # behind a new fence. Make the person ask again.
        tokens.discard_all()

    def _apply(self, site: Site) -> None:
        gate.config = dataclasses.replace(gate.config, zones=list(site.zones))
        if isinstance(robot, FakeRobot):
            robot.places = dict(site.places)
        self.check_map(refetch=True)

    def check_map(self, refetch: bool = False) -> dict:
        """Is the robot localising against the map these zones were drawn on?

        A mismatch is handed to the gate, which then refuses every trip: the
        zones would be fencing off the wrong floor. If the robot's map cannot
        be read at all (Nav2 not up yet, map_server named differently), that
        is reported but does not block, so a missing service never strands
        the demo; the panel says the zones are unverified.
        """
        if isinstance(robot, FakeRobot):
            self.map_check = {"status": "simulated",
                              "detail": "Simulated robot: no robot map to compare."}
            gate.config = dataclasses.replace(gate.config, map_mismatch=None)
            return self.map_check

        stale = time.monotonic() - self._robot_map_at > self.ROBOT_MAP_TTL
        if refetch or stale or self._robot_map is None:
            fetched = robot.fetch_map()
            self._robot_map_at = time.monotonic()
            if fetched is not None or self._robot_map is None or refetch:
                self._robot_map = fetched

        if self._robot_map is None:
            self.map_check = {"status": "unverified",
                              "detail": "Could not read the map from the robot's "
                                        "map_server, so the zones are not verified."}
            mismatch = None
        else:
            mismatch = compare_with_robot(self.site.grid, self._robot_map)
            self.map_check = (
                {"status": "mismatch", "detail": mismatch} if mismatch else
                {"status": "match", "detail": "The robot is using this map."}
            )
        if mismatch != gate.config.map_mismatch:
            print(f"Map check: {self.map_check['status']} {self.map_check['detail']}")
        gate.config = dataclasses.replace(gate.config, map_mismatch=mismatch)
        return self.map_check

    def _stamp(self) -> float:
        try:
            return (self.folder / "site.json").stat().st_mtime
        except FileNotFoundError:
            return 0.0


# Which room: SITE in the shell or in .env. Without it the in-memory robot
# gets the demo flat and the real one gets the living room it was mapped in.
sites = SiteKeeper(site_folder(os.environ.get("SITE") or ("obyvak" if ROBOT_HOST else "demo")))

mcp = MCPServer("robot-operator", version="0.4.0")


def _resolve(place: str) -> tuple[str, Point]:
    """Match a spoken place name against the map. Raises ValueError."""
    places = sites.current().places
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
    return "I can drive to: " + ", ".join(sites.current().places) + "."


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

    sites.check_map()
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

    sites.check_map()
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
    site = sites.current()
    s = robot.get_state()
    scene = {
        "site": {"name": site.name, "revision": sites.revision,
                 "map_check": sites.map_check},
        "robot": {
            "x": s.pose.x, "y": s.pose.y, "yaw": s.pose.yaw,
            "moving": s.moving, "voltage": round(s.voltage, 1),
            "localized": s.localized,
        },
        "places": [
            {"name": n, "x": p.x, "y": p.y} for n, p in site.places.items()
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


# ---- operator routes ------------------------------------------------------
# Plain HTTP next to the MCP endpoint, for the panel. They are not MCP tools,
# so the model cannot see or call them.

@mcp.custom_route("/operator/site", methods=["GET"])
async def operator_site(request: Request) -> JSONResponse:
    site = sites.current()
    body = site.to_json()
    body["revision"] = sites.revision
    body["map_info"] = site.describe_map()
    return JSONResponse(body)


@mcp.custom_route("/operator/site", methods=["PUT"])
async def operator_save(request: Request) -> JSONResponse:
    """Save places and zones drawn in the panel.

    The request carries the revision it was drawn on. If the site changed
    in the meantime — someone edited the file, or another tab saved first —
    the save is refused rather than silently undoing that change.
    """
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return _error("The request was not JSON.")
    if not isinstance(body, dict):
        return _error("The request must be a JSON object.")
    current = sites.current()
    if body.get("revision") != sites.revision:
        return _error("The map changed while you were editing. Reload it and "
                      "draw your change again.", status=409)
    data = current.to_json()
    data["places"] = body.get("places", [])
    data["zones"] = body.get("zones", [])
    try:
        site = parse_site(data, current.folder, current.grid)
    except SiteError as e:
        return _error(str(e))
    before = current.to_json()
    save_site(site)
    log_change(current.folder, before, site.to_json())
    sites._mtime = sites._stamp()      # our own write, not a hand edit
    sites.replace(site)
    print(f"Site {site.name!r} saved: {len(site.places)} places, "
          f"{len(site.zones)} zones")
    return await operator_site(request)


@mcp.custom_route("/operator/check-map", methods=["POST"])
async def operator_check_map(request: Request) -> JSONResponse:
    """Fetch the robot's map again and compare, e.g. after restarting Nav2."""
    return JSONResponse(sites.check_map(refetch=True))


@mcp.custom_route("/operator/sites", methods=["GET"])
async def operator_sites(request: Request) -> JSONResponse:
    return JSONResponse({
        "sites": list_sites(),
        "active": sites.folder.name,
        # The robot's own map lives on the robot, and the fences only mean
        # something in that map's frame. With a real robot connected the
        # site is chosen at startup (SITE=...), not switched from a browser.
        "switchable": not ROBOT_HOST,
    })


@mcp.custom_route("/operator/sites", methods=["POST"])
async def operator_new_site(request: Request) -> JSONResponse:
    """Create a site from a map saved by map_saver, and switch to it."""
    if ROBOT_HOST:
        return _error("With a real robot connected, start the server with "
                      "SITE=... instead of switching maps here.", status=403)
    try:
        body = await request.json()
        pgm = base64.b64decode(body["pgm_base64"], validate=True)
        target = create_site(str(body["name"]), str(body["yaml"]), pgm)
    except (json.JSONDecodeError, KeyError, TypeError, binascii.Error):
        return _error("Send the map's name, its .yaml text and its .pgm file.")
    except SiteError as e:
        return _error(str(e))
    sites.switch(target)
    print(f"New site {sites.site.name!r} created in {target}")
    return await operator_site(request)


@mcp.custom_route("/operator/active", methods=["POST"])
async def operator_switch(request: Request) -> JSONResponse:
    if ROBOT_HOST:
        return _error("With a real robot connected, start the server with "
                      "SITE=... instead of switching maps here.", status=403)
    try:
        folder = site_folder(str((await request.json())["folder"]))
        sites.switch(folder)
    except (json.JSONDecodeError, KeyError, TypeError):
        return _error("Say which site to switch to.")
    except SiteError as e:
        return _error(str(e))
    return await operator_site(request)


def _error(message: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


@mcp.custom_route("/operator/map.png", methods=["GET"])
async def operator_map(request: Request) -> Response:
    site = sites.current()
    if site.grid is None:
        return Response(status_code=404)
    return Response(site.map_png(), media_type="image/png",
                    headers={"Cache-Control": "no-store"})


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
from __future__ import annotations

import math
import time
from typing import Protocol

from gate.types import MoveProposal, Point, Pose, RobotState
from robot.lights import Level as LightLevel

PATH_STEP = 0.20   # metres between sampled points along a planned route
AT_PLACE = 0.40    # how close the robot has to be to count as being somewhere


class RobotAdapter(Protocol):
    """Everything the tools need from a robot. The gate never sees this."""

    def get_state(self) -> RobotState: ...

    def plan(self, goal: Point, goal_name: str | None) -> MoveProposal:
        """Ask the planner for a route. Does not move the robot."""
        ...

    def drive(self, proposal: MoveProposal, speed_factor: float) -> None:
        """Start driving. Only ever called after a confirmed verdict."""
        ...

    def halt(self) -> None:
        """Stop now. Never gated, never confirmed."""
        ...

    def set_light(self, level: LightLevel) -> None:
        """Show the gate's verdict to the room."""
        ...

    def close(self) -> None:
        """Release whatever the adapter is holding on to."""
        ...


class FakeRobot:
    """A robot that lives in memory. Same interface as the real one.

    Used for tests and for developing the tools without hardware.

    Its places come from the site (sites/demo/site.json by default) and are
    set by the server. The doorways below are what the fake planner reports
    as the narrowest point on the way into each demo place: laid out so that
    each one produces a different verdict, because a gate that has only ever
    been seen saying yes has not been seen working. Places on any other site
    stand in open floor.
    """

    def __init__(self) -> None:
        self.voltage = 12.0
        self.pose = Pose(0.0, 0.0, 0.0)
        self.nearest_obstacle = 2.0
        self.moving = False
        self.localized = True
        self.current_goal: Point | None = None
        self.current_path: list[Point] = []
        self.speed_factor = 1.0
        self.halted_at: float | None = None
        self.light: LightLevel | None = None

        self.places: dict[str, Point] = {}  # set from the site by the server
        # The narrowest point on the way into a place, in metres: a door
        # frame, or a gap left by furniture. Width belongs to the place and
        # not to a point on the map, because a route into the bathroom goes
        # through the bathroom door whichever way it approaches. Places not
        # listed here stand on open floor.
        self._doorways = {
            "the living room": 0.80,   # wide open
            "the kitchen": 0.55,       # comfortable
            "the bathroom": 0.26,      # tight: worth slowing down for
            "the front door": 0.18,    # hallway, narrowed by the shoe rack
        }

    def get_state(self) -> RobotState:
        return RobotState(
            voltage=self.voltage,
            voltage_raw=self.voltage,
            pose=self.pose,
            nearest_obstacle=self.nearest_obstacle,
            moving=self.moving,
            localized=self.localized,
            timestamp=time.monotonic(),
        )

    def plan(self, goal: Point, goal_name: str | None) -> MoveProposal:
        start = Point(self.pose.x, self.pose.y)
        return MoveProposal(
            goal=goal,
            goal_name=goal_name,
            path=_straight_line(start, goal),
            min_clearance=self._clearance(start, goal_name),
        )

    def drive(self, proposal: MoveProposal, speed_factor: float) -> None:
        self.moving = True
        self.current_goal = proposal.goal
        self.current_path = list(proposal.path)
        self.speed_factor = speed_factor
        self.pose = Pose(proposal.goal.x, proposal.goal.y, self.pose.yaw)
        self.moving = False

    def halt(self) -> None:
        self.moving = False
        self.current_goal = None
        self.current_path = []
        self.speed_factor = 0.0
        self.halted_at = time.monotonic()

    def set_light(self, level: LightLevel) -> None:
        """No light bar in the fake robot; the panel shows the verdict."""
        self.light = level

    def close(self) -> None:
        pass

    def _clearance(self, start: Point, goal_name: str | None) -> float:
        """Narrowest doorway the trip has to pass through.

        Two of them: the one out of wherever the robot is standing, and the
        one into where it is going.
        """
        widths = [self._doorways.get(goal_name or "", float("inf"))]
        here = self._place_at(start)
        if here is not None:
            widths.append(self._doorways.get(here, float("inf")))
        return min(widths)

    def _place_at(self, point: Point) -> str | None:
        """Which named place the robot is standing in, if any."""
        for name, p in self.places.items():
            if _distance(point, p) <= AT_PLACE:
                return name
        return None


def _straight_line(start: Point, goal: Point) -> list[Point]:
    """Sample a straight route at PATH_STEP intervals, ends included.

    Real planners return a dense path. This exists so the panel has
    something to draw, and so the shape of a MoveProposal matches what the
    ROS adapter produces.
    """
    span = _distance(start, goal)
    if span < 1e-6:
        return [start]
    steps = max(1, math.ceil(span / PATH_STEP))
    return [
        Point(
            start.x + (goal.x - start.x) * i / steps,
            start.y + (goal.y - start.y) * i / steps,
        )
        for i in range(steps + 1)
    ]


def _distance(a: Point, b: Point) -> float:
    return math.hypot(a.x - b.x, a.y - b.y)
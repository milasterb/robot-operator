from __future__ import annotations

import math
import time
from typing import Protocol

from gate.types import MoveProposal, Point, Pose, RobotState


class RobotAdapter(Protocol):
    """Everything the tools need from a robot. The gate never sees this."""

    def get_state(self) -> RobotState: ...

    def places(self) -> dict[str, Point]:
        """Named destinations, e.g. {"the charger": Point(0.0, 0.0)}."""
        ...

    def plan(self, goal: Point, goal_name: str | None) -> MoveProposal:
        """Ask the planner for a route. Does not move the robot."""
        ...

    def drive(self, proposal: MoveProposal, speed_factor: float) -> None:
        """Start driving. Only ever called after a confirmed verdict."""
        ...

    def halt(self) -> None:
        """Stop now. Never gated, never confirmed."""
        ...


class FakeRobot:
    """A robot that lives in memory. Same interface as the real one.

    Used for tests and for developing the tools without hardware.
    """

    def __init__(self) -> None:
        self.voltage = 12.0
        self.pose = Pose(0.0, 0.0, 0.0)
        self.nearest_obstacle = 2.0
        self.moving = False
        self.localized = True
        self.current_goal: Point | None = None
        self.speed_factor = 1.0
        self.halted_at: float | None = None

        self._places = {
            "the charger": Point(0.0, 0.0),
            "the kitchen": Point(3.0, 0.5),
            "the living room": Point(1.5, 2.5),
            "the bathroom": Point(3.0, -2.0),
            "the front door": Point(-1.0, 1.0),
        }
        # Doorways, as (point, width in metres). The narrowest one a route
        # passes near decides the clearance.
        self._doorways = [
            (Point(1.5, 1.5), 0.80),  # into the living room
            (Point(2.5, -1.0), 0.34),  # into the bathroom, tight
            (Point(2.0, 0.2), 0.55),  # into the kitchen
        ]

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

    def places(self) -> dict[str, Point]:
        return dict(self._places)

    def plan(self, goal: Point, goal_name: str | None) -> MoveProposal:
        start = Point(self.pose.x, self.pose.y)
        path = [start, Point((start.x + goal.x) / 2, (start.y + goal.y) / 2), goal]
        return MoveProposal(
            goal=goal,
            goal_name=goal_name,
            path=path,
            min_clearance=self._clearance(path),
        )

    def drive(self, proposal: MoveProposal, speed_factor: float) -> None:
        self.moving = True
        self.current_goal = proposal.goal
        self.speed_factor = speed_factor
        self.pose = Pose(proposal.goal.x, proposal.goal.y, self.pose.yaw)
        self.moving = False

    def halt(self) -> None:
        self.moving = False
        self.current_goal = None
        self.speed_factor = 0.0
        self.halted_at = time.monotonic()

    def _clearance(self, path: list[Point]) -> float:
        """Narrowest doorway within half a metre of the route."""
        narrowest = float("inf")
        for door, width in self._doorways:
            if any(_distance(p, door) < 0.5 for p in path):
                narrowest = min(narrowest, width)
        return narrowest


def _distance(a: Point, b: Point) -> float:
    return math.hypot(a.x - b.x, a.y - b.y)
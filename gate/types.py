from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum


class Level(IntEnum):
    """Verdict severity. Higher wins when rules disagree."""

    ALLOW = 0
    LIMIT = 1
    STOP = 2


@dataclass(frozen=True)
class Point:
    x: float  # metres, "map" frame
    y: float


@dataclass(frozen=True)
class Pose:
    x: float  # metres, "map" frame
    y: float
    yaw: float  # radians


@dataclass(frozen=True)
class RobotState:
    """A snapshot of the robot, as seen by the adapter."""

    voltage: float  # volts, 5-second rolling mean
    voltage_raw: float  # volts, latest reading
    pose: Pose
    nearest_obstacle: float  # metres, closest LiDAR return
    moving: bool
    localized: bool  # does AMCL have a valid pose?
    timestamp: float  # seconds, monotonic


@dataclass(frozen=True)
class MoveProposal:
    """A move the assistant wants to make. Nothing has moved yet."""

    goal: Point
    goal_name: str | None = None  # e.g. "the charger"
    path: list[Point] = field(default_factory=list)  # empty = no valid plan
    min_clearance: float = float("inf")  # metres, narrowest point on the path


@dataclass(frozen=True)
class Verdict:
    level: Level
    reasons: list[str] = field(default_factory=list)  # full English sentences
    speed_factor: float = 1.0  # 1.0 allow, 0.5 limit, 0.0 stop
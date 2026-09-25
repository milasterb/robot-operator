from __future__ import annotations
from dataclasses import dataclass, field
from .types import Level, MoveProposal, Point, RobotState, Verdict

@dataclass(frozen=True)
class Zone:
    """A named area the robot must not enter."""

    name: str
    polygon: list[Point]  # metres, "map" frame


@dataclass(frozen=True)
class GateConfig:
    # battery, volts (3S pack)
    voltage_stop: float = 10.8
    voltage_limit: float = 11.4
    voltage_hysteresis: float = 0.2  # must recover this much to clear a verdict

    # geometry, metres
    robot_width: float = 0.30  # must match the Nav2 footprint
    clearance_stop_ratio: float = 1.1
    clearance_limit_ratio: float = 1.5
    obstacle_stop: float = 0.30

    # freshness, seconds
    max_state_age: float = 2.0

    charger_name: str = "the charger"
    zones: list[Zone] = field(default_factory=list)


def point_in_polygon(p: Point, polygon: list[Point]) -> bool:
    """Ray casting. Returns True when the point lies inside the polygon."""
    inside = False
    n = len(polygon)
    for i in range(n):
        a, b = polygon[i], polygon[(i + 1) % n]
        if (a.y > p.y) != (b.y > p.y):
            x_cross = a.x + (p.y - a.y) * (b.x - a.x) / (b.y - a.y)
            if p.x < x_cross:
                inside = not inside
    return inside


def check_state_age(
    state: RobotState, proposal: MoveProposal, cfg: GateConfig,
    now: float, previous: Level,
) -> Verdict | None:
    age = now - state.timestamp
    if age > cfg.max_state_age:
        return Verdict(
            Level.STOP,
            [f"I have not heard from the robot for {age:.1f} seconds."],
            0.0,
        )
    return None


def check_localization(
    state: RobotState, proposal: MoveProposal, cfg: GateConfig,
    now: float, previous: Level,
) -> Verdict | None:
    if not state.localized:
        return Verdict(
            Level.STOP,
            ["The robot does not know where it is on the map."],
            0.0,
        )
    return None


def check_battery(
    state: RobotState, proposal: MoveProposal, cfg: GateConfig,
    now: float, previous: Level,
) -> Verdict | None:
    v = state.voltage
    heading_to_charger = proposal.goal_name == cfg.charger_name

    stop_at = cfg.voltage_stop
    limit_at = cfg.voltage_limit
    if previous >= Level.STOP:
        stop_at += cfg.voltage_hysteresis
    if previous >= Level.LIMIT:
        limit_at += cfg.voltage_hysteresis

    if v < stop_at:
        if heading_to_charger:
            return Verdict(
                Level.LIMIT,
                [f"Battery is low at {v:.1f} volts, so I will drive to the "
                 f"charger slowly."],
                0.5,
            )
        return Verdict(
            Level.STOP,
            [f"Battery is down to {v:.1f} volts. The only trip I can make is "
             f"to the charger."],
            0.0,
        )
    if v < limit_at:
        return Verdict(
            Level.LIMIT,
            [f"Battery is at {v:.1f} volts, so I will drive at half speed."],
            0.5,
        )
    return None


def check_plan(
    state: RobotState, proposal: MoveProposal, cfg: GateConfig,
    now: float, previous: Level,
) -> Verdict | None:
    if not proposal.path:
        return Verdict(
            Level.STOP,
            ["I could not find a route to that place."],
            0.0,
        )
    return None


def check_zones(
    state: RobotState, proposal: MoveProposal, cfg: GateConfig,
    now: float, previous: Level,
) -> Verdict | None:
    for zone in cfg.zones:
        if point_in_polygon(proposal.goal, zone.polygon):
            return Verdict(
                Level.STOP,
                [f"That destination is inside {zone.name}, which is off "
                 f"limits."],
                0.0,
            )
        for p in proposal.path:
            if point_in_polygon(p, zone.polygon):
                return Verdict(
                    Level.STOP,
                    [f"The route passes through {zone.name}, which is off "
                     f"limits."],
                    0.0,
                )
    return None


def check_clearance(
    state: RobotState, proposal: MoveProposal, cfg: GateConfig,
    now: float, previous: Level,
) -> Verdict | None:
    c = proposal.min_clearance
    if c < cfg.robot_width * cfg.clearance_stop_ratio:
        return Verdict(
            Level.STOP,
            [f"The narrowest point on the route is {c:.2f} metres, too tight "
             f"for me to fit."],
            0.0,
        )
    if c < cfg.robot_width * cfg.clearance_limit_ratio:
        return Verdict(
            Level.LIMIT,
            [f"The route narrows to {c:.2f} metres, so I will drive at half "
             f"speed."],
            0.5,
        )
    return None


def check_obstacle(
    state: RobotState, proposal: MoveProposal, cfg: GateConfig,
    now: float, previous: Level,
) -> Verdict | None:
    if state.nearest_obstacle < cfg.obstacle_stop:
        return Verdict(
            Level.STOP,
            [f"Something is {state.nearest_obstacle:.2f} metres away from me, "
             f"too close to start moving."],
            0.0,
        )
    return None


RULES = [
    check_state_age,
    check_localization,
    check_battery,
    check_plan,
    check_zones,
    check_clearance,
    check_obstacle,
]
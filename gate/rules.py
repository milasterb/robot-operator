from __future__ import annotations
from dataclasses import dataclass, field
from .types import Level, MoveProposal, Point, RobotState, Verdict
from i18n import num, t

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

    # Set when the map the zones were drawn on is not the map the robot is
    # localising against. Zones are coordinates; in the wrong map they fence
    # off the wrong floor, silently. None means no known disagreement.
    map_mismatch: str | None = None


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
            [t("gate.state_age", age=num(age))],
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
            [t("gate.lost")],
            0.0,
        )
    return None


def check_map(
    state: RobotState, proposal: MoveProposal, cfg: GateConfig,
    now: float, previous: Level,
) -> Verdict | None:
    if cfg.map_mismatch:
        return Verdict(
            Level.STOP,
            [t("gate.map", detail=cfg.map_mismatch)],
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
                [t("gate.battery_to_charger", v=num(v))],
                0.5,
            )
        return Verdict(
            Level.STOP,
            [t("gate.battery_stop", v=num(v))],
            0.0,
        )
    if v < limit_at:
        return Verdict(
            Level.LIMIT,
            [t("gate.battery_limit", v=num(v))],
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
            [t("gate.no_route")],
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
                [t("gate.zone_goal", zone=zone.name)],
                0.0,
            )
        for p in proposal.path:
            if point_in_polygon(p, zone.polygon):
                return Verdict(
                    Level.STOP,
                    [t("gate.zone_route", zone=zone.name)],
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
            [t("gate.too_tight", c=num(c, 2))],
            0.0,
        )
    if c < cfg.robot_width * cfg.clearance_limit_ratio:
        return Verdict(
            Level.LIMIT,
            [t("gate.narrow", c=num(c, 2))],
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
            [t("gate.obstacle", d=num(state.nearest_obstacle, 2))],
            0.0,
        )
    return None


RULES = [
    check_state_age,
    check_localization,
    check_map,
    check_battery,
    check_plan,
    check_zones,
    check_clearance,
    check_obstacle,
]
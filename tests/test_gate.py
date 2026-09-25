from __future__ import annotations

import pytest

from gate.gate import Gate
from gate.rules import GateConfig, Zone
from gate.types import Level, MoveProposal, Point, Pose, RobotState

NOW = 1000.0


def state(**kwargs) -> RobotState:
    """A healthy robot. Override one field per test."""
    defaults = dict(
        voltage=12.0,
        voltage_raw=12.0,
        pose=Pose(0.0, 0.0, 0.0),
        nearest_obstacle=2.0,
        moving=False,
        localized=True,
        timestamp=NOW,
    )
    return RobotState(**{**defaults, **kwargs})


def proposal(**kwargs) -> MoveProposal:
    """A sane trip across the room."""
    defaults = dict(
        goal=Point(3.0, 0.0),
        goal_name="the kitchen",
        path=[Point(0.0, 0.0), Point(1.5, 0.0), Point(3.0, 0.0)],
        min_clearance=1.0,
    )
    return MoveProposal(**{**defaults, **kwargs})


def test_healthy_robot_is_allowed():
    assert Gate().evaluate(state(), proposal(), NOW).level is Level.ALLOW


def test_flat_battery_stops_unless_going_to_the_charger():
    gate = Gate()
    assert gate.evaluate(state(voltage=10.5), proposal(), NOW).level is Level.STOP


def test_flat_battery_allows_a_slow_trip_to_the_charger():
    verdict = Gate().evaluate(
        state(voltage=10.5), proposal(goal_name="the charger"), NOW
    )
    assert verdict.level is Level.LIMIT
    assert verdict.speed_factor == 0.5


def test_low_battery_limits_speed():
    verdict = Gate().evaluate(state(voltage=11.2), proposal(), NOW)
    assert verdict.level is Level.LIMIT
    assert verdict.speed_factor == 0.5


def test_hysteresis_keeps_a_stop_until_the_voltage_recovers():
    gate = Gate()
    # 10.9 V is above the 10.8 V threshold, so a fresh gate only limits.
    assert gate.evaluate(state(voltage=10.9), proposal(), NOW).level is Level.LIMIT
    # After a stop, the threshold moves up to 11.0 V, so the same reading stops.
    gate.commit(gate.evaluate(state(voltage=10.5), proposal(), NOW), proposal())
    assert gate.evaluate(state(voltage=10.9), proposal(), NOW).level is Level.STOP


def test_unlocalized_robot_never_moves():
    assert Gate().evaluate(
        state(localized=False), proposal(), NOW
    ).level is Level.STOP


def test_stale_state_stops():
    assert Gate().evaluate(state(), proposal(), NOW + 5.0).level is Level.STOP


def test_missing_plan_stops():
    assert Gate().evaluate(state(), proposal(path=[]), NOW).level is Level.STOP


def test_goal_inside_a_forbidden_zone_stops():
    bathroom = Zone(
        "the bathroom",
        [Point(2.0, -1.0), Point(4.0, -1.0), Point(4.0, 1.0), Point(2.0, 1.0)],
    )
    gate = Gate(GateConfig(zones=[bathroom]))
    verdict = gate.evaluate(state(), proposal(), NOW)
    assert verdict.level is Level.STOP
    assert "bathroom" in verdict.reasons[0]


def test_route_through_a_forbidden_zone_stops():
    bathroom = Zone(
        "the bathroom",
        [Point(1.0, -1.0), Point(2.0, -1.0), Point(2.0, 1.0), Point(1.0, 1.0)],
    )
    gate = Gate(GateConfig(zones=[bathroom]))
    # The goal is past the bathroom, but the path runs straight through it.
    assert gate.evaluate(state(), proposal(), NOW).level is Level.STOP


def test_a_gap_narrower_than_the_robot_stops():
    assert Gate().evaluate(
        state(), proposal(min_clearance=0.30), NOW
    ).level is Level.STOP


def test_a_tight_gap_limits_speed():
    assert Gate().evaluate(
        state(), proposal(min_clearance=0.40), NOW
    ).level is Level.LIMIT


def test_an_obstacle_right_in_front_stops():
    assert Gate().evaluate(
        state(nearest_obstacle=0.15), proposal(), NOW
    ).level is Level.STOP


def test_the_strictest_rule_wins():
    verdict = Gate().evaluate(
        state(voltage=11.2, localized=False), proposal(), NOW
    )
    assert verdict.level is Level.STOP
    # Only the reasons behind the stop are reported, not the battery note.
    assert all("volts" not in r for r in verdict.reasons)


def test_evaluate_changes_nothing():
    gate = Gate()
    for _ in range(3):
        gate.evaluate(state(voltage=10.5), proposal(), NOW)
    assert len(gate.log) == 0
    assert gate.evaluate(state(voltage=10.9), proposal(), NOW).level is Level.LIMIT


def test_the_same_input_always_gives_the_same_verdict():
    gate = Gate()
    first = gate.evaluate(state(voltage=11.2), proposal(), NOW)
    second = gate.evaluate(state(voltage=11.2), proposal(), NOW)
    assert first == second


def test_explain_stop_reports_the_last_stop():
    gate = Gate()
    gate.commit(gate.evaluate(state(), proposal(), NOW), proposal())
    stop = gate.evaluate(state(localized=False), proposal(), NOW)
    gate.commit(stop, proposal())
    gate.commit(gate.evaluate(state(), proposal(), NOW), proposal())

    entry = gate.last_stop()
    assert entry is not None
    assert "does not know where it is" in entry.reasons[0]
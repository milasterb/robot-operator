from __future__ import annotations

import re
import string

import pytest

import i18n
from gate.gate import Gate
from gate.rules import GateConfig, Zone
from gate.types import Level, MoveProposal, Point, Pose, RobotState

NOW = 1000.0


def fields(text: str) -> set[str]:
    return {f for _, f, _, _ in string.Formatter().parse(text) if f}


@pytest.mark.parametrize("key", sorted(i18n.MESSAGES))
def test_every_message_exists_in_both_languages_with_the_same_blanks(key):
    message = i18n.MESSAGES[key]
    assert set(message) == set(i18n.SUPPORTED)
    assert fields(message["en"]) == fields(message["cs"])


def test_numbers_follow_the_language(czech):
    assert i18n.num(11.44) == "11,4"
    assert i18n.num(0.256, 2) == "0,26"
    assert i18n.percent(0.9) == "90 %"
    assert i18n.seconds_ago(1) == "1 sekundou"
    assert i18n.seconds_ago(12) == "12 sekundami"


def test_numbers_in_english():
    assert i18n.num(11.44) == "11.4"
    assert i18n.percent(0.9) == "90%"


def test_an_unknown_language_is_refused():
    with pytest.raises(ValueError):
        i18n.set_language("de")


def robot(**kw) -> RobotState:
    base = dict(voltage=12.0, voltage_raw=12.0, pose=Pose(0, 0, 0),
                nearest_obstacle=2.0, moving=False, localized=True, timestamp=NOW)
    return RobotState(**{**base, **kw})


def trip(**kw) -> MoveProposal:
    base = dict(goal=Point(3, 0), goal_name="kuchyň",
                path=[Point(0, 0), Point(1.5, 0), Point(3, 0)], min_clearance=1.0)
    return MoveProposal(**{**base, **kw})


def test_the_gate_explains_itself_in_czech(czech):
    zone = Zone("schodiště", [Point(1, -1), Point(2, -1), Point(2, 1), Point(1, 1)])
    verdict = Gate(GateConfig(zones=[zone])).evaluate(robot(), trip(), NOW)
    assert verdict.level is Level.STOP
    assert "„schodiště“" in verdict.reasons[0] and "nesmím" in verdict.reasons[0]

    slow = Gate().evaluate(robot(voltage=11.2), trip(), NOW)
    assert slow.level is Level.LIMIT
    assert "11,2 voltu" in slow.reasons[0]


def test_czech_does_not_change_what_the_gate_decides(czech):
    """Same inputs, same verdicts — only the words differ."""
    cases = [robot(), robot(voltage=10.5), robot(localized=False), robot(nearest_obstacle=0.1)]
    czech_levels = [Gate().evaluate(r, trip(), NOW).level for r in cases]
    i18n.set_language("en")
    english_levels = [Gate().evaluate(r, trip(), NOW).level for r in cases]
    assert czech_levels == english_levels


def test_the_offer_carries_a_token_the_panel_can_find(czech):
    text = i18n.t("tool.offer", name="kuchyň", warning="", token="Ab3_x-9")
    assert re.search(r"token(?:em)? ([A-Za-z0-9_-]+)", text).group(1) == "Ab3_x-9"
    assert re.sub(r"\s*(Confirm with token|Potvrď to tokenem).*$", "", text) == "Můžu jet na místo „kuchyň“."


def test_spoken_place_names_ignore_diacritics_and_case():
    from server import _fold
    assert _fold("Kuchyně") == _fold("kuchyne") == "kuchyne"
    assert _fold("  OBÝVÁK ") == "obyvak"

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass

from .rules import RULES, GateConfig
from .types import Level, MoveProposal, RobotState, Verdict


@dataclass(frozen=True)
class LogEntry:
    timestamp: float
    level: Level
    reasons: list[str]
    goal_name: str | None


class Gate:
    """Deterministic safety gate. No model, no randomness.

    The same state and proposal always produce the same verdict, apart from
    hysteresis, which depends on the previous verdict only.
    """

    def __init__(self, config: GateConfig | None = None, log_size: int = 200):
        self.config = config or GateConfig()
        self._previous = Level.ALLOW
        self.log: deque[LogEntry] = deque(maxlen=log_size)

    def evaluate(
        self,
        state: RobotState,
        proposal: MoveProposal,
        now: float | None = None,
    ) -> Verdict:
        """Judge one proposal. Does not move anything and does not log."""
        now = time.monotonic() if now is None else now

        verdicts = [
            v for rule in RULES
            if (v := rule(state, proposal, self.config, now, self._previous))
            is not None
        ]

        if not verdicts:
            return Verdict(Level.ALLOW, ["The route is clear."], 1.0)

        level = max(v.level for v in verdicts)
        reasons = [r for v in verdicts if v.level == level for r in v.reasons]
        speed = min(v.speed_factor for v in verdicts)
        return Verdict(level, reasons, speed)

    def commit(self, verdict: Verdict, proposal: MoveProposal) -> None:
        """Record a verdict that was acted on. Drives hysteresis and the log."""
        self._previous = verdict.level
        self.log.append(
            LogEntry(time.monotonic(), verdict.level, verdict.reasons,
                     proposal.goal_name)
        )

    def last_stop(self) -> LogEntry | None:
        """The most recent STOP, for explain_stop."""
        for entry in reversed(self.log):
            if entry.level >= Level.STOP:
                return entry
        return None
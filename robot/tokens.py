from __future__ import annotations

import secrets
import time
from dataclasses import dataclass

from gate.types import MoveProposal, Verdict

TOKEN_LIFETIME = 30.0  # seconds


@dataclass(frozen=True)
class PendingMove:
    token: str
    proposal: MoveProposal
    verdict: Verdict
    issued_at: float
    voltage_at_issue: float


class TokenStore:
    """Holds moves that have been proposed but not yet confirmed.

    A token is single use and short lived. Confirming one does not mean
    "do what I said"; it means "do exactly this, if it is still true".
    """

    def __init__(self, lifetime: float = TOKEN_LIFETIME) -> None:
        self.lifetime = lifetime
        self._pending: dict[str, PendingMove] = {}

    def issue(
        self, proposal: MoveProposal, verdict: Verdict, voltage: float,
        now: float | None = None,
    ) -> PendingMove:
        now = time.monotonic() if now is None else now
        self._expire(now)
        pending = PendingMove(
            token=secrets.token_urlsafe(8),
            proposal=proposal,
            verdict=verdict,
            issued_at=now,
            voltage_at_issue=voltage,
        )
        self._pending[pending.token] = pending
        return pending

    def redeem(self, token: str, now: float | None = None) -> PendingMove:
        """Spend a token. Raises TokenError if it is not usable."""
        now = time.monotonic() if now is None else now
        self._expire(now)

        pending = self._pending.pop(token, None)
        if pending is None:
            raise TokenError(
                "That confirmation is no longer valid. Ask me again and I will "
                "work out a fresh route."
            )
        return pending

    def discard_all(self) -> None:
        """Drop every pending move. Called on an emergency stop."""
        self._pending.clear()

    def _expire(self, now: float) -> None:
        dead = [
            t for t, p in self._pending.items()
            if now - p.issued_at > self.lifetime
        ]
        for t in dead:
            del self._pending[t]


class TokenError(Exception):
    """A token was missing, spent, or too old. The message is spoken aloud."""
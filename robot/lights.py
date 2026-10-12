"""The light bar on the back of the robot, carrying the gate's verdict.

The panel shows the verdict to whoever is holding the laptop. The light bar
shows it to anyone in the room, which is the point: an operator with both
hands busy is not looking at a screen. Industrial machines signal their state
the same way, and the colours mean here what they mean there.

Colours are signals, not decoration:

    idle     dim blue     powered, listening, nothing being decided
    waiting  amber        a move is proposed and waiting on a human
    allow    green        cleared and moving
    limit    orange       moving under a restriction
    stop     red          refused, or stopped

Only `waiting` animates. It breathes slowly rather than blinking, because a
blinking machine reads as a fault and this is an ordinary pause. Every other
state is published once and left alone — the light bar hangs off the same
serial link as the motors and the IMU, so this code stays quiet unless it has
something to say.
"""

from __future__ import annotations

import enum
import math
import threading
import time
from typing import Callable, Protocol


class Level(enum.Enum):
    IDLE = "idle"
    WAITING = "waiting"
    ALLOW = "allow"
    LIMIT = "limit"
    STOP = "stop"


# (red, green, blue, brightness), each 0..1
COLOURS: dict[Level, tuple[float, float, float, float]] = {
    Level.IDLE:    (0.00, 0.30, 1.00, 0.25),
    Level.WAITING: (1.00, 0.75, 0.00, 0.50),
    Level.ALLOW:   (0.00, 1.00, 0.25, 1.00),
    Level.LIMIT:   (1.00, 0.55, 0.00, 1.00),
    Level.STOP:    (1.00, 0.00, 0.00, 1.00),
}

BREATH_PERIOD = 2.0    # seconds for one full breath
BREATH_FLOOR = 0.10    # dimmest point of the breath
STOP_HOLD = 4.0        # seconds a refusal stays red before going back to idle
TICK = 0.05            # 20 Hz, only while breathing


class Publisher(Protocol):
    def __call__(self, r: float, g: float, b: float, a: float) -> None: ...


class LightBar:
    """Drives the light bar from the gate's verdicts.

    Thread-safe: `set` can be called from any thread. A single daemon thread
    does the breathing and the stop timeout; it sleeps when neither applies.
    """

    def __init__(self, publish: Publisher) -> None:
        self._publish = publish
        self._lock = threading.Lock()
        self._level: Level | None = None  # nothing shown yet
        self._since = time.monotonic()
        self._wake = threading.Event()
        self._running = True

        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        self.set(Level.IDLE)

    def set(self, level: Level) -> None:
        """Show a new state. Publishes immediately; animation follows."""
        with self._lock:
            if level is self._level and level is not Level.STOP:
                return  # already showing it; a repeated STOP restarts its hold
            self._level = level
            self._since = time.monotonic()

        r, g, b, a = COLOURS[level]
        self._emit(r, g, b, a)
        self._wake.set()

    def off(self) -> None:
        self._emit(0.0, 0.0, 0.0, 0.0)

    def close(self) -> None:
        self._running = False
        self._wake.set()
        self.off()

    # ---- internals ----------------------------------------------------

    def _emit(self, r: float, g: float, b: float, a: float) -> None:
        try:
            self._publish(r, g, b, a)
        except Exception:
            pass  # a dark light bar must never take the robot down with it

    def _loop(self) -> None:
        while self._running:
            with self._lock:
                level, since = self._level, self._since
            age = time.monotonic() - since

            if level is Level.WAITING:
                # sine from BREATH_FLOOR up to the state's own brightness
                r, g, b, peak = COLOURS[level]
                phase = (age % BREATH_PERIOD) / BREATH_PERIOD
                swing = (1 - math.cos(2 * math.pi * phase)) / 2
                self._emit(r, g, b, BREATH_FLOOR + swing * (peak - BREATH_FLOOR))
                self._wake.wait(TICK)
                self._wake.clear()
                continue

            if level is Level.STOP and age >= STOP_HOLD:
                self.set(Level.IDLE)
                continue

            # Nothing animating: wait to be woken, but look again when a
            # STOP is due to expire.
            timeout = max(0.05, STOP_HOLD - age) if level is Level.STOP else None
            self._wake.wait(timeout)
            self._wake.clear()


def verdict_level(verdict_name: str, moving: bool = True) -> Level:
    """Map a gate verdict onto a light state.

    `moving` distinguishes a cleared move that is under way from one that is
    still waiting on the person: the gate says ALLOW in both cases, but the
    light must not go green before anyone has agreed to anything.
    """
    if verdict_name == "STOP":
        return Level.STOP
    if not moving:
        return Level.WAITING
    return Level.LIMIT if verdict_name == "LIMIT" else Level.ALLOW
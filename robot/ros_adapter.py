from __future__ import annotations

import math
import statistics
import threading
import time
from collections import deque

import roslibpy

from gate.types import Pose, RobotState

VOLTAGE_WINDOW = 5.0  # seconds of readings to average


class RosRobot:
    """Talks to a ROSMASTER X3 over rosbridge. Same shape as FakeRobot."""

    def __init__(self, host: str, port: int = 9090) -> None:
        self.ros = roslibpy.Ros(host=host, port=port)
        self._lock = threading.Lock()

        self._voltages: deque[tuple[float, float]] = deque()  # (time, volts)
        self._pose = Pose(0.0, 0.0, 0.0)
        self._nearest = float("inf")
        self._moving = False
        self._last_update = 0.0

    def connect(self, timeout: float = 10.0) -> None:
        self.ros.run(timeout=timeout)
        if not self.ros.is_connected:
            raise ConnectionError(f"rosbridge at {self.ros.host} did not answer")

        roslibpy.Topic(self.ros, "/voltage", "std_msgs/Float32").subscribe(
            self._on_voltage
        )
        roslibpy.Topic(self.ros, "/odom", "nav_msgs/Odometry").subscribe(
            self._on_odom
        )
        roslibpy.Topic(
            self.ros, "/scan", "sensor_msgs/LaserScan",
            throttle_rate=200, queue_length=1,
        ).subscribe(self._on_scan)

    def _on_voltage(self, msg: dict) -> None:
        now = time.monotonic()
        with self._lock:
            self._voltages.append((now, msg["data"]))
            while self._voltages and now - self._voltages[0][0] > VOLTAGE_WINDOW:
                self._voltages.popleft()
            self._last_update = now

    def _on_odom(self, msg: dict) -> None:
        p = msg["pose"]["pose"]["position"]
        q = msg["pose"]["pose"]["orientation"]
        yaw = math.atan2(
            2.0 * (q["w"] * q["z"] + q["x"] * q["y"]),
            1.0 - 2.0 * (q["y"] ** 2 + q["z"] ** 2),
        )
        v = msg["twist"]["twist"]["linear"]
        speed = math.hypot(v["x"], v["y"])
        with self._lock:
            self._pose = Pose(p["x"], p["y"], yaw)
            self._moving = speed > 0.02
            self._last_update = time.monotonic()

    def _on_scan(self, msg: dict) -> None:
        good = [
            r for r in msg["ranges"]
            if r is not None and msg["range_min"] < r < msg["range_max"]
        ]
        with self._lock:
            self._nearest = min(good) if good else float("inf")

    def get_state(self) -> RobotState:
        with self._lock:
            readings = [v for _, v in self._voltages]
            return RobotState(
                voltage=statistics.fmean(readings) if readings else 0.0,
                voltage_raw=readings[-1] if readings else 0.0,
                pose=self._pose,
                nearest_obstacle=self._nearest,
                moving=self._moving,
                localized=bool(readings),  # replaced by AMCL later
                timestamp=self._last_update,
            )

    def close(self) -> None:
        if self.ros.is_connected:
            self.ros.terminate()
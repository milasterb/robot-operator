from __future__ import annotations

import math
import statistics
import threading
import time
from collections import deque

import roslibpy
import roslibpy.actionlib

from gate.types import MoveProposal, Point, Pose, RobotState
from robot.lights import LightBar, Level as LightLevel

VOLTAGE_WINDOW = 5.0  # seconds of readings to average


class RosRobot:
    """Talks to a ROSMASTER X3 over rosbridge. Same shape as FakeRobot.

    Reading happens through topic subscriptions; driving goes through the
    Nav2 action server. The gate never sees this class — it only ever gets
    a RobotState and a MoveProposal.
    """

    # Named destinations, in the coordinates of maps/obyvak_final.yaml.
    # Measured from the map, not guessed: each one sits in open floor with
    # room for the robot to turn around. Rename them to whatever you would
    # actually say out loud; only the coordinates matter to the gate.
    PLACES: dict[str, Point] = {
        "my room": Point(-0.35, 0.00),       # 0.70 m clear around it
        "the living room": Point(2.30, 0.15),  # 0.87 m
        "the window": Point(3.45, 0.35),     # 0.96 m, the most open spot
        # Inside the forbidden zone. It is a real, reachable place — Nav2
        # will plan a route to it — which is the point: the gate refuses it
        # on its own authority, not because the planner failed.
        "the treadmill": Point(2.00, -0.42),
    }

    def __init__(self, host: str, port: int = 9090) -> None:
        self.ros = roslibpy.Ros(host=host, port=port)
        self._lock = threading.Lock()

        self._voltages: deque[tuple[float, float]] = deque()  # (time, volts)
        self._pose = Pose(0.0, 0.0, 0.0)
        self._nearest = float("inf")
        self._moving = False
        self._last_update = 0.0

        self._current_goal = None
        self._current_path: list[Point] = []
        self._nav_client = None
        self._light_topic = None
        self.lights: LightBar | None = None

    # ---- lifecycle ----------------------------------------------------

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

        self._nav_client = roslibpy.actionlib.ActionClient(
            self.ros, "/navigate_to_pose", "nav2_msgs/NavigateToPose"
        )

        # The light bar needs a patched Yahboom driver; see
        # patches/Mcnamu_driver_X3.patch. Without it /gate_light has no
        # subscriber and these publishes go nowhere, which is harmless.
        self._light_topic = roslibpy.Topic(
            self.ros, "/gate_light", "std_msgs/ColorRGBA"
        )
        self.lights = LightBar(self._publish_colour)

    def _publish_colour(self, r: float, g: float, b: float, a: float) -> None:
        if self._light_topic is not None and self.ros.is_connected:
            self._light_topic.publish(
                roslibpy.Message({"r": r, "g": g, "b": b, "a": a})
            )

    def set_light(self, level: LightLevel) -> None:
        if self.lights is not None:
            self.lights.set(level)

    def close(self) -> None:
        if self.lights is not None:
            self.lights.close()
        if self.ros.is_connected:
            self.ros.terminate()

    # ---- incoming data ------------------------------------------------

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

    # ---- state --------------------------------------------------------

    def get_state(self) -> RobotState:
        with self._lock:
            readings = [v for _, v in self._voltages]
            return RobotState(
                voltage=statistics.fmean(readings) if readings else 0.0,
                voltage_raw=readings[-1] if readings else 0.0,
                pose=self._pose,
                nearest_obstacle=self._nearest,
                moving=self._moving,
                localized=bool(readings),  # replaced by AMCL covariance later
                timestamp=self._last_update,
            )

    def places(self) -> dict[str, Point]:
        return dict(self.PLACES)

    @property
    def current_path(self) -> list[Point]:
        with self._lock:
            return list(self._current_path)

    # ---- planning -----------------------------------------------------

    def plan(self, goal: Point, goal_name: str | None) -> MoveProposal:
        """Ask Nav2 for a route. Does not move the robot."""
        client = roslibpy.Service(
            self.ros, "/compute_path_to_pose", "nav2_msgs/ComputePathToPose"
        )
        try:
            result = client.call(
                roslibpy.ServiceRequest({"goal": _pose_stamped(goal)}),
                timeout=10,
            )
            path = [
                Point(p["pose"]["position"]["x"], p["pose"]["position"]["y"])
                for p in result.get("path", {}).get("poses", [])
            ]
        except Exception:
            path = []  # no plan is a valid answer; the gate turns it into a stop

        return MoveProposal(
            goal=goal,
            goal_name=goal_name,
            path=path,
            min_clearance=self._clearance_along(path),
        )

    def _clearance_along(self, path: list[Point]) -> float:
        """Narrowest free space on the route.

        Conservative: derived from the closest laser return rather than
        sampling the costmap, so the gate errs towards stopping.
        """
        if not path:
            return float("inf")
        with self._lock:
            nearest = self._nearest
        return nearest * 2.0 if nearest != float("inf") else float("inf")

    # ---- driving ------------------------------------------------------

    def drive(self, proposal: MoveProposal, speed_factor: float) -> None:
        """Send the goal to Nav2. Returns once the goal is accepted."""
        self._set_speed_limit(speed_factor)

        goal = roslibpy.actionlib.Goal(
            self._nav_client,
            roslibpy.Message({"pose": _pose_stamped(proposal.goal)}),
        )
        goal.send()
        with self._lock:
            self._current_goal = goal
            self._current_path = list(proposal.path)

    def halt(self) -> None:
        """Stop now. Never gated, never confirmed.

        Cancels the Nav2 goal and also publishes a zero velocity straight to
        the base, so the wheels stop even if the action server is slow to
        react.
        """
        with self._lock:
            goal, self._current_goal = self._current_goal, None
            self._current_path = []

        if goal is not None:
            try:
                goal.cancel()
            except Exception:
                pass

        roslibpy.Topic(self.ros, "/cmd_vel", "geometry_msgs/Twist").publish(
            roslibpy.Message(
                {
                    "linear": {"x": 0.0, "y": 0.0, "z": 0.0},
                    "angular": {"x": 0.0, "y": 0.0, "z": 0.0},
                }
            )
        )

    def _set_speed_limit(self, factor: float) -> None:
        """Ask the controller to drive slower. LIMIT verdicts use this."""
        roslibpy.Topic(self.ros, "/speed_limit", "nav2_msgs/SpeedLimit").publish(
            roslibpy.Message({"percentage": True, "speed_limit": factor * 100.0})
        )


def _pose_stamped(p: Point) -> dict:
    return {
        "header": {"frame_id": "map"},
        "pose": {
            "position": {"x": p.x, "y": p.y, "z": 0.0},
            "orientation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
        },
    }
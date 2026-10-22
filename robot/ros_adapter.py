from __future__ import annotations

import math
import statistics
import threading
import time
from collections import deque

import roslibpy

from gate.types import MoveProposal, Point, Pose, RobotState
from robot.lights import LightBar, Level as LightLevel

VOLTAGE_WINDOW = 5.0        # seconds of readings to average

# What counts as knowing where you are. AMCL publishes a 6x6 covariance;
# the square roots of its diagonal are standard deviations in metres and
# radians. Half a metre and about thirty degrees is the point past which
# the robot's own estimate is too vague to drive on.
AMCL_POSITION_STD = 0.50    # metres
AMCL_YAW_STD = 0.52         # radians, about 30 degrees
AMCL_MAX_AGE_MOVING = 5.0   # seconds; see get_state for why only moving

PLAN_TIMEOUT = 10.0         # seconds to wait for onboard/plan_relay.py

# Nav2 Foxy has no /speed_limit topic — that arrived in Galactic — so a
# LIMIT verdict is enforced by writing the controller's own velocity
# parameters. The robot is mecanum, so capping x alone would leave it free
# to strafe and spin at full speed.
SPEED_PARAMS = (
    "FollowPath.max_vel_x",
    "FollowPath.max_vel_y",
    "FollowPath.max_speed_xy",
    "FollowPath.max_vel_theta",
)
PARAM_DOUBLE = 3            # rcl_interfaces/ParameterType.PARAMETER_DOUBLE


class RosRobot:
    """Talks to a ROSMASTER X3 over rosbridge. Same shape as FakeRobot.

    Reading happens through topic subscriptions; driving goes through Nav2.
    The gate never sees this class — it only ever gets a RobotState and a
    MoveProposal.

    Goals go to the /goal_pose topic rather than the NavigateToPose action,
    because roslibpy 2.x ships an action client for ROS 1 only and its ros2
    package has none at all. /goal_pose is the topic RViz's "2D Goal Pose"
    button publishes, so Nav2 treats it as a first-class way in. The cost is
    in halt(), which has to cancel through the action's own service instead
    of holding a goal handle.
    """

    # Named destinations, in the coordinates of maps/obyvak_final.yaml.
    # Measured from the map, not guessed: each one sits in open floor with
    # room for the robot to turn around.
    PLACES: dict[str, Point] = {
        "my room": Point(-0.35, 0.00),         # 0.70 m clear around it
        "the living room": Point(2.30, 0.15),  # 0.87 m
        # Inside the forbidden zone. It is a real, reachable place — Nav2
        # will plan a route to it — which is the point: the gate refuses it
        # on its own authority, not because the planner failed.
        "the treadmill": Point(2.00, -0.42),
    }

    def __init__(self, host: str, port: int = 9090) -> None:
        self.ros = roslibpy.Ros(host=host, port=port)
        self._lock = threading.Lock()

        self._voltages: deque[tuple[float, float]] = deque()  # (time, volts)
        self._pose = Pose(0.0, 0.0, 0.0)   # map frame, from AMCL
        self._yaw = 0.0                    # radians, map frame
        self._amcl_at = 0.0                # when the last AMCL pose arrived
        self._amcl_ok = False              # was its covariance tight enough
        self._nearest = float("inf")
        self._moving = False
        self._last_update = 0.0

        self._goal_outstanding = False
        self._current_path: list[Point] = []
        self._goal_topic = None
        self._cmd_vel = None
        self._light_topic = None
        self._speed_base: dict[str, float] = {}

        # One plan at a time. propose_move is sequential by construction —
        # a proposal has to be confirmed or expire before the next one —
        # so a single slot needs no request ids to match answers to asks.
        self._plan_request = None
        self._plan_done = threading.Event()
        self._plan_path: list[Point] = []
        self.lights: LightBar | None = None

    # ---- lifecycle ----------------------------------------------------

    def connect(self, timeout: float = 10.0) -> None:
        self.ros.run(timeout=timeout)
        if not self.ros.is_connected:
            raise ConnectionError(f"rosbridge at {self.ros.host} did not answer")

        roslibpy.Topic(self.ros, "/voltage", "std_msgs/Float32").subscribe(
            self._on_voltage
        )
        # Odometry is only good for "is it moving". Its frame drifts, and the
        # named places are in the map frame, so the position comes from AMCL.
        roslibpy.Topic(self.ros, "/odom", "nav_msgs/Odometry").subscribe(
            self._on_odom
        )
        roslibpy.Topic(
            self.ros, "/amcl_pose", "geometry_msgs/PoseWithCovarianceStamped"
        ).subscribe(self._on_amcl)
        roslibpy.Topic(
            self.ros, "/scan", "sensor_msgs/LaserScan",
            throttle_rate=200, queue_length=1,
        ).subscribe(self._on_scan)

        # Advertised once and reused. Publishing through a Topic created on
        # the fly works, but each one advertises and un-advertises, and an
        # emergency stop is the worst moment to pay that.
        self._goal_topic = roslibpy.Topic(
            self.ros, "/goal_pose", "geometry_msgs/PoseStamped"
        )
        self._cmd_vel = roslibpy.Topic(
            self.ros, "/cmd_vel", "geometry_msgs/Twist"
        )
        self._read_speed_base()

        # Routes come from onboard/plan_relay.py, not from the action
        # directly: rosbridge resolves service types in <pkg>.srv and an
        # action's hidden services live in <pkg>.action, so the action is
        # unreachable from here. The relay does the call on the robot.
        self._plan_request = roslibpy.Topic(
            self.ros, "/gate/plan_request", "geometry_msgs/PoseStamped"
        )
        roslibpy.Topic(self.ros, "/gate/plan_result", "nav_msgs/Path").subscribe(
            self._on_plan
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
            # Hand the controller back at its nominal speed. Leaving it
            # capped would silently slow down anything else that drives
            # this robot afterwards, including RViz by hand.
            self._set_speed_limit(1.0)
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
        """Only the velocity is used. The position here is in the odom frame,
        which drifts away from the map, and everything the gate reasons about
        — places, zones — is in the map frame."""
        v = msg["twist"]["twist"]["linear"]
        speed = math.hypot(v["x"], v["y"])
        with self._lock:
            self._moving = speed > 0.02
            self._last_update = time.monotonic()

    def _on_amcl(self, msg: dict) -> None:
        """Position in the map frame, and how sure AMCL is about it."""
        p = msg["pose"]["pose"]["position"]
        q = msg["pose"]["pose"]["orientation"]
        yaw = math.atan2(
            2.0 * (q["w"] * q["z"] + q["x"] * q["y"]),
            1.0 - 2.0 * (q["y"] ** 2 + q["z"] ** 2),
        )
        cov = msg["pose"]["covariance"]  # 6x6, row major
        std_x = math.sqrt(max(0.0, cov[0]))
        std_y = math.sqrt(max(0.0, cov[7]))
        std_yaw = math.sqrt(max(0.0, cov[35]))

        now = time.monotonic()
        with self._lock:
            self._pose = Pose(p["x"], p["y"], yaw)
            self._yaw = yaw          # kept apart so stops can hold the heading
            self._amcl_at = now
            self._amcl_ok = (
                max(std_x, std_y) <= AMCL_POSITION_STD
                and std_yaw <= AMCL_YAW_STD
            )
            self._last_update = now

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
                localized=self._is_localized(),
                timestamp=self._last_update,
            )

    def _is_localized(self) -> bool:
        """Does the robot know where it is? Call with the lock held.

        Three ways to answer no: AMCL has never spoken, so there is no map
        pose at all and no amount of odometry substitutes for one; its last
        covariance was too loose to drive on; or it has gone quiet while the
        robot is moving, which means the pose on file is not where the robot
        is any more.

        The staleness check only applies while moving on purpose. A parked
        robot gives AMCL nothing to update from, so it stops publishing —
        treating that silence as being lost would refuse every trip that
        starts after a pause. Stale telemetry in general is the gate's own
        max_state_age rule, fed by the other topics.
        """
        if self._amcl_at == 0.0 or not self._amcl_ok:
            return False
        if self._moving:
            return (time.monotonic() - self._amcl_at) <= AMCL_MAX_AGE_MOVING
        return True

    def places(self) -> dict[str, Point]:
        return dict(self.PLACES)

    @property
    def current_path(self) -> list[Point]:
        with self._lock:
            return list(self._current_path)

    # ---- planning -----------------------------------------------------

    def _on_plan(self, msg: dict) -> None:
        points = [
            Point(p["pose"]["position"]["x"], p["pose"]["position"]["y"])
            for p in msg.get("poses", [])
        ]
        with self._lock:
            self._plan_path = points
        self._plan_done.set()

    def plan(self, goal: Point, goal_name: str | None) -> MoveProposal:
        """Ask Nav2 for a route, via the onboard relay. Does not move."""
        self._plan_done.clear()
        with self._lock:
            self._plan_path = []

        self._plan_request.publish(roslibpy.Message(_pose_stamped(goal)))

        if self._plan_done.wait(PLAN_TIMEOUT):
            with self._lock:
                path = list(self._plan_path)
        else:
            # Either the relay is not running or the planner is wedged.
            # No route is a valid answer and the gate turns it into a
            # refusal, which is the right way to fail.
            path = []

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
        """Send the goal to Nav2. Returns once the goal has been published.

        The speed limit goes out first. Sending it after the goal would
        leave a window in which a LIMIT verdict drives at full speed, which
        is exactly the thing the verdict exists to prevent.

        And if the cap cannot be applied at all, the trip does not happen.
        A robot that announces half speed and then drives at full speed is
        worse than one that refuses: the operator would be trusting a limit
        that is not there.
        """
        capped = self._set_speed_limit(speed_factor)
        if speed_factor < 1.0 and not capped:
            raise RuntimeError(
                "the controller would not accept a speed cap, so a limited "
                "trip would run at full speed — not driving"
            )
        self._goal_topic.publish(
            roslibpy.Message(
                _pose_stamped(proposal.goal, self._arrival_yaw(proposal.path))
            )
        )
        with self._lock:
            self._goal_outstanding = True
            self._current_path = list(proposal.path)

    def _arrival_yaw(self, path: list[Point]) -> float:
        """Which way to be facing on arrival: the way it was travelling.

        Anything else makes the robot turn on the spot once it is already
        there, which reads as confusion rather than arrival. A path too
        short to have a direction leaves the heading where it is.
        """
        if len(path) >= 2:
            a, b = path[-2], path[-1]
            if math.hypot(b.x - a.x, b.y - a.y) > 1e-6:
                return math.atan2(b.y - a.y, b.x - a.x)
        with self._lock:
            return self._yaw

    def halt(self) -> None:
        """Stop now. Never gated, never confirmed.

        Zeroing the velocity is instant but not durable: the controller
        publishes its own command at about 20 Hz, so it overwrites ours
        within 50 ms. Something has to make it stop asking for motion.

        Cancelling the goal would be the clean way, but the cancel service
        belongs to the action and so is unreachable through rosbridge for
        the same reason the planner was. What works with topics alone is a
        goal at the robot's own current position: the controller then has
        nowhere left to go, reaches its goal tolerance, and stands down of
        its own accord. A stranger way to stop a robot than cancelling, but
        it needs nothing that is not already proven to work.
        """
        self._zero_velocity()

        with self._lock:
            had_goal, self._goal_outstanding = self._goal_outstanding, False
            self._current_path = []
            here, facing = self._pose, self._yaw

        if had_goal:
            # Current heading, not zero: a stop must not turn the robot.
            self._goal_topic.publish(
                roslibpy.Message(_pose_stamped(Point(here.x, here.y), facing))
            )

        self._zero_velocity()

    def _zero_velocity(self) -> None:
        self._cmd_vel.publish(
            roslibpy.Message(
                {
                    "linear": {"x": 0.0, "y": 0.0, "z": 0.0},
                    "angular": {"x": 0.0, "y": 0.0, "z": 0.0},
                }
            )
        )

    def _read_speed_base(self) -> None:
        """Remember the controller's nominal speeds, once, at connect.

        Scaling needs something to scale from, and it has to be read before
        anything has scaled it. If the process is killed mid-trip the
        parameters stay capped, and the next run would mistake the capped
        values for nominal — so close() puts them back, and a kill -9 is
        worth a glance at `ros2 param get` before trusting the next run.
        """
        try:
            res = roslibpy.Service(
                self.ros,
                "/controller_server/get_parameters",
                "rcl_interfaces/GetParameters",
            ).call(
                roslibpy.ServiceRequest({"names": list(SPEED_PARAMS)}), timeout=5
            )
        except Exception:
            return  # _set_speed_limit refuses to limit without a baseline

        for name, value in zip(SPEED_PARAMS, res.get("values", [])):
            if value.get("type") == PARAM_DOUBLE:
                self._speed_base[name] = value["double_value"]

    def _set_speed_limit(self, factor: float) -> bool:
        """Cap the controller's speed. True if the cap actually landed.

        ALLOW passes 1.0, which writes the nominal values back, so a limited
        trip cannot leave the robot crawling on the next one.
        """
        if not self._speed_base:
            return False
        params = [
            {
                "name": name,
                "value": {"type": PARAM_DOUBLE, "double_value": base * factor},
            }
            for name, base in self._speed_base.items()
        ]
        try:
            res = roslibpy.Service(
                self.ros,
                "/controller_server/set_parameters",
                "rcl_interfaces/SetParameters",
            ).call(
                roslibpy.ServiceRequest({"parameters": params}), timeout=5
            )
        except Exception:
            return False
        return all(r.get("successful") for r in res.get("results", []))


def _pose_stamped(p: Point, yaw: float = 0.0) -> dict:
    """A goal pose in the map frame.

    The heading is not decoration. Nav2 drives to the position and then
    turns to face the orientation it was given, so leaving this at the
    default means every trip ends with the robot rotating to face +x —
    which looks like a fault and, during a stop, is one.
    """
    return {
        "header": {"frame_id": "map"},
        "pose": {
            "position": {"x": p.x, "y": p.y, "z": 0.0},
            "orientation": {
                "x": 0.0,
                "y": 0.0,
                "z": math.sin(yaw / 2.0),
                "w": math.cos(yaw / 2.0),
            },
        },
    }
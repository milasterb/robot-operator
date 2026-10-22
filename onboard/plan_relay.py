"""Expose Nav2's ComputePathToPose action as a pair of plain topics.

Why this node exists
--------------------
The gate has to see the route before the robot drives it — that is the
whole point of propose/confirm. Nav2 will produce that route on demand
through the ComputePathToPose action, but the operator runs off-board and
talks to the robot over rosbridge, and rosbridge cannot reach ROS 2
actions: it resolves every service type in `<pkg>.srv`, while an action's
hidden services live in `<pkg>.action`. So the call fails with "module
'nav2_msgs.srv' has no attribute 'ComputePathToPose_SendGoal'" no matter
how the type is spelled.

Here on the robot there is no such limit, because rclpy speaks actions
natively. So this node does the action call and republishes the result as
a nav_msgs/Path, which rosbridge forwards without complaint.

    /gate/plan_request   geometry_msgs/PoseStamped   in   (where to)
    /gate/plan_result    nav_msgs/Path               out  (how to get there)

An unreachable goal answers with an empty Path rather than silence. The
gate treats no route as a refusal, which is the correct outcome, but only
if it gets an answer at all — silence would just look like a timeout.

Run it inside the ROS 2 container, alongside the navigation stack:

    python3 plan_relay.py
"""

import rclpy
from nav2_msgs.action import ComputePathToPose
from nav_msgs.msg import Path
from geometry_msgs.msg import PoseStamped
from rclpy.action import ActionClient
from rclpy.node import Node

ACTION_TIMEOUT = 5.0  # seconds to wait for the action server at startup


class PlanRelay(Node):
    def __init__(self) -> None:
        super().__init__("gate_plan_relay")
        self._client = ActionClient(self, ComputePathToPose, "compute_path_to_pose")
        self._result = self.create_publisher(Path, "/gate/plan_result", 1)
        self.create_subscription(
            PoseStamped, "/gate/plan_request", self._on_request, 1
        )

        if not self._client.wait_for_server(timeout_sec=ACTION_TIMEOUT):
            self.get_logger().warn(
                "compute_path_to_pose is not up yet — requests will be "
                "answered with an empty path until it is"
            )
        self.get_logger().info("relay ready on /gate/plan_request")

    # Each step hands off through a done-callback rather than waiting. A
    # single-threaded executor cannot spin inside its own callback, so
    # blocking on the future here would deadlock the node on its first
    # request.

    def _on_request(self, msg: PoseStamped) -> None:
        goal = ComputePathToPose.Goal()
        # Foxy calls this field `pose`; later distros renamed it `goal` and
        # added start/use_start. Asking the message which it is costs
        # nothing and makes the node portable across both.
        if hasattr(goal, "goal"):
            goal.goal = msg
        else:
            goal.pose = msg

        self.get_logger().info(
            f"plan requested to ({msg.pose.position.x:+.2f}, "
            f"{msg.pose.position.y:+.2f})"
        )
        self._client.send_goal_async(goal).add_done_callback(self._on_accepted)

    def _on_accepted(self, future) -> None:
        try:
            handle = future.result()
        except Exception as exc:
            self.get_logger().error(f"send_goal failed: {exc}")
            return self._answer_empty()

        if not handle.accepted:
            self.get_logger().warn("planner rejected the goal")
            return self._answer_empty()

        handle.get_result_async().add_done_callback(self._on_result)

    def _on_result(self, future) -> None:
        try:
            path = future.result().result.path
        except Exception as exc:
            self.get_logger().error(f"get_result failed: {exc}")
            return self._answer_empty()

        self.get_logger().info(f"planned {len(path.poses)} poses")
        self._result.publish(path)

    def _answer_empty(self) -> None:
        empty = Path()
        empty.header.frame_id = "map"
        self._result.publish(empty)


def main() -> None:
    rclpy.init()
    node = PlanRelay()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()

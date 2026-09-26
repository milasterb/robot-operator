# check_ros.py
from robot.ros_adapter import RosRobot
import time

r = RosRobot("192.168.1.11")
r.connect()
for _ in range(10):
    time.sleep(1)
    print(r.get_state())
r.close()
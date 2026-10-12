# Patches

## `Mcnamu_driver_X3.patch`

Robot Operator shows the safety gate's verdict on the LED bar across the back
of the ROSMASTER X3. Yahboom's driver node owns the serial link to the board
and does not expose the LEDs in a usable way: the stock `RGBLight` topic takes
an `Int32` that selects one of the board's canned effects, so it cannot carry
an arbitrary colour.

This patch adds a `gate_light` subscriber taking `std_msgs/ColorRGBA`. The RGB
channels are floats in 0..1 and alpha scales brightness, so one colour can be
published dim (idle) or full (a verdict). Nothing else in the driver changes.

### Applying it

On the robot, in the Docker container that runs ROS 2:

```bash
cd /root/yahboomcar_ros2_ws/yahboomcar_ws/src
patch -p1 < Mcnamu_driver_X3.patch
cd /root/yahboomcar_ros2_ws/yahboomcar_ws
colcon build --packages-select yahboomcar_bringup --symlink-install
```

Then restart the bring-up launch.

### Checking it

```bash
ros2 topic pub --once /gate_light std_msgs/ColorRGBA "{r: 1.0, g: 0.0, b: 0.0, a: 1.0}"
```

The bar should go red. If nothing happens, the driver was not rebuilt, or two
processes are holding the serial port — see the friction log.

Without this patch everything else still works: `RosRobot` publishes to
`/gate_light` regardless, and with no subscriber those messages go nowhere.
The panel shows the same verdicts on screen.

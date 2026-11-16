"""ARCADIA native robotics bring-up.

    ros2 launch arcadia_bringup arcadia.launch.py [gui:=true|false] [stream_port:=1234] [api_url:=http://localhost:8080/api]

Starts, in order:
  1. Webots (native snap/tarball) with the ARCADIA world, W3D streaming server on :stream_port
  2. the C++ supervisor (extern controller) that spawns the farm from the backend world config
  3. one rclpy extern controller per robot (uav-1, ugv-1, agv-1)
  4. telemetry_throttle, diagnostic_aggregator, rosbridge_websocket (+rosapi), world_fetcher
"""
from __future__ import annotations

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, ExecuteProcess, OpaqueFunction, RegisterEventHandler, TimerAction
from launch.event_handlers import OnProcessExit
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node

ROBOTS = ["uav-1", "ugv-1", "agv-1"]

# Everything runs on this laptop: keep DDS discovery on loopback so Wi-Fi changes/sleep never
# break the graph (multicast writes to a vanished interface spam CycloneDDS errors otherwise).
os.environ.setdefault("ROS_AUTOMATIC_DISCOVERY_RANGE", "LOCALHOST")
_CYCLONE_CFG = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "..", "robotics", "config", "cyclonedds.xml"))
for _cand in (_CYCLONE_CFG, os.path.expanduser("~/WORK/Projects/ARCADIA/robotics/config/cyclonedds.xml")):
    if os.path.isfile(_cand):
        os.environ.setdefault("CYCLONEDDS_URI", f"file://{_cand}")
        break


def _webots_home() -> str:
    candidates = (os.environ.get("WEBOTS_HOME"), os.path.expanduser("~/opt/webots"), os.path.expanduser("~/webots"),
                  "/usr/local/webots", "/opt/webots", "/snap/webots/current/usr/share/webots")
    for c in candidates:
        if c and os.path.isdir(os.path.join(c, "lib", "controller")):
            if c.startswith("/snap/") and os.environ.get("XDG_SESSION_TYPE") == "wayland":
                print("[arcadia] WARNING: the Webots snap cannot open an Xwayland display; if Webots dies with "
                      "'cannot open display', run robotics/scripts/install_webots_tarball.sh", flush=True)
            return c
    raise RuntimeError("Webots not found; run robotics/scripts/install_webots_tarball.sh or set WEBOTS_HOME")


def _webots_bin(home: str) -> str:
    return "/snap/bin/webots" if home.startswith("/snap/") else os.path.join(home, "webots")


def _controller_env(home: str, robot: str) -> dict:
    env = dict(os.environ)
    env["WEBOTS_HOME"] = home
    env["WEBOTS_CONTROLLER_URL"] = f"ipc://1234/{robot}"
    env["LD_LIBRARY_PATH"] = f"{home}/lib/controller:" + env.get("LD_LIBRARY_PATH", "")
    env["PYTHONPATH"] = f"{home}/lib/controller/python:" + env.get("PYTHONPATH", "")
    # keep the Python controllers responsive next to the Webots GUI
    env["PYTHONUNBUFFERED"] = "1"
    return env


def launch_setup(context, *args, **kwargs):
    home = _webots_home()
    sim_share = get_package_share_directory("arcadia_sim")
    bringup_share = get_package_share_directory("arcadia_bringup")
    world = os.path.join(sim_share, "worlds", "arcadia_farm.wbt")
    gui = LaunchConfiguration("gui").perform(context).lower() in ("1", "true", "yes")
    stream_port = LaunchConfiguration("stream_port").perform(context)
    api_url = LaunchConfiguration("api_url").perform(context)
    mode = LaunchConfiguration("mode").perform(context)

    webots_args = [_webots_bin(home), f"--mode={mode}", "--batch", "--stdout", "--stderr", "--stream", f"--port={stream_port}"]
    if not gui:
        webots_args += ["--minimize", "--no-rendering"]
    webots_args.append(world)
    webots_env = {"WEBOTS_HOME": home, "QT_QPA_PLATFORM": os.environ.get("QT_QPA_PLATFORM", "xcb"),
                  "LD_LIBRARY_PATH": f"{home}/lib/webots:{home}/lib/controller:" + os.environ.get("LD_LIBRARY_PATH", "")}
    # GNOME on Wayland: Xwayland needs the mutter auth cookie; shells spawned outside the session lack it.
    if not os.environ.get("XAUTHORITY"):
        import glob
        cookies = glob.glob(f"/run/user/{os.getuid()}/.mutter-Xwaylandauth.*")
        if cookies:
            webots_env["XAUTHORITY"] = cookies[0]
    webots_env.setdefault("DISPLAY", os.environ.get("DISPLAY", ":0"))
    webots = ExecuteProcess(cmd=webots_args, name="webots", output="screen", additional_env=webots_env)

    supervisor = ExecuteProcess(
        cmd=[os.path.join(get_package_share_directory("arcadia_supervisor"), "..", "..", "lib", "arcadia_supervisor", "supervisor")],
        name="supervisor", output="screen", additional_env={**_controller_env(home, "arcadia_supervisor")},
        respawn=True, respawn_delay=3.0)

    controllers = [
        Node(package="arcadia_sim", executable="robot_controller", name=f"ctrl_{r.replace('-', '_')}", output="screen",
             additional_env=_controller_env(home, r), respawn=True, respawn_delay=3.0)
        for r in ROBOTS
    ]

    throttle = Node(package="arcadia_sim", executable="telemetry_throttle", name="telemetry_throttle", output="screen",
                    parameters=[{"rate_hz": 5.0, "robots": ROBOTS}])
    aggregator = Node(package="diagnostic_aggregator", executable="aggregator_node", name="diagnostic_aggregator", output="screen",
                      parameters=[os.path.join(sim_share, "config", "diagnostics.yaml")])
    rosbridge = Node(package="rosbridge_server", executable="rosbridge_websocket", name="rosbridge_websocket", output="screen",
                     parameters=[os.path.join(bringup_share, "config", "rosbridge.yaml")])
    rosapi = Node(package="rosapi", executable="rosapi_node", name="rosapi", output="screen")
    fetcher = Node(package="arcadia_sim", executable="world_fetcher", name="world_fetcher", output="screen",
                   parameters=[{"api_url": api_url}])

    # Webots must be listening before extern controllers connect: stagger start-up.
    return [
        webots,
        TimerAction(period=6.0, actions=[supervisor]),
        TimerAction(period=9.0, actions=controllers),
        TimerAction(period=2.0, actions=[rosbridge, rosapi, aggregator, throttle]),
        TimerAction(period=12.0, actions=[fetcher]),
        RegisterEventHandler(OnProcessExit(target_action=webots, on_exit=[ExecuteProcess(cmd=["echo", "Webots exited - shutting down ARCADIA sim"], output="screen")])),
    ]


def generate_launch_description() -> LaunchDescription:
    return LaunchDescription([
        DeclareLaunchArgument("gui", default_value="true", description="Show the Webots window (false = minimised, no rendering)"),
        DeclareLaunchArgument("mode", default_value="realtime", description="Webots mode: realtime | fast | pause"),
        DeclareLaunchArgument("stream_port", default_value="1234", description="Webots W3D streaming server port"),
        DeclareLaunchArgument("api_url", default_value="http://localhost:8080/api", description="ARCADIA backend API base URL"),
        OpaqueFunction(function=launch_setup),
    ])

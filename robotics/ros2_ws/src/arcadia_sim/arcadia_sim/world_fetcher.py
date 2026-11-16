"""Pulls the active world config from the backend REST API and publishes a world_load Command.

The backend also pushes world_load whenever rosbridge connects, so this is the belt-and-braces
path for starting the sim after a farm was already activated (or when running without rosbridge).
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

import rclpy
from rclpy.node import Node
from std_srvs.srv import Trigger

from arcadia_msgs.msg import Command


class WorldFetcher(Node):
    def __init__(self) -> None:
        super().__init__("world_fetcher")
        self.declare_parameter("api_url", "http://localhost:8080/api")
        self.declare_parameter("retry_s", 5.0)
        self.pub = self.create_publisher(Command, "/arcadia/command", 10)
        self.create_service(Trigger, "/arcadia/reload_world", self.on_reload)
        self.loaded = False
        self.create_timer(float(self.get_parameter("retry_s").value), self.tick)
        self.create_timer(2.0, self.tick)  # first attempt quickly

    def tick(self) -> None:
        if self.loaded:
            return
        ok, msg = self.fetch_and_publish()
        if ok:
            self.loaded = True
            self.get_logger().info(msg)
        else:
            self.get_logger().debug(msg)

    def on_reload(self, _req, res):
        ok, msg = self.fetch_and_publish()
        res.success, res.message = ok, msg
        return res

    def fetch_and_publish(self) -> tuple[bool, str]:
        url = f"{self.get_parameter('api_url').value}/world/active"
        try:
            with urllib.request.urlopen(url, timeout=5) as r:
                world = json.loads(r.read())
        except urllib.error.HTTPError as e:
            return False, f"no active world yet ({e.code})"
        except Exception as e:  # noqa: BLE001
            return False, f"backend unreachable at {url}: {e}"
        cmd = Command(command_id="fetcher", robot_id="", kind="world_load",
                      payload_json=json.dumps({"farm_id": world["farm"]["id"], "world": world}, separators=(",", ":")))
        self.pub.publish(cmd)
        return True, f"published world_load for farm {world['farm']['name']} ({len(world.get('sections', []))} bed sections)"


def main(args=None) -> None:
    rclpy.init(args=args)
    node = WorldFetcher()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()

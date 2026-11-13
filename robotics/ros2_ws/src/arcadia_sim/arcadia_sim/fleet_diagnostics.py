"""Fallback aggregator used only when ros-<distro>-diagnostic-aggregator is unavailable:
collapses /diagnostics into per-robot OK/WARN/ERROR on /diagnostics_agg with the same naming
scheme (arcadia/<robot>/<component>) the backend bridge expects."""
from __future__ import annotations

import rclpy
from diagnostic_msgs.msg import DiagnosticArray, DiagnosticStatus
from rclpy.node import Node


class FleetDiagnostics(Node):
    def __init__(self) -> None:
        super().__init__("fleet_diagnostics")
        self.latest: dict[str, DiagnosticStatus] = {}
        self.create_subscription(DiagnosticArray, "/diagnostics", self.on_diag, 20)
        self.pub = self.create_publisher(DiagnosticArray, "/diagnostics_agg", 10)
        self.create_timer(1.0, self.publish)

    def on_diag(self, msg: DiagnosticArray) -> None:
        for s in msg.status:
            self.latest[s.name] = s

    def publish(self) -> None:
        out = DiagnosticArray()
        out.header.stamp = self.get_clock().now().to_msg()
        robots: dict[str, list[DiagnosticStatus]] = {}
        for name, s in self.latest.items():
            parts = name.split("/")
            if len(parts) >= 3 and parts[0] == "arcadia":
                robots.setdefault(parts[1], []).append(s)
        for rid, items in robots.items():
            level = max((int.from_bytes(i.level, "big") if isinstance(i.level, bytes) else int(i.level)) for i in items)
            top = DiagnosticStatus(name=f"/arcadia/{rid}", level=bytes([level]), hardware_id=rid,
                                   message={0: "OK", 1: "WARN", 2: "ERROR"}.get(level, "STALE"))
            out.status.append(top)
            for i in items:
                c = DiagnosticStatus(name=f"/arcadia/{rid}/{i.name.split('/')[-1]}", level=i.level, message=i.message, hardware_id=rid, values=i.values)
                out.status.append(c)
        self.pub.publish(out)


def main(args=None) -> None:
    rclpy.init(args=args)
    node = FleetDiagnostics()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()

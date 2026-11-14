"""Road-aware routing for ground vehicles.

Missions for UGV/AGV are routed robot -> nearest road -> along the road network -> nearest road
cell to the target -> target, so the fleet visibly uses the planned travel roads instead of
cutting across crop beds. Pure BFS over H3 adjacency (grids are a few hundred cells).
"""
from __future__ import annotations

from collections import deque

import h3

from app.schemas import Waypoint


class RoadNetwork:
    def __init__(self, cells: list[dict]):
        """cells: dicts with h3, x, y, lat, lng, kind."""
        self.cells = {c["h3"]: c for c in cells}
        self.road = {h for h, c in self.cells.items() if c["kind"] == "road"}
        self.neigh: dict[str, list[str]] = {}
        for h in self.road:
            self.neigh[h] = [n for n in h3.grid_ring(h, 1) if n in self.road]

    def nearest_road(self, x: float, y: float) -> str | None:
        if not self.road:
            return None
        return min(self.road, key=lambda h: (self.cells[h]["x"] - x) ** 2 + (self.cells[h]["y"] - y) ** 2)

    def path(self, a: str, b: str) -> list[str]:
        if a == b:
            return [a]
        prev = {a: None}
        q = deque([a])
        while q:
            cur = q.popleft()
            for n in self.neigh.get(cur, []):
                if n in prev:
                    continue
                prev[n] = cur
                if n == b:
                    out = [n]
                    while prev[out[-1]] is not None:
                        out.append(prev[out[-1]])
                    return out[::-1]
                q.append(n)
        return []

    def route(self, sx: float, sy: float, target: dict, z: float = 0.0) -> list[Waypoint]:
        """Waypoints from (sx, sy) to target via roads; falls back to a direct leg."""
        direct = [Waypoint(x=target["x"], y=target["y"], z=z, h3=target.get("h3"), lat=target.get("lat"), lng=target.get("lng"))]
        entry = self.nearest_road(sx, sy)
        exit_ = self.nearest_road(target["x"], target["y"])
        if entry is None or exit_ is None:
            return direct
        # not worth the detour when the target is right next to us
        if (target["x"] - sx) ** 2 + (target["y"] - sy) ** 2 < (2.5 * self._edge()) ** 2:
            return direct
        p = self.path(entry, exit_)
        if not p:
            return direct
        pts = [self.cells[h] for h in self._simplify(p)]
        wps = [Waypoint(x=c["x"], y=c["y"], z=z, h3=c["h3"], lat=c.get("lat"), lng=c.get("lng")) for c in pts]
        return wps + direct

    def _edge(self) -> float:
        any_h = next(iter(self.cells))
        return h3.average_hexagon_edge_length(h3.get_resolution(any_h), unit="m")

    @staticmethod
    def _simplify(path: list[str]) -> list[str]:
        """Drop cells that lie on a straight run (keep turns and endpoints)."""
        if len(path) <= 2:
            return path
        out = [path[0]]
        for i in range(1, len(path) - 1):
            a, b, c = path[i - 1], path[i], path[i + 1]
            try:
                ia, ib, ic = (h3.cell_to_local_ij(a, x) for x in (a, b, c))
                if (ib[0] - ia[0], ib[1] - ia[1]) == (ic[0] - ib[0], ic[1] - ib[1]):
                    continue
            except Exception:  # pragma: no cover
                pass
            out.append(b)
        out.append(path[-1])
        return out

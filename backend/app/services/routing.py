"""Road-aware routing for ground vehicles.

Weighted shortest path (Dijkstra) over the whole H3 grid: travel roads and infrastructure pads
are cheap, driving across crop beds is expensive (soil compaction, crop damage). Vehicles
therefore follow the planned roads and only cut across a bed when the road detour would be
disproportionate. Grids are a few hundred cells, so this runs in well under a millisecond.
"""
from __future__ import annotations

import heapq

import h3

from app.schemas import Waypoint

COST = {"road": 1.0, "dock": 1.0, "storage": 1.0, "repair": 1.0, "crop": 6.0}


class RoadNetwork:
    def __init__(self, cells: list[dict]):
        """cells: dicts with h3, x, y, lat, lng, kind."""
        self.cells = {c["h3"]: c for c in cells}
        self.road = {h for h, c in self.cells.items() if c["kind"] == "road"}
        self.neigh: dict[str, list[str]] = {h: [n for n in h3.grid_ring(h, 1) if n in self.cells] for h in self.cells}

    def nearest(self, x: float, y: float, pool=None) -> str | None:
        pool = pool if pool is not None else self.cells.keys()
        best, bd = None, float("inf")
        for h in pool:
            c = self.cells[h]
            d = (c["x"] - x) ** 2 + (c["y"] - y) ** 2
            if d < bd:
                best, bd = h, d
        return best

    def nearest_road(self, x: float, y: float) -> str | None:
        return self.nearest(x, y, self.road) if self.road else None

    def path(self, a: str, b: str) -> list[str]:
        """Cheapest cell path from a to b (cost of a step = cost of the cell entered)."""
        if a == b:
            return [a]
        dist = {a: 0.0}
        prev: dict[str, str | None] = {a: None}
        pq = [(0.0, a)]
        while pq:
            d, cur = heapq.heappop(pq)
            if cur == b:
                break
            if d > dist.get(cur, float("inf")):
                continue
            for n in self.neigh.get(cur, []):
                nd = d + COST.get(self.cells[n]["kind"], 6.0)
                if nd < dist.get(n, float("inf")):
                    dist[n], prev[n] = nd, cur
                    heapq.heappush(pq, (nd, n))
        if b not in prev:
            return []
        out = [b]
        while prev[out[-1]] is not None:
            out.append(prev[out[-1]])
        return out[::-1]

    def route(self, sx: float, sy: float, target: dict, z: float = 0.0) -> list[Waypoint]:
        """Waypoints from (sx, sy) to target, preferring roads; falls back to a direct leg."""
        direct = [Waypoint(x=target["x"], y=target["y"], z=z, h3=target.get("h3"), lat=target.get("lat"), lng=target.get("lng"))]
        start = self.nearest(sx, sy)
        goal = target.get("h3") if target.get("h3") in self.cells else self.nearest(target["x"], target["y"])
        if start is None or goal is None:
            return direct
        p = self.path(start, goal)
        if len(p) <= 2:
            return direct
        pts = [self.cells[h] for h in self._simplify(p[1:-1])]
        return [Waypoint(x=c["x"], y=c["y"], z=z, h3=c["h3"], lat=c.get("lat"), lng=c.get("lng")) for c in pts] + direct

    def _simplify(self, path: list[str]) -> list[str]:
        """Keep turns and road/field transitions; drop cells in the middle of straight runs."""
        if len(path) <= 2:
            return path
        out = [path[0]]
        for i in range(1, len(path) - 1):
            a, b, c = path[i - 1], path[i], path[i + 1]
            kinds_change = self.cells[a]["kind"] != self.cells[b]["kind"] or self.cells[b]["kind"] != self.cells[c]["kind"]
            try:
                ia, ib, ic = (h3.cell_to_local_ij(a, x) for x in (a, b, c))
                straight = (ib[0] - ia[0], ib[1] - ia[1]) == (ic[0] - ib[0], ic[1] - ib[1])
            except Exception:  # pragma: no cover
                straight = False
            if straight and not kinds_change:
                continue
            out.append(b)
        out.append(path[-1])
        return out

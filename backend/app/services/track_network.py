"""Track network router: ground vehicles only ever drive on tracks.

The headland, tramlines, alleys and yard spurs are noded into one planar graph (every crossing
becomes a junction). Routing snaps the start and goal onto the nearest track segment and runs
Dijkstra over track length, so a route can never cross a crop bed - there is simply no edge
there. Grids are small (a few thousand segments), so a query takes a few milliseconds.
"""
from __future__ import annotations

import heapq
import math
from collections import defaultdict

from shapely.geometry import LineString, Point
from shapely.ops import unary_union
from shapely.strtree import STRtree

Node = tuple[float, float]


def _key(x: float, y: float) -> Node:
    return (round(x, 2), round(y, 2))


class TrackNetwork:
    def __init__(self, tracks: list[dict]):
        """tracks: [{id, kind, coords:[[x,y],...]}] in local ENU metres."""
        lines = [LineString(t["coords"]) for t in tracks if len(t.get("coords") or []) >= 2]
        merged = unary_union(lines)  # nodes every intersection
        segs: list[LineString] = []
        for g in getattr(merged, "geoms", [merged]):
            if g.geom_type == "LineString" and g.length > 0:
                cs = list(g.coords)
                segs.extend(LineString([cs[i], cs[i + 1]]) for i in range(len(cs) - 1))
        self.adj: dict[Node, list[tuple[Node, float]]] = defaultdict(list)
        for s in segs:
            self._link(_key(*s.coords[0]), _key(*s.coords[1]))
        self._rebuild_index()
        self._heal()

    def _link(self, a: Node, b: Node) -> None:
        if a == b or any(v == b for v, _ in self.adj[a]):
            return
        d = math.dist(a, b)
        self.adj[a].append((b, d))
        self.adj[b].append((a, d))

    def _unlink(self, a: Node, b: Node) -> None:
        self.adj[a] = [(v, w) for v, w in self.adj[a] if v != b]
        self.adj[b] = [(v, w) for v, w in self.adj[b] if v != a]

    def _rebuild_index(self) -> None:
        edges = {(a, b) if a < b else (b, a) for a, nbrs in self.adj.items() for b, _ in nbrs}
        self.segments = [LineString([a, b]) for a, b in sorted(edges)]
        self.tree = STRtree(self.segments) if self.segments else None

    def _heal(self, tol: float = 0.05) -> None:
        """Join dead ends that stop a hair short of (or just past) another track. Coordinates are
        rounded to the millimetre, so a spur drawn to the headland can miss it by 0.3 mm and the
        union never nodes it: the yard pad becomes an island and no route reaches it."""
        if self.tree is None:
            return
        splits: dict[int, list[tuple[Node, Node]]] = defaultdict(list)
        for n in [n for n, nbrs in self.adj.items() if len(nbrs) == 1]:
            p = Point(n)
            best = None
            for i in self.tree.query(p.buffer(tol)):
                seg = self.segments[int(i)]
                a, b = _key(*seg.coords[0]), _key(*seg.coords[1])
                if n in (a, b):
                    continue
                d = seg.distance(p)
                if d <= tol and (best is None or d < best[0]):
                    best = (d, int(i), seg)
            if best is not None:
                q = best[2].interpolate(best[2].project(p))
                splits[best[1]].append((_key(q.x, q.y), n))
        if not splits:
            return
        for i, joins in splits.items():
            seg = self.segments[i]
            a, b = _key(*seg.coords[0]), _key(*seg.coords[1])
            self._unlink(a, b)
            chain = [a] + [q for q, _ in sorted(joins, key=lambda j: math.dist(a, j[0]))] + [b]
            for u, v in zip(chain, chain[1:]):
                self._link(u, v)
            for q, n in joins:
                self._link(q, n)
        self._rebuild_index()

    def graph(self) -> dict:
        """Compact, JSON-friendly graph for the robot controllers: node coordinates and edges as
        node-index pairs. Every crossing is already a node, so controllers only need to snap onto
        the nearest edge and run Dijkstra."""
        idx: dict[Node, int] = {}
        for n in self.adj:
            idx.setdefault(n, len(idx))
        edges = sorted({(min(idx[a], idx[b]), max(idx[a], idx[b])) for a, nbrs in self.adj.items() for b, _ in nbrs})
        return {"nodes": [[n[0], n[1]] for n in idx], "edges": [list(e) for e in edges]}

    # ------------------------------------------------------------------ snapping
    def snap(self, x: float, y: float) -> tuple[Node, LineString | None, float]:
        """Nearest point on the network: (point, segment, distance)."""
        if self.tree is None:
            return _key(x, y), None, float("inf")
        p = Point(x, y)
        idx = self.tree.nearest(p)
        seg = self.segments[int(idx)]
        q = seg.interpolate(seg.project(p))
        return _key(q.x, q.y), seg, p.distance(q)

    # ------------------------------------------------------------------ routing
    def path(self, sx: float, sy: float, tx: float, ty: float) -> tuple[list[Node], float]:
        """Shortest on-track polyline from (sx,sy) to (tx,ty), both snapped to the network."""
        s, s_seg, _ = self.snap(sx, sy)
        t, t_seg, _ = self.snap(tx, ty)
        if s_seg is None or t_seg is None:
            return [], float("inf")
        extra: dict[Node, list[tuple[Node, float]]] = defaultdict(list)

        def attach(n: Node, seg: LineString):
            a, b = _key(*seg.coords[0]), _key(*seg.coords[1])
            for e in (a, b):
                d = math.dist(n, e)
                extra[n].append((e, d))
                extra[e].append((n, d))

        attach(s, s_seg)
        attach(t, t_seg)
        if s_seg.equals(t_seg):  # same segment: go straight
            d = math.dist(s, t)
            extra[s].append((t, d))
            extra[t].append((s, d))
        dist = {s: 0.0}
        prev: dict[Node, Node | None] = {s: None}
        pq = [(0.0, s)]
        while pq:
            d, u = heapq.heappop(pq)
            if u == t:
                break
            if d > dist.get(u, math.inf):
                continue
            for v, w in self.adj.get(u, []) + extra.get(u, []):
                nd = d + w
                if nd < dist.get(v, math.inf):
                    dist[v], prev[v] = nd, u
                    heapq.heappush(pq, (nd, v))
        if t not in prev:
            return [], float("inf")
        out = [t]
        while prev[out[-1]] is not None:
            out.append(prev[out[-1]])
        return out[::-1], dist[t]

    def route(self, sx: float, sy: float, tx: float, ty: float) -> tuple[list[Node], float]:
        """Simplified waypoints (turns only) from start to goal along the tracks."""
        pts, length = self.path(sx, sy, tx, ty)
        return _simplify(pts), length


def _simplify(pts: list[Node], tol: float = 0.25) -> list[Node]:
    """Drop collinear interior points (keep every turn) - controllers follow straight legs."""
    if len(pts) <= 2:
        return pts
    out = [pts[0]]
    for i in range(1, len(pts) - 1):
        a, b, c = out[-1], pts[i], pts[i + 1]
        # distance of b from the line a-c
        ax, ay = a
        cx, cy = c
        bx, by = b
        L = math.dist(a, c)
        if L < 1e-6:
            continue
        dev = abs((cx - ax) * (ay - by) - (ax - bx) * (cy - ay)) / L
        if dev > tol:
            out.append(b)
    out.append(pts[-1])
    # drop zero-length steps
    clean = [out[0]]
    for q in out[1:]:
        if math.dist(q, clean[-1]) > 0.05:
            clean.append(q)
    return clean

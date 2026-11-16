"""Algorithmic infrastructure planning on a controlled-traffic field layout.

1. The field is laid out as beds / tramlines / headland / alleys (services.layout).
2. K-Means++ (numpy) over bed-section centroids (weighted by area) places the Renewable
   Charging Docks; Robot Storage sits at the dock-weighted centre; the Repair Station next to
   storage. Every site is a pad in the farm yard just *outside* the headland, joined to it by a
   short spur track - infrastructure never takes cropped ground.
3. Travel latency is measured on the track network (vehicles cannot cut across beds).
Deterministic given a seed.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

import numpy as np
from shapely.geometry import LineString, Point, Polygon

from app.services.field import Field
from app.services.geo import Origin
from app.services.layout import Layout, LayoutParams, Track, generate_layout
from app.services.track_network import TrackNetwork


@dataclass
class KMeansResult:
    centroids: np.ndarray
    labels: np.ndarray
    inertia: float
    iterations: int
    converged: bool


def kmeans(points: np.ndarray, k: int, seed: int = 42, max_iter: int = 100, tol: float = 1e-4,
           weights: np.ndarray | None = None) -> KMeansResult:
    """Lloyd's algorithm with k-means++ seeding (optionally weighted). points: (n, 2) metres."""
    n = len(points)
    k = max(1, min(k, n))
    w = np.ones(n) if weights is None else np.asarray(weights, dtype=float)
    rng = np.random.default_rng(seed)
    centroids = np.empty((k, 2))
    centroids[0] = points[rng.choice(n, p=w / w.sum())]
    d2 = np.sum((points - centroids[0]) ** 2, axis=1)
    for i in range(1, k):
        pr = d2 * w
        pr = pr / pr.sum() if pr.sum() > 0 else np.full(n, 1.0 / n)
        centroids[i] = points[rng.choice(n, p=pr)]
        d2 = np.minimum(d2, np.sum((points - centroids[i]) ** 2, axis=1))
    labels = np.zeros(n, dtype=int)
    converged, it = False, 0
    for it in range(1, max_iter + 1):
        dists = np.linalg.norm(points[:, None, :] - centroids[None, :, :], axis=2)
        labels = dists.argmin(axis=1)
        new_c = centroids.copy()
        for j in range(k):
            m = labels == j
            if m.any():
                new_c[j] = (points[m] * w[m, None]).sum(axis=0) / w[m].sum()
        shift = np.linalg.norm(new_c - centroids, axis=1).max()
        centroids = new_c
        if shift < tol:
            converged = True
            break
    dists = np.linalg.norm(points[:, None, :] - centroids[None, :, :], axis=2)
    return KMeansResult(centroids, labels, float((dists.min(axis=1) ** 2 * w).sum()), it, converged)


@dataclass
class PlannedSite:
    slug: str
    kind: str  # solar_dock | wind_dock | storage | repair_station
    x: float
    y: float
    lat: float
    lng: float
    cluster_size: int
    capacity_kw: float
    storage_kwh: float
    access: list[float] = field(default_factory=list)  # headland junction of its spur [x, y]


@dataclass
class PlanReport:
    docks: list[PlannedSite]
    storage: list[PlannedSite]
    repair: list[PlannedSite]
    layout: Layout
    k_docks: int
    k_storage: int
    inertia: float
    iterations: int
    converged: bool
    mean_travel_m: float
    p95_travel_m: float
    max_travel_m: float

    @property
    def sites(self) -> list[PlannedSite]:
        return self.docks + self.storage + self.repair

    def to_dict(self) -> dict:
        L = self.layout
        return {
            "docks": [asdict(s) for s in self.docks], "storage": [asdict(s) for s in self.storage],
            "repair": [asdict(s) for s in self.repair], "k_docks": self.k_docks, "k_storage": self.k_storage,
            "inertia": self.inertia, "iterations": self.iterations, "converged": self.converged,
            "mean_travel_m": self.mean_travel_m, "p95_travel_m": self.p95_travel_m, "max_travel_m": self.max_travel_m,
            "layout": {"angle_deg": round(math.degrees(L.angle), 2), "params": asdict(L.params),
                       "beds": L.beds, "sections": len(L.sections),
                       "tramlines": sum(1 for t in L.tracks if t.kind == "lane"),
                       "alleys": sum(1 for t in L.tracks if t.kind == "alley"),
                       "hazards": len(L.hazards), "field_area_m2": round(L.field_area_m2, 1),
                       "crop_area_m2": round(L.crop_area_m2, 1), "track_length_m": round(L.track_length_m, 1),
                       "cropped_pct": round(100 * L.crop_area_m2 / max(L.field_area_m2, 1.0), 1)},
        }


def _yard_pad(ring: LineString, field_poly: Polygon, along: float, offset: float) -> tuple[Point, Point]:
    """Junction on the headland centre line at `along` metres, and a pad `offset` metres outward
    (outside the field boundary)."""
    total = ring.length
    q = ring.interpolate(along % total)
    q2 = ring.interpolate((along + 1.0) % total)
    tx, ty = q2.x - q.x, q2.y - q.y
    n = math.hypot(tx, ty) or 1.0
    tx, ty = tx / n, ty / n
    for nx, ny in ((-ty, tx), (ty, -tx)):
        pad = Point(q.x + nx * offset, q.y + ny * offset)
        if not field_poly.contains(pad):
            return q, pad
    return q, Point(q.x - ty * offset, q.y + tx * offset)


def _insert_vertex(track: Track, pt: list[float]) -> None:
    """Add `pt` to the track polyline as a vertex on its nearest leg, so another track ending at
    `pt` shares that exact coordinate (and the track union nodes them together)."""
    cs = track.coords
    if any(c[0] == pt[0] and c[1] == pt[1] for c in cs):
        return
    best = min(range(len(cs) - 1), key=lambda i: LineString([cs[i], cs[i + 1]]).distance(Point(pt)))
    cs.insert(best + 1, [pt[0], pt[1]])


def plan_infrastructure(fld: Field, k_docks: int | None = None, k_storage: int | None = None, seed: int = 42,
                        params: LayoutParams | None = None) -> PlanReport:
    origin: Origin = fld.origin
    params = params or LayoutParams(seed=seed)
    field_xy = fld.polygon_xy if fld.boundary_lnglat else None
    if field_xy is None or field_xy.is_empty:
        raise ValueError("farm has no boundary")
    layout = generate_layout(field_xy, params)
    secs = layout.sections
    if not secs:
        raise ValueError("no crop beds fit - reduce headland/bed width or draw a larger field")
    pts = np.array([[s.x, s.y] for s in secs])
    wts = np.array([s.length * s.width for s in secs])
    if k_docks is None:
        k_docks = int(np.clip(round(layout.crop_area_m2 / 25_000), 1, 8))  # ~1 dock per 2.5 ha of beds
    if k_storage is None:
        k_storage = 1 if layout.field_area_m2 < 250_000 else 2

    km = kmeans(pts, k_docks, seed=seed, weights=wts)
    head = next(t for t in layout.tracks if t.kind == "headland")
    ring = LineString(head.coords)
    pad_offset = params.headland_width / 2 + 9.0  # pad centre ~9 m beyond the field edge
    used_along: list[float] = []

    def place(target_xy: tuple[float, float], min_gap: float = 30.0) -> tuple[Point, Point]:
        along = ring.project(Point(*target_xy))
        total = ring.length
        for _ in range(40):  # keep pads apart along the headland
            if all(min(abs(along - u), total - abs(along - u)) >= min_gap for u in used_along):
                break
            along = (along + min_gap * 0.6) % total
        used_along.append(along)
        return _yard_pad(ring, field_xy, along, pad_offset)

    def site(slug: str, kind: str, junction: Point, pad: Point, size: int = 0, cap: float = 0.0, store: float = 0.0) -> PlannedSite:
        lat, lng = origin.to_latlng(pad.x, pad.y)
        return PlannedSite(slug=slug, kind=kind, x=round(pad.x, 3),
                           y=round(pad.y, 3), lat=lat, lng=lng, cluster_size=size, capacity_kw=cap, storage_kwh=store,
                           access=[round(junction.x, 3), round(junction.y, 3)])

    docks: list[PlannedSite] = []
    order = np.argsort(-np.bincount(km.labels, minlength=len(km.centroids)))
    for rank, j in enumerate(order):
        size = int((km.labels == j).sum())
        jn, pad = place(tuple(km.centroids[j]))
        kind = "solar_dock" if rank % 2 == 0 else "wind_dock"
        docks.append(site(f"dock-{rank + 1}", kind, jn, pad, size, round(8.0 + 0.05 * size, 2), round(40.0 + 0.2 * size, 1)))

    dock_xy = np.array([[d.x, d.y] for d in docks])
    dw = np.array([max(1, d.cluster_size) for d in docks], dtype=float)
    storage: list[PlannedSite] = []
    targets = [(dock_xy * dw[:, None]).sum(axis=0) / dw.sum()] if (k_storage == 1 or len(docks) == 1) else \
        list(kmeans(dock_xy, k_storage, seed=seed + 1, weights=dw).centroids)
    for i, t in enumerate(targets):
        jn, pad = place(tuple(t))
        storage.append(site(f"storage-{i + 1}", "storage", jn, pad))
    # repair station beside storage-1 (along the headland)
    s_along = ring.project(Point(storage[0].access[0], storage[0].access[1]))
    jn, pad = place(tuple(ring.interpolate((s_along + 30.0) % ring.length).coords[0]), min_gap=25.0)
    repair = [site("repair-1", "repair_station", jn, pad, cap=6.0, store=20.0)]

    for s in docks + storage + repair:  # yard spurs join the network at an exact headland vertex
        _insert_vertex(head, s.access)
        layout.tracks.append(Track(id=f"spur-{s.slug}", kind="spur", width=4.0, coords=[s.access, [s.x, s.y]]))
    layout.track_length_m += sum(math.dist(s.access, (s.x, s.y)) for s in docks + storage + repair)

    # travel latency on the tracks (sampled) from each section's nearest work pass to its nearest dock
    net = TrackNetwork([{"coords": t.coords} for t in layout.tracks])
    sample = secs[:: max(1, len(secs) // 120)]
    dists = []
    for sct in sample:
        sx, sy = sct.passes[0]["start"]
        dists.append(min(net.route(sx, sy, d.x, d.y)[1] for d in docks))
    dists = np.array([d for d in dists if math.isfinite(d)] or [0.0])
    return PlanReport(docks=docks, storage=storage, repair=repair, layout=layout, k_docks=k_docks, k_storage=k_storage,
                      inertia=round(km.inertia, 1), iterations=km.iterations, converged=km.converged,
                      mean_travel_m=round(float(dists.mean()), 1), p95_travel_m=round(float(np.percentile(dists, 95)), 1),
                      max_travel_m=round(float(dists.max()), 1))

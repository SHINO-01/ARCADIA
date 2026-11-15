"""Algorithmic infrastructure planning.

1. K-Means++ (numpy) over hex centroids places Renewable Charging Docks (solar/wind microgrids).
2. Robot Storage and the Repair Station are placed at the fleet's travel-latency optimum.
3. A travel-road network is laid along hex rows/columns at a configurable spacing, with spurs so
   every dock, storage and the repair station sit on a road.
4. The remaining cells are grouped into crop beds (strips between roads) that can be repositioned
   by changing orientation, bed width or the rotation seed.

Deterministic given a seed.
"""
from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass, field

import h3
import numpy as np

from app.services.h3_engine import CROP_TYPES, HexCellSpec, HexGrid


@dataclass
class KMeansResult:
    centroids: np.ndarray
    labels: np.ndarray
    inertia: float
    iterations: int
    converged: bool


def kmeans(points: np.ndarray, k: int, seed: int = 42, max_iter: int = 100, tol: float = 1e-4) -> KMeansResult:
    """Lloyd's algorithm with k-means++ seeding. points: (n, 2) metres."""
    n = len(points)
    k = max(1, min(k, n))
    rng = np.random.default_rng(seed)
    centroids = np.empty((k, 2))
    centroids[0] = points[rng.integers(n)]
    d2 = np.sum((points - centroids[0]) ** 2, axis=1)
    for i in range(1, k):
        probs = d2 / d2.sum() if d2.sum() > 0 else np.full(n, 1.0 / n)
        centroids[i] = points[rng.choice(n, p=probs)]
        d2 = np.minimum(d2, np.sum((points - centroids[i]) ** 2, axis=1))
    labels = np.zeros(n, dtype=int)
    converged = False
    it = 0
    for it in range(1, max_iter + 1):
        dists = np.linalg.norm(points[:, None, :] - centroids[None, :, :], axis=2)
        labels = dists.argmin(axis=1)
        new_c = centroids.copy()
        for j in range(k):
            members = points[labels == j]
            if len(members):
                new_c[j] = members.mean(axis=0)
        shift = np.linalg.norm(new_c - centroids, axis=1).max()
        centroids = new_c
        if shift < tol:
            converged = True
            break
    dists = np.linalg.norm(points[:, None, :] - centroids[None, :, :], axis=2)
    return KMeansResult(centroids, labels, float((dists.min(axis=1) ** 2).sum()), it, converged)


@dataclass
class PlannedSite:
    slug: str
    kind: str  # solar_dock | wind_dock | storage | repair_station
    h3: str
    x: float
    y: float
    lat: float
    lng: float
    cluster_size: int
    capacity_kw: float
    storage_kwh: float


@dataclass
class Bed:
    id: str
    crop: str
    cells: list[str]
    centroid_x: float
    centroid_y: float


@dataclass
class PlanReport:
    docks: list[PlannedSite]
    storage: list[PlannedSite]
    repair: list[PlannedSite]
    roads: list[str]
    beds: list[Bed]
    k_docks: int
    k_storage: int
    inertia: float
    iterations: int
    converged: bool
    mean_travel_m: float
    p95_travel_m: float
    max_travel_m: float
    road_spacing: int
    bed_width: int
    orientation: str
    road_cells: int = 0
    crop_cells: int = 0
    params: dict = field(default_factory=dict)

    @property
    def sites(self) -> list[PlannedSite]:
        return self.docks + self.storage + self.repair

    def to_dict(self) -> dict:
        d = asdict(self)
        d["roads"] = len(self.roads)  # keep the report compact; cells carry the kind
        return d


def _snap(grid: HexGrid, xy: np.ndarray, taken: set[str]) -> HexCellSpec:
    best, best_d = None, float("inf")
    for c in grid.cells:
        if c.h3 in taken:
            continue
        d = (c.x - xy[0]) ** 2 + (c.y - xy[1]) ** 2
        if d < best_d:
            best, best_d = c, d
    assert best is not None
    return best


def _ij_axes(grid: HexGrid) -> dict[str, tuple[int, int]]:
    """Local IJ lattice coordinates for every cell (rows/columns for roads and beds)."""
    anchor = grid.cells[0].h3
    out: dict[str, tuple[int, int]] = {}
    for c in grid.cells:
        try:
            out[c.h3] = h3.cell_to_local_ij(anchor, c.h3)
        except Exception:  # pragma: no cover
            out[c.h3] = (int(c.x // grid.edge_m), int(c.y // grid.edge_m))
    return out


def _bfs_path(start: str, goal: str, allowed: set[str], neighbors: dict[str, list[str]]) -> list[str]:
    if start == goal:
        return [start]
    prev = {start: None}
    q = deque([start])
    while q:
        cur = q.popleft()
        for n in neighbors.get(cur, []):
            if n in prev or n not in allowed:
                continue
            prev[n] = cur
            if n == goal:
                path = [n]
                while prev[path[-1]] is not None:
                    path.append(prev[path[-1]])
                return path[::-1]
            q.append(n)
    return []


def plan_infrastructure(grid: HexGrid, k_docks: int | None = None, k_storage: int | None = None, seed: int = 42,
                        road_spacing: int = 6, bed_width: int = 3, orientation: str = "ew",
                        shuffle_crops: bool = False) -> PlanReport:
    cells = grid.cells
    by_h3 = grid.by_index
    pts = np.array([[c.x, c.y] for c in cells])
    n = len(cells)
    if k_docks is None:
        k_docks = int(np.clip(round(n / 110), 1, 8))
    if k_storage is None:
        k_storage = 1 if n < 450 else 2
    road_spacing = max(3, min(20, int(road_spacing)))
    bed_width = max(1, min(road_spacing - 1, int(bed_width)))
    orientation = "ns" if orientation == "ns" else "ew"

    # ---- 1. docks via K-Means++
    km = kmeans(pts, k_docks, seed=seed)
    taken: set[str] = set()
    docks: list[PlannedSite] = []
    for j, c in enumerate(km.centroids):
        cell = _snap(grid, c, taken)
        taken.add(cell.h3)
        size = int((km.labels == j).sum())
        kind = "solar_dock" if j % 2 == 0 else "wind_dock"
        docks.append(PlannedSite(slug=f"dock-{j + 1}", kind=kind, h3=cell.h3, x=cell.x, y=cell.y, lat=cell.lat, lng=cell.lng,
                                 cluster_size=size, capacity_kw=round(8.0 + 0.05 * size, 2), storage_kwh=round(40.0 + 0.2 * size, 1)))

    # ---- 2. storage (+ repair station next to storage-1) at the dock-weighted centre
    dock_pts = np.array([[d.x, d.y] for d in docks])
    weights = np.array([max(1, d.cluster_size) for d in docks], dtype=float)
    storage: list[PlannedSite] = []
    if k_storage == 1 or len(docks) == 1:
        targets = [(dock_pts * weights[:, None]).sum(axis=0) / weights.sum()]
    else:
        rep = np.repeat(dock_pts, np.maximum(1, (weights / weights.min()).astype(int)), axis=0)
        targets = list(kmeans(rep, k_storage, seed=seed + 1).centroids)
    for i, t in enumerate(targets):
        cell = _snap(grid, np.asarray(t), taken)
        taken.add(cell.h3)
        storage.append(PlannedSite(slug=f"storage-{i + 1}", kind="storage", h3=cell.h3, x=cell.x, y=cell.y, lat=cell.lat,
                                   lng=cell.lng, cluster_size=0, capacity_kw=0.0, storage_kwh=0.0))
    s1 = storage[0]
    rcell = _snap(grid, np.array([s1.x + grid.edge_m * 1.8, s1.y]), taken)
    taken.add(rcell.h3)
    repair = [PlannedSite(slug="repair-1", kind="repair_station", h3=rcell.h3, x=rcell.x, y=rcell.y, lat=rcell.lat, lng=rcell.lng,
                          cluster_size=0, capacity_kw=6.0, storage_kwh=20.0)]

    # ---- 3. travel roads along the IJ lattice
    ij = _ij_axes(grid)
    axis = 1 if orientation == "ew" else 0            # road runs along constant j (east-west rows) or constant i
    coords = [ij[c.h3][axis] for c in cells]
    lo = min(coords)
    road: set[str] = set()
    for c in cells:
        if (ij[c.h3][axis] - lo) % road_spacing == 0 and c.h3 not in taken:
            road.add(c.h3)
    # one cross road through the storage so every row is connected
    cross_axis = 1 - axis
    cross_val = ij[s1.h3][cross_axis]
    for c in cells:
        if ij[c.h3][cross_axis] == cross_val and c.h3 not in taken:
            road.add(c.h3)
    # spurs: connect every site to the nearest road cell with a BFS over the grid
    neighbors = {c.h3: c.neighbors for c in cells}
    all_cells = set(by_h3)
    for site in docks + storage + repair:
        if not road:
            break
        nearest = min(road, key=lambda r: (by_h3[r].x - site.x) ** 2 + (by_h3[r].y - site.y) ** 2)
        path = _bfs_path(site.h3, nearest, all_cells - (taken - {site.h3}), neighbors)
        for h in path[1:-1]:
            road.add(h)
    # keep the road network connected: attach stray components to the main one
    comps: list[set[str]] = []
    seen: set[str] = set()
    for r in road:
        if r in seen:
            continue
        comp, q = {r}, deque([r])
        while q:
            cur = q.popleft()
            for nb in neighbors.get(cur, []):
                if nb in road and nb not in comp:
                    comp.add(nb)
                    q.append(nb)
        seen |= comp
        comps.append(comp)
    if len(comps) > 1:
        comps.sort(key=len, reverse=True)
        main = comps[0]
        for comp in comps[1:]:
            a = next(iter(comp))
            b = min(main, key=lambda m: (by_h3[m].x - by_h3[a].x) ** 2 + (by_h3[m].y - by_h3[a].y) ** 2)
            for h in _bfs_path(a, b, all_cells - taken, neighbors):
                road.add(h)
            main |= comp

    # ---- 4. crop beds: strips of `bed_width` lattice lines between roads, labelled A, B, C...
    rng = np.random.default_rng(seed)
    rotation = CROP_TYPES[:]
    if shuffle_crops:
        rng.shuffle(rotation)
    beds: dict[tuple[int, int], Bed] = {}
    crop_cells = 0
    cross_lo = min(ij[c.h3][cross_axis] for c in cells)
    for c in cells:
        if c.h3 in taken or c.h3 in road:
            continue
        strip = (ij[c.h3][axis] - lo) // road_spacing
        band = (ij[c.h3][cross_axis] - cross_lo) // (bed_width * road_spacing)
        key = (strip, band)
        if key not in beds:
            idx = len(beds)
            letter = chr(ord("A") + idx % 26) + (str(idx // 26 + 1) if idx >= 26 else "")
            beds[key] = Bed(id=f"bed-{letter}", crop=rotation[(strip + band) % len(rotation)], cells=[], centroid_x=0.0, centroid_y=0.0)
        beds[key].cells.append(c.h3)
        c.crop_type = beds[key].crop
        crop_cells += 1
    def _centroids() -> None:
        for b in beds.values():
            b.centroid_x = round(float(np.mean([by_h3[h].x for h in b.cells])), 2)
            b.centroid_y = round(float(np.mean([by_h3[h].y for h in b.cells])), 2)

    # Slivers at the field edge are not workable beds: fold any bed smaller than min_cells into
    # the nearest larger bed (it inherits that bed's crop), then re-letter A, B, C... in order.
    _centroids()
    min_cells = max(6, bed_width * 4)
    while len(beds) > 1:
        small = min(beds.items(), key=lambda kv: len(kv[1].cells))
        if len(small[1].cells) >= min_cells:
            break
        key, b = small
        others = [(k, o) for k, o in beds.items() if k != key]
        tgt_key, tgt = min(others, key=lambda kv: (kv[1].centroid_x - b.centroid_x) ** 2 + (kv[1].centroid_y - b.centroid_y) ** 2)
        tgt.cells.extend(b.cells)
        del beds[key]
        _centroids()
    ordered = sorted(beds.values(), key=lambda b: (-b.centroid_y, b.centroid_x))  # north-west first
    for idx, b in enumerate(ordered):
        letter = chr(ord("A") + idx % 26) + (str(idx // 26 + 1) if idx >= 26 else "")
        b.id = f"bed-{letter}"
        for h in b.cells:
            by_h3[h].crop_type = b.crop
    # mark kinds on the specs so the world builder / DB see them
    for c in cells:
        if c.h3 in road:
            c.kind = "road"
    bed_list = sorted(beds.values(), key=lambda b: (len(b.id), b.id))

    dmin = np.linalg.norm(pts[:, None, :] - dock_pts[None, :, :], axis=2).min(axis=1)
    return PlanReport(
        docks=docks, storage=storage, repair=repair, roads=sorted(road), beds=bed_list, k_docks=k_docks, k_storage=k_storage,
        inertia=round(km.inertia, 1), iterations=km.iterations, converged=km.converged,
        mean_travel_m=round(float(dmin.mean()), 1), p95_travel_m=round(float(np.percentile(dmin, 95)), 1),
        max_travel_m=round(float(dmin.max()), 1), road_spacing=road_spacing, bed_width=bed_width, orientation=orientation,
        road_cells=len(road), crop_cells=crop_cells,
        params={"seed": seed, "shuffle_crops": shuffle_crops},
    )

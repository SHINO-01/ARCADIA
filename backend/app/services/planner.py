"""Algorithmic infrastructure planning: K-Means++ (numpy) over hex centroids.

Determines where Renewable Charging Docks (solar/wind microgrids) and Robot Storage
facilities should sit to minimise fleet travel latency. Deterministic given a seed.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np

from app.services.h3_engine import HexCellSpec, HexGrid


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

    # k-means++ initialisation
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
    inertia = float((dists.min(axis=1) ** 2).sum())
    return KMeansResult(centroids, labels, inertia, it, converged)


@dataclass
class PlannedSite:
    slug: str
    kind: str
    h3: str
    x: float
    y: float
    lat: float
    lng: float
    cluster_size: int
    capacity_kw: float
    storage_kwh: float


@dataclass
class PlanReport:
    docks: list[PlannedSite]
    storage: list[PlannedSite]
    k_docks: int
    k_storage: int
    inertia: float
    iterations: int
    converged: bool
    mean_travel_m: float
    p95_travel_m: float
    max_travel_m: float

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


def _snap(grid: HexGrid, xy: np.ndarray, taken: set[str]) -> HexCellSpec:
    """Nearest free hex cell to a continuous centroid."""
    best, best_d = None, float("inf")
    for c in grid.cells:
        if c.h3 in taken:
            continue
        d = (c.x - xy[0]) ** 2 + (c.y - xy[1]) ** 2
        if d < best_d:
            best, best_d = c, d
    assert best is not None
    return best


def plan_infrastructure(grid: HexGrid, k_docks: int | None = None, k_storage: int | None = None,
                        seed: int = 42) -> PlanReport:
    cells = grid.cells
    pts = np.array([[c.x, c.y] for c in cells])
    n = len(cells)
    if k_docks is None:
        k_docks = int(np.clip(round(n / 110), 1, 8))
    if k_storage is None:
        k_storage = 1 if n < 450 else 2

    km = kmeans(pts, k_docks, seed=seed)
    taken: set[str] = set()
    docks: list[PlannedSite] = []
    for j, c in enumerate(km.centroids):
        cell = _snap(grid, c, taken)
        taken.add(cell.h3)
        size = int((km.labels == j).sum())
        # alternate solar / wind microgrids; capacity scales with the cluster it serves
        kind = "solar_dock" if j % 2 == 0 else "wind_dock"
        docks.append(PlannedSite(
            slug=f"dock-{j + 1}", kind=kind, h3=cell.h3, x=cell.x, y=cell.y, lat=cell.lat, lng=cell.lng,
            cluster_size=size, capacity_kw=round(8.0 + 0.05 * size, 2), storage_kwh=round(40.0 + 0.2 * size, 1),
        ))

    # Storage sits where the docks are closest on average: k-means over dock centroids weighted
    # by cluster size, then snapped to a free cell.
    dock_pts = np.array([[d.x, d.y] for d in docks])
    weights = np.array([max(1, d.cluster_size) for d in docks], dtype=float)
    storage: list[PlannedSite] = []
    if k_storage == 1 or len(docks) == 1:
        centre = (dock_pts * weights[:, None]).sum(axis=0) / weights.sum()
        targets = [centre]
    else:
        rep = np.repeat(dock_pts, np.maximum(1, (weights / weights.min()).astype(int)), axis=0)
        targets = list(kmeans(rep, k_storage, seed=seed + 1).centroids)
    for i, t in enumerate(targets):
        cell = _snap(grid, np.asarray(t), taken)
        taken.add(cell.h3)
        storage.append(PlannedSite(
            slug=f"storage-{i + 1}", kind="storage", h3=cell.h3, x=cell.x, y=cell.y, lat=cell.lat, lng=cell.lng,
            cluster_size=0, capacity_kw=0.0, storage_kwh=0.0,
        ))

    # Travel-latency metrics: distance from every crop cell to its nearest dock
    dmin = np.linalg.norm(pts[:, None, :] - dock_pts[None, :, :], axis=2).min(axis=1)
    return PlanReport(
        docks=docks, storage=storage, k_docks=k_docks, k_storage=k_storage,
        inertia=round(km.inertia, 1), iterations=km.iterations, converged=km.converged,
        mean_travel_m=round(float(dmin.mean()), 1), p95_travel_m=round(float(np.percentile(dmin, 95)), 1),
        max_travel_m=round(float(dmin.max()), 1),
    )

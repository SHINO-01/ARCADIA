"""Mission planning helpers: waypoint generation for each mission kind."""
from __future__ import annotations

from app.schemas import Waypoint


def boustrophedon(cells: list[dict], row_height: float, altitude: float = 6.0) -> list[Waypoint]:
    """Lawn-mower ordering over hex centroids: bucket by row (y), alternate sweep direction.
    Produces a smooth, low-turn survey path for the UAV."""
    if not cells:
        return []
    rows: dict[int, list[dict]] = {}
    for c in cells:
        rows.setdefault(int(round(c["y"] / row_height)), []).append(c)
    path: list[Waypoint] = []
    for i, key in enumerate(sorted(rows)):
        row = sorted(rows[key], key=lambda c: c["x"], reverse=(i % 2 == 1))
        for c in row:
            path.append(Waypoint(x=c["x"], y=c["y"], z=altitude, h3=c.get("h3"), lat=c.get("lat"), lng=c.get("lng")))
    return path


def direct(target: dict, z: float = 0.0) -> list[Waypoint]:
    return [Waypoint(x=target["x"], y=target["y"], z=z, h3=target.get("h3"), lat=target.get("lat"), lng=target.get("lng"))]

"""Adaptive controlled-traffic field layout.

Real mechanised farms never drive on the growing area. The field is laid out as:

  * headland  - a turning track just inside the boundary, all the way round;
  * beds      - long rectangular strips split into ~40 m sections (the unit of crop state and work);
  * tramlines - narrow permanent lanes between beds, just wide enough for the widest vehicle;
  * alleys    - cross tracks that break very long beds, and cut tracks between management zones;
  * roads     - existing roads and tracks found in the satellite imagery, reused as farm tracks;
  * spurs     - short tracks from the headland to the farm yard (docks, storage, repair).

Adaptive setup (any shape, any size):

  1. cropable area = field inside the headland, minus existing road corridors and obstacles
     (buildings, water, tree cover) found by the imagery analysis;
  2. it falls apart into zones where roads cross it; concave zones are also tried split along
     cuts from their reflex vertices (each cut becomes a track);
  3. per zone the bed orientation and lateral offset are searched (coarse-to-fine over 0-180
     degrees) to maximise the soil-weighted productive bed area minus the headland turn loss of
     every bed end; beds shorter than the minimum are not planted;
  4. a split is kept only when it beats the unsplit zone by more than 1 %.

The score is evaluated exactly on the geometry: bed centre lines are sampled every metre and
tested against the zone eroded by half a bed width (shapely's vectorised contains_xy), so a bed is
only counted where the full-width rectangle fits.
"""
from __future__ import annotations

import math
import random
from dataclasses import asdict, dataclass, field

import numpy as np
from shapely import affinity, contains_xy
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union



@dataclass
class LayoutParams:
    bed_width: float = 6.0        # m, cropped strip width
    lane_width: float = 2.4       # m, tramline width (AGV body + wheels + margin)
    headland_width: float = 10.0  # m, turning track inside the boundary
    section_length: float = 40.0  # m, management unit along a bed
    alley_spacing: float = 0.0    # m between cross alleys; 0 = auto (only when beds are long)
    alley_width: float = 5.0
    orientation: str = "auto"     # auto (adaptive optimisation) | ew | ns
    shuffle_crops: bool = False   # kept for API compatibility (crops are a farm decision now)
    seed: int = 42
    crop: str = "maize"           # the field's crop this season
    rotation_crop: str = ""       # optional second crop: the field is split into two rotation blocks
    min_bed_length: float = 15.0  # m: shorter beds are not worth planting
    turn_loss_m2: float = 30.0    # m2-equivalent lost per bed end (turning time, overlap, compaction)
    use_roads: bool = True        # reuse detected roads as tracks / zone boundaries
    max_zones: int = 6


@dataclass
class Section:
    id: str
    bed_id: str
    crop: str
    x: float          # centroid (ENU)
    y: float
    length: float
    width: float
    angle: float      # radians, bed axis direction in ENU
    u0: float         # along-bed extent in the zone's rotated frame
    u1: float
    v: float          # bed centre line in the rotated frame
    lanes: list[float] = field(default_factory=list)
    polygon: list[list[float]] = field(default_factory=list)  # ENU ring (closed)
    passes: list[dict] = field(default_factory=list)  # work passes: [{lane, start:[x,y], end:[x,y]}] (ENU)
    zone: int = 0


@dataclass
class Track:
    id: str
    kind: str         # headland | lane | alley | spur | road
    width: float
    coords: list[list[float]]  # ENU polyline


@dataclass
class Layout:
    angle: float
    params: LayoutParams
    sections: list[Section]
    tracks: list[Track]
    beds: list[dict]
    hazards: list[dict]
    field_area_m2: float
    crop_area_m2: float
    track_length_m: float
    zones: list[dict] = field(default_factory=list)
    roads_used: int = 0
    obstacles_excluded: int = 0
    baseline: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "angle": self.angle, "params": asdict(self.params),
            "sections": [asdict(s) for s in self.sections], "tracks": [asdict(t) for t in self.tracks],
            "beds": self.beds, "hazards": self.hazards, "field_area_m2": round(self.field_area_m2, 1),
            "crop_area_m2": round(self.crop_area_m2, 1), "track_length_m": round(self.track_length_m, 1),
            "zones": self.zones,
        }


# ----------------------------------------------------------------------------- geometry helpers
def _polys(g) -> list[Polygon]:
    if g is None or g.is_empty:
        return []
    if g.geom_type == "Polygon":
        return [g]
    if g.geom_type in ("MultiPolygon", "GeometryCollection"):
        return [q for q in g.geoms if q.geom_type == "Polygon" and not q.is_empty]
    return []


def _lines(geom) -> list[LineString]:
    if geom.is_empty:
        return []
    if geom.geom_type == "LineString":
        return [geom]
    if geom.geom_type in ("MultiLineString", "GeometryCollection"):
        return [g for g in geom.geoms if g.geom_type == "LineString" and not g.is_empty]
    return []


def _largest(geom):
    ps = _polys(geom)
    return max(ps, key=lambda g: g.area) if ps else Polygon()


def bed_axis_angle(field_poly: Polygon, orientation: str) -> float:
    """Long axis of the minimum rotated rectangle (the classic rule of thumb), or a fixed EW/NS."""
    if orientation == "ew":
        return 0.0
    if orientation == "ns":
        return math.pi / 2
    rect = field_poly.minimum_rotated_rectangle
    xs, ys = rect.exterior.coords.xy
    best, best_len = 0.0, -1.0
    for i in range(4):
        dx, dy = xs[i + 1] - xs[i], ys[i + 1] - ys[i]
        length = math.hypot(dx, dy)
        if length > best_len:
            best, best_len = math.atan2(dy, dx), length
    return _norm(best)


def _norm(a: float) -> float:
    while a <= -math.pi / 2:
        a += math.pi
    while a > math.pi / 2:
        a -= math.pi
    return a


# ----------------------------------------------------------------------------- the optimiser
class Potential:
    """Relative productivity raster (1 = average) sampled at ENU points; flat when no soil data."""

    def __init__(self, grid: np.ndarray | None = None, origin=(0.0, 0.0), cell: float = 4.0):
        self.grid, self.origin, self.cell = grid, origin, cell

    def __call__(self, x: np.ndarray, y: np.ndarray) -> np.ndarray:
        if self.grid is None:
            return np.ones_like(np.asarray(x, float))
        ny, nx = self.grid.shape
        ix = np.clip(((np.asarray(x) - self.origin[0]) / self.cell).astype(int), 0, nx - 1)
        iy = np.clip(((np.asarray(y) - self.origin[1]) / self.cell).astype(int), 0, ny - 1)
        return self.grid[iy, ix]


def score_zone(zone: Polygon, angle: float, offset: float, p: LayoutParams, pot: Potential, step: float = 1.0) -> tuple[float, int, float]:
    """(score, bed segments, bed area m2) for beds at `angle` with lateral `offset` in `zone`."""
    E = zone.buffer(-p.bed_width / 2, join_style=2)
    if E.is_empty:
        return 0.0, 0, 0.0
    deg = math.degrees(angle)
    Er = affinity.rotate(E, -deg, origin=(0, 0))
    zr = affinity.rotate(zone, -deg, origin=(0, 0))
    umin, vmin, umax, vmax = Er.bounds
    P = p.bed_width + p.lane_width
    first = zr.bounds[1] + p.lane_width + p.bed_width / 2 + offset
    centres = np.arange(first, vmax + 1e-6, P)
    centres = centres[centres >= vmin - 1e-6]
    if len(centres) == 0:
        return 0.0, 0, 0.0
    us = np.arange(umin, umax + step, step)
    U, V = np.meshgrid(us, centres)
    inside = contains_xy(Er, U, V)
    pad = np.pad(inside.astype(np.int8), ((0, 0), (1, 1)))
    d = np.diff(pad, axis=1)
    starts = np.argwhere(d == 1)
    ends = np.argwhere(d == -1)
    if len(starts) == 0:
        return 0.0, 0, 0.0
    lengths = (ends[:, 1] - starts[:, 1]) * step
    keep = lengths >= p.min_bed_length
    if not keep.any():
        return 0.0, 0, 0.0
    c, s = math.cos(angle), math.sin(angle)
    total = 0.0
    for (row, a), (_, b), ok in zip(starts, ends, keep):
        if ok:
            uu = us[a:b]
            vv = np.full_like(uu, centres[row])
            total += float(pot(uu * c - vv * s, uu * s + vv * c).sum()) * step * p.bed_width
    nseg = int(keep.sum())
    return total - 2 * nseg * p.turn_loss_m2, nseg, float(lengths[keep].sum() * p.bed_width)


def best_orientation(zone: Polygon, p: LayoutParams, pot: Potential, coarse_deg: float = 5.0, fine: bool = True) -> dict:
    P = p.bed_width + p.lane_width
    offsets = [0.0, P / 4, P / 2, 3 * P / 4]
    base = bed_axis_angle(zone, "auto")
    best = {"score": -1e18, "angle": base, "offset": 0.0, "segments": 0, "area": 0.0}
    cand = sorted({round(_norm(math.radians(a)), 6) for a in np.arange(0, 180, coarse_deg)} | {round(base, 6), round(_norm(base + math.pi / 2), 6)})
    for a in cand:
        for off in offsets[:2]:
            sc, n, area = score_zone(zone, a, off, p, pot, step=2.0)
            if sc > best["score"]:
                best = {"score": sc, "angle": a, "offset": off, "segments": n, "area": area}
    if fine:
        a0 = best["angle"]
        for da in np.arange(-coarse_deg, coarse_deg + 0.01, 1.0):
            for off in offsets:
                a = _norm(a0 + math.radians(da))
                sc, n, area = score_zone(zone, a, off, p, pot, step=1.0)
                if sc > best["score"]:
                    best = {"score": sc, "angle": a, "offset": off, "segments": n, "area": area}
    return best


def _reflex_cuts(zone: Polygon, max_cuts: int = 12) -> list[LineString]:
    """Cut lines through reflex vertices along the directions of their adjacent edges."""
    poly = zone.simplify(2.0)
    if poly.geom_type != "Polygon":
        return []
    ring = list(poly.exterior.coords)[:-1]
    if not poly.exterior.is_ccw:
        ring = ring[::-1]
    n = len(ring)
    cuts = []
    for i in range(n):
        a, b, c = np.array(ring[i - 1]), np.array(ring[i]), np.array(ring[(i + 1) % n])
        cross = (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])
        if cross >= 0:
            continue  # convex vertex
        for dvec in (b - a, c - b):
            L = float(np.hypot(*dvec))
            if L < 1e-6:
                continue
            u = dvec / L * 1e4
            cuts.append((abs(cross), LineString([b - u, b + u])))
    cuts.sort(key=lambda t: -t[0])
    return [c for _, c in cuts[:max_cuts]]


def optimise_zones(cropable, p: LayoutParams, pot: Potential, fixed_angle: float | None = None) -> tuple[list[dict], list[LineString]]:
    """Zones (polygon + best orientation) and the cut lines that separate split zones."""
    min_area = max(300.0, (p.min_bed_length * 2) * p.bed_width)
    zones = [z for z in _polys(cropable) if z.area >= min_area]

    def solve(z: Polygon, depth: int):
        if fixed_angle is not None:
            best = None
            for off in (0.0, (p.bed_width + p.lane_width) / 2):
                sc, n, area = score_zone(z, fixed_angle, off, p, pot)
                if best is None or sc > best["score"]:
                    best = {"score": sc, "angle": fixed_angle, "offset": off, "segments": n, "area": area}
            return best["score"], [{"poly": z, **best}], []
        b = best_orientation(z, p, pot)
        whole = (b["score"], [{"poly": z, **b}], [])
        convexity = z.area / max(z.convex_hull.area, 1e-6)
        if depth >= 2 or convexity > 0.9 or z.area < 4 * min_area:
            return whole
        best_split = None
        for cut in _reflex_cuts(z):
            corridor = cut.buffer(p.alley_width / 2, cap_style=2)
            parts = [q for q in _polys(z.difference(corridor)) if q.area >= min_area]
            if len(parts) < 2:
                continue
            sc = sum(max(best_orientation(q, p, pot, coarse_deg=10.0, fine=False)["score"], 0.0) for q in parts)
            if sc > whole[0] * 1.01 and (best_split is None or sc > best_split[0]):
                best_split = (sc, parts, cut.intersection(z.buffer(1.0)))
        if best_split is None:
            return whole
        total, zs, cs = 0.0, [], [best_split[2]]
        for q in best_split[1]:
            s_, zq, cq = solve(q, depth + 1)
            total += s_
            zs += zq
            cs += cq
        return (total, zs, cs) if total > whole[0] * 1.01 else whole

    out, cuts_used = [], []
    for z in sorted(zones, key=lambda g: -g.area):
        _, zs, cs = solve(z, 0)
        out += zs
        cuts_used += [c for c in cs if not c.is_empty]
    return out, cuts_used


# ----------------------------------------------------------------------------- generation
def _bed_name(i: int) -> str:
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def _coords(line: LineString) -> list[list[float]]:
    return [[round(x, 3), round(y, 3)] for x, y in line.coords]


def _alleys_for(umin: float, umax: float, p: LayoutParams) -> list[float]:
    spacing = p.alley_spacing if p.alley_spacing > 0 else (160.0 if (umax - umin) > 220.0 else 0.0)
    if spacing <= 0:
        return []
    n = int((umax - umin) // spacing)
    if n < 1:
        return []
    stepu = (umax - umin) / (n + 1)
    return [umin + stepu * (i + 1) for i in range(n)]


def _cut_by_alleys(pieces: list[LineString], alleys_u: list[float], bed_v: float, p: LayoutParams) -> list[LineString]:
    out = []
    for piece in pieces:
        segs = [piece]
        for ua in alleys_u:
            gap = (ua - p.alley_width / 2 - 1.0, ua + p.alley_width / 2 + 1.0)
            nxt = []
            for sgm in segs:
                a, b = sorted([sgm.coords[0][0], sgm.coords[-1][0]])
                if b <= gap[0] or a >= gap[1]:
                    nxt.append(sgm)
                    continue
                if a < gap[0] - 1:
                    nxt.append(LineString([(a, bed_v), (gap[0], bed_v)]))
                if b > gap[1] + 1:
                    nxt.append(LineString([(gap[1], bed_v), (b, bed_v)]))
            segs = nxt
        out.extend(segs)
    return [s for s in out if s.length >= p.min_bed_length]


def generate_layout(field_xy: Polygon, params: LayoutParams, roads: list[dict] | None = None, obstacles: list[dict] | None = None,
                    potential: Potential | None = None) -> Layout:
    """field_xy: farm boundary in ENU metres. roads: [{xy, width_m}] from the imagery; obstacles: [{xy, kind}]."""
    p = params
    pot = potential or Potential()
    ring_poly = _largest(field_xy.buffer(-p.headland_width / 2, join_style=2))
    inner = field_xy.buffer(-p.headland_width, join_style=2)
    if inner.is_empty or ring_poly.is_empty:
        raise ValueError("field is too small for the headland width")

    # existing infrastructure found in the imagery
    road_lines, road_corr = [], []
    near = field_xy.buffer(80)
    for r in (roads or []) if p.use_roads else []:
        if len(r.get("xy", [])) < 2:
            continue
        ln = LineString(r["xy"])
        if ln.length < 15 or not ln.intersects(near):
            continue
        w = float(r.get("width_m", 5.0))
        for piece in _lines(ln.intersection(near)):
            road_lines.append((piece, w))
            road_corr.append(piece.buffer(w / 2 + 1.0, cap_style=2))
    obst = []
    for o in obstacles or []:
        if len(o.get("xy", [])) >= 4:
            q = Polygon(o["xy"])
            if q.is_valid and q.intersects(field_xy):
                obst.append(q.buffer(3.0))
    blocked = unary_union(road_corr + obst) if (road_corr or obst) else Polygon()
    cropable = inner.difference(blocked) if not blocked.is_empty else inner

    fixed = None if p.orientation == "auto" else bed_axis_angle(field_xy, p.orientation)
    zones, cuts = optimise_zones(cropable, p, pot, fixed)
    base_angle = bed_axis_angle(field_xy, "auto")
    base = [score_zone(z, base_angle, 0.0, p, pot) for z in _polys(cropable)]
    base_score, base_segs, base_area = sum(b[0] for b in base), sum(b[1] for b in base), sum(b[2] for b in base)

    rng = random.Random(p.seed)

    sections: list[Section] = []
    beds: list[dict] = []
    tracks: list[Track] = []
    lane_lines: list[LineString] = []
    strips = unary_union([ring_poly.exterior.buffer(p.headland_width / 2 + 0.6)]
                         + [c.buffer(p.alley_width / 2 + 0.6, cap_style=2) for c in cuts]
                         + [ln.buffer(w / 2 + 1.6, cap_style=2) for ln, w in road_lines])
    obst_u = unary_union(obst) if obst else None
    bed_idx = 0
    zone_info = []
    for zi, z in enumerate(zones):
        zp, angle, offset = z["poly"], z["angle"], z["offset"]
        deg = math.degrees(angle)

        def back(g, deg=deg):
            return affinity.rotate(g, deg, origin=(0, 0))

        zr = affinity.rotate(zp, -deg, origin=(0, 0))
        bed_zone = zr.buffer(-p.bed_width / 2, join_style=2)
        if bed_zone.is_empty:
            continue
        umin, vmin, umax, vmax = zr.bounds
        alleys_u = _alleys_for(umin, umax, p)
        P = p.bed_width + p.lane_width
        lanes_v: list[float] = []
        zone_secs = 0
        bed_v = vmin + p.lane_width + p.bed_width / 2 + offset
        while bed_v - p.bed_width / 2 < vmax:
            centre = LineString([(umin - 5, bed_v), (umax + 5, bed_v)])
            cutsg = _cut_by_alleys(_lines(centre.intersection(bed_zone)), alleys_u, bed_v, p)
            if cutsg:
                letter = _bed_name(bed_idx)
                crop = p.crop
                bed_secs = []
                lane_vs = [bed_v - p.bed_width / 2 - p.lane_width / 2, bed_v + p.bed_width / 2 + p.lane_width / 2]
                for piece in sorted(cutsg, key=lambda s: min(s.coords[0][0], s.coords[-1][0])):
                    a, b = sorted([piece.coords[0][0], piece.coords[-1][0]])
                    n = max(1, round((b - a) / p.section_length))
                    stp = (b - a) / n
                    for k in range(n):
                        u0, u1 = a + k * stp, a + (k + 1) * stp
                        rect = Polygon([(u0, bed_v - p.bed_width / 2), (u1, bed_v - p.bed_width / 2),
                                        (u1, bed_v + p.bed_width / 2), (u0, bed_v + p.bed_width / 2)])
                        enu = back(rect)
                        cen = enu.centroid
                        passes = []
                        for lv in lane_vs:
                            pa, pb = back(Point(u0, lv)), back(Point(u1, lv))
                            passes.append({"lane": round(lv, 3), "start": [round(pa.x, 3), round(pa.y, 3)],
                                           "end": [round(pb.x, 3), round(pb.y, 3)]})
                        bed_secs.append(Section(id=f"{letter}-{len(bed_secs) + 1:02d}", bed_id=f"bed-{letter}", crop=crop,
                                                x=round(cen.x, 3), y=round(cen.y, 3), length=round(u1 - u0, 3), width=p.bed_width,
                                                angle=angle, u0=u0, u1=u1, v=bed_v, lanes=lane_vs, passes=passes, zone=zi,
                                                polygon=[[round(x, 3), round(y, 3)] for x, y in enu.exterior.coords]))
                sections.extend(bed_secs)
                zone_secs += len(bed_secs)
                beds.append({"id": f"bed-{letter}", "crop": crop, "sections": len(bed_secs), "zone": zi,
                             "length_m": round(sum(s.length for s in bed_secs), 1)})
                lanes_v.extend(lane_vs)
                bed_idx += 1
            bed_v += P
        # tramlines: out of the zone to the tracks that bound it, never into another zone or an obstacle
        corridor = unary_union([zp, strips]).intersection(field_xy.buffer(-0.5))
        if obst_u is not None:
            corridor = corridor.difference(obst_u)
        for lv in sorted(set(round(v, 3) for v in lanes_v)):
            line = back(LineString([(umin - 400, lv), (umax + 400, lv)]))
            for piece in _lines(line.intersection(corridor)):
                if piece.intersects(zp) and piece.length > 2:
                    lane_lines.append(piece)
        for ua in alleys_u:
            line = back(LineString([(ua, vmin - 400), (ua, vmax + 400)]))
            for piece in _lines(line.intersection(corridor)):
                if piece.intersects(zp):
                    tracks.append(Track(id=f"alley-{zi + 1}-{len(tracks) + 1}", kind="alley", width=p.alley_width, coords=_coords(piece)))
        zone_info.append({"id": zi, "angle_deg": round(math.degrees(angle), 1), "area_m2": round(zp.area, 1), "sections": zone_secs,
                          "score": round(z["score"], 1), "bed_runs": int(z.get("segments", 0))})

    head_line = LineString(ring_poly.exterior.coords)
    head_pieces = [head_line] if obst_u is None else _lines(head_line.difference(obst_u))
    for k, piece in enumerate(reversed(head_pieces)):
        tracks.insert(0, Track(id="headland" if k == len(head_pieces) - 1 else f"headland-{len(head_pieces) - k}", kind="headland",
                               width=p.headland_width, coords=_coords(piece)))
    for k, c in enumerate(cuts):
        for piece in _lines(c.intersection(ring_poly.buffer(0.6))):
            tracks.append(Track(id=f"cut-{k + 1}", kind="alley", width=p.alley_width, coords=_coords(piece)))
    for k, (ln, w) in enumerate(road_lines):
        tracks.append(Track(id=f"road-{k + 1}", kind="road", width=round(w, 1), coords=_coords(ln)))
    for k, ln in enumerate(lane_lines):
        tracks.append(Track(id=f"lane-{k + 1:03d}", kind="lane", width=p.lane_width, coords=_coords(ln)))

    # waterlogging-prone spots on the tracks: the least productive (wettest) ground, else random
    lane_tracks = [t for t in tracks if t.kind in ("lane", "headland")]
    hazards: list[dict] = []
    nhz = min(6, max(2, len(sections) // 90))
    if pot.grid is not None and lane_tracks:
        pts = []
        for t in lane_tracks:
            ls = LineString(t.coords)
            for f in np.linspace(0.05, 0.95, 12):
                q = ls.interpolate(f, normalized=True)
                pts.append((float(pot(np.array([q.x]), np.array([q.y]))[0]), q, t.id))
        pts.sort(key=lambda r: r[0])
        for _, q, tid in pts:
            if len(hazards) >= nhz:
                break
            if all(math.hypot(q.x - h["x"], q.y - h["y"]) > 40 for h in hazards):
                hazards.append({"id": f"wet-{len(hazards) + 1}", "x": round(q.x, 2), "y": round(q.y, 2), "r": 3.5, "track": tid})
    elif lane_tracks:
        for k in range(nhz):
            t = rng.choice(lane_tracks)
            q = LineString(t.coords).interpolate(rng.uniform(0.15, 0.85), normalized=True)
            hazards.append({"id": f"wet-{k + 1}", "x": round(q.x, 2), "y": round(q.y, 2), "r": 3.5, "track": t.id})

    if p.rotation_crop and p.rotation_crop != p.crop and beds:
        # two rotation blocks of contiguous beds with about equal area
        half, acc, second = sum(b["length_m"] for b in beds) / 2, 0.0, set()
        for b in beds:
            if acc >= half:
                second.add(b["id"])
                b["crop"] = p.rotation_crop
            acc += b["length_m"]
        for s in sections:
            if s.bed_id in second:
                s.crop = p.rotation_crop
    crop_area = sum(s.length * s.width for s in sections)
    track_len = sum(LineString(t.coords).length for t in tracks)
    main_angle = zones[0]["angle"] if zones else base_angle
    return Layout(angle=main_angle, params=p, sections=sections, tracks=tracks, beds=beds, hazards=hazards,
                  field_area_m2=field_xy.area, crop_area_m2=crop_area, track_length_m=track_len, zones=zone_info,
                  roads_used=len(road_lines), obstacles_excluded=len(obst),
                  baseline={"angle_deg": round(math.degrees(base_angle), 1), "bed_area_m2": round(base_area, 1),
                            "bed_runs": int(base_segs), "score": round(base_score, 1),
                            "optimised_score": round(sum(z["score"] for z in zones), 1)})


def is_on_crop(sections: list[Section], x: float, y: float) -> bool:
    pt = Point(x, y)
    return any(Polygon(s.polygon).contains(pt) for s in sections)


__all__ = ["Layout", "LayoutParams", "Potential", "Section", "Track", "generate_layout", "is_on_crop", "score_zone",
           "best_orientation", "optimise_zones"]

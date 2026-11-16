"""Controlled-traffic field layout.

Real mechanised farms never drive on the growing area. The field is laid out as:

  * **headland** - a turning track just inside the boundary, all the way round;
  * **beds** - long rectangular strips along the field's long axis; each bed is split into
    **sections** (~40 m) which are the unit of crop state, observation and work;
  * **tramlines** - narrow permanent dirt lanes between beds, just wide enough for the widest
    vehicle; machines work a bed section by driving the tramline beside it, tool reaching in;
  * **alleys** - optional cross tracks that break very long beds so vehicles can change lanes
    without driving to the far headland;
  * **spurs** - short tracks from the headland to the farm yard (docks, storage, repair).

Everything is generated in local ENU metres. Geometry is built in a rotated frame (u along the
beds, v across them) and rotated back, using shapely for clipping.
"""
from __future__ import annotations

import math
import random
from dataclasses import asdict, dataclass, field

from shapely import affinity
from shapely.geometry import LineString, Point, Polygon

from app.services.field import CROP_TYPES


@dataclass
class LayoutParams:
    bed_width: float = 6.0        # m, cropped strip width
    lane_width: float = 2.4       # m, tramline width (AGV body + wheels + margin)
    headland_width: float = 10.0  # m, turning track inside the boundary
    section_length: float = 40.0  # m, management unit along a bed
    alley_spacing: float = 0.0    # m between cross alleys; 0 = auto (only when beds are long)
    alley_width: float = 5.0
    orientation: str = "auto"     # auto | ew | ns
    shuffle_crops: bool = False
    seed: int = 42


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
    u0: float         # along-bed extent in the rotated frame
    u1: float
    v: float          # bed centre line in the rotated frame
    lanes: list[float] = field(default_factory=list)  # v of the tramlines either side
    polygon: list[list[float]] = field(default_factory=list)  # ENU ring (closed)
    passes: list[dict] = field(default_factory=list)  # work passes: [{lane, start:[x,y], end:[x,y]}] (ENU)


@dataclass
class Track:
    id: str
    kind: str         # headland | lane | alley | spur
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
    origin_rot: tuple[float, float] = (0.0, 0.0)

    def to_dict(self) -> dict:
        return {
            "angle": self.angle, "params": asdict(self.params),
            "sections": [asdict(s) for s in self.sections], "tracks": [asdict(t) for t in self.tracks],
            "beds": self.beds, "hazards": self.hazards, "field_area_m2": round(self.field_area_m2, 1),
            "crop_area_m2": round(self.crop_area_m2, 1), "track_length_m": round(self.track_length_m, 1),
        }


def _largest(geom):
    if geom.is_empty:
        return geom
    if geom.geom_type == "MultiPolygon":
        return max(geom.geoms, key=lambda g: g.area)
    return geom


def _lines(geom) -> list[LineString]:
    if geom.is_empty:
        return []
    if geom.geom_type == "LineString":
        return [geom]
    if geom.geom_type in ("MultiLineString", "GeometryCollection"):
        return [g for g in geom.geoms if g.geom_type == "LineString" and not g.is_empty]
    return []


def bed_axis_angle(field_poly: Polygon, orientation: str) -> float:
    """Direction of the beds (radians in ENU). auto = along the longest side of the minimum
    rotated rectangle, which minimises headland turns."""
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
    # normalise to (-pi/2, pi/2] so "east-ish" beds are not flipped
    while best <= -math.pi / 2:
        best += math.pi
    while best > math.pi / 2:
        best -= math.pi
    return best


def generate_layout(field_xy: Polygon, params: LayoutParams) -> Layout:
    """field_xy: the farm boundary in local ENU metres."""
    p = params
    angle = bed_axis_angle(field_xy, p.orientation)
    deg = math.degrees(angle)
    c = field_xy.centroid
    # rotated frame: beds run along +u
    fld = affinity.rotate(field_xy, -deg, origin=c)
    back = lambda g: affinity.rotate(g, deg, origin=c)  # noqa: E731

    ring_poly = _largest(fld.buffer(-p.headland_width / 2, join_style=2))
    inner = _largest(fld.buffer(-p.headland_width, join_style=2))
    if inner.is_empty or ring_poly.is_empty:
        raise ValueError("field is too small for the headland width")
    # beds are rectangles: keep their centre lines inside the inner area eroded by half a bed
    bed_zone = inner.buffer(-p.bed_width / 2, join_style=2)
    # lanes run out to (and slightly past) the headland centre line so the network is connected
    lane_zone = ring_poly.buffer(0.6, join_style=2)
    umin, vmin, umax, vmax = inner.bounds

    # optional cross alleys
    spacing = p.alley_spacing
    if spacing <= 0:
        spacing = 160.0 if (umax - umin) > 220.0 else 0.0
    alleys_u: list[float] = []
    if spacing > 0:
        n = int((umax - umin) // spacing)
        if n >= 1:
            step = (umax - umin) / (n + 1)
            alleys_u = [umin + step * (i + 1) for i in range(n)]

    rng = random.Random(p.seed)
    rotation = CROP_TYPES[:]
    if p.shuffle_crops:
        rng.shuffle(rotation)

    sections: list[Section] = []
    beds: list[dict] = []
    lanes_v: list[float] = []
    v = vmin + p.lane_width / 2  # first tramline hugs the inner edge
    bed_idx = 0
    while v < vmax:
        lanes_v.append(v)
        bed_v = v + p.lane_width / 2 + p.bed_width / 2
        if bed_v + p.bed_width / 2 > vmax:
            break
        centre = LineString([(umin - 5, bed_v), (umax + 5, bed_v)])
        pieces = _lines(centre.intersection(bed_zone))
        # cut by alleys
        cuts = []
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
            cuts.extend(segs)
        cuts = [s for s in cuts if s.length >= max(8.0, p.section_length * 0.25)]
        if cuts:
            letter = _bed_name(bed_idx)
            crop = rotation[bed_idx % len(rotation)]
            bed_secs = []
            for piece in sorted(cuts, key=lambda s: min(s.coords[0][0], s.coords[-1][0])):
                a, b = sorted([piece.coords[0][0], piece.coords[-1][0]])
                n = max(1, round((b - a) / p.section_length))
                step = (b - a) / n
                for k in range(n):
                    u0, u1 = a + k * step, a + (k + 1) * step
                    rect = Polygon([(u0, bed_v - p.bed_width / 2), (u1, bed_v - p.bed_width / 2),
                                    (u1, bed_v + p.bed_width / 2), (u0, bed_v + p.bed_width / 2)])
                    enu = back(rect)
                    cen = enu.centroid
                    sid = f"{letter}-{len(bed_secs) + 1:02d}"
                    lane_vs = [bed_v - p.bed_width / 2 - p.lane_width / 2, bed_v + p.bed_width / 2 + p.lane_width / 2]
                    passes = []
                    for lv in lane_vs:
                        pa, pb = back(Point(u0, lv)), back(Point(u1, lv))
                        passes.append({"lane": round(lv, 3), "start": [round(pa.x, 3), round(pa.y, 3)],
                                       "end": [round(pb.x, 3), round(pb.y, 3)]})
                    sec = Section(id=sid, bed_id=f"bed-{letter}", crop=crop, x=round(cen.x, 3), y=round(cen.y, 3),
                                  length=round(u1 - u0, 3), width=p.bed_width, angle=angle, u0=u0, u1=u1, v=bed_v,
                                  lanes=lane_vs, passes=passes,
                                  polygon=[[round(x, 3), round(y, 3)] for x, y in enu.exterior.coords])
                    bed_secs.append(sec)
            sections.extend(bed_secs)
            beds.append({"id": f"bed-{letter}", "crop": crop, "sections": len(bed_secs),
                         "length_m": round(sum(s.length for s in bed_secs), 1)})
            bed_idx += 1
        v = bed_v + p.bed_width / 2 + p.lane_width / 2

    # tracks (in the rotated frame, then rotated back)
    tracks: list[Track] = []
    ring = ring_poly.exterior
    tracks.append(Track(id="headland", kind="headland", width=p.headland_width,
                        coords=_coords(back(LineString(ring.coords)))))
    used_lanes = set()
    for s in sections:
        for lv in s.lanes:
            used_lanes.add(round(lv, 3))
    for i, lv in enumerate(sorted(used_lanes)):
        line = LineString([(umin - 50, lv), (umax + 50, lv)])
        for j, piece in enumerate(_lines(line.intersection(lane_zone))):
            tracks.append(Track(id=f"lane-{i + 1:03d}{'' if j == 0 else chr(97 + j)}", kind="lane", width=p.lane_width,
                                coords=_coords(back(piece))))
    for i, ua in enumerate(alleys_u):
        line = LineString([(ua, vmin - 50), (ua, vmax + 50)])
        for piece in _lines(line.intersection(lane_zone)):
            tracks.append(Track(id=f"alley-{i + 1}", kind="alley", width=p.alley_width, coords=_coords(back(piece))))

    # low spots that get waterlogged after irrigation: hazards on the lanes / headland
    lane_tracks = [t for t in tracks if t.kind in ("lane", "headland")]
    hazards = []
    for k in range(min(6, max(2, len(sections) // 90))):
        t = rng.choice(lane_tracks)
        ls = LineString(t.coords)
        pt = ls.interpolate(rng.uniform(0.15, 0.85), normalized=True)
        hazards.append({"id": f"wet-{k + 1}", "x": round(pt.x, 2), "y": round(pt.y, 2), "r": 3.5, "track": t.id})

    crop_area = sum(s.length * s.width for s in sections)
    track_len = sum(LineString(t.coords).length for t in tracks)
    return Layout(angle=angle, params=p, sections=sections, tracks=tracks, beds=beds, hazards=hazards,
                  field_area_m2=field_xy.area, crop_area_m2=crop_area, track_length_m=track_len)


def _coords(line: LineString) -> list[list[float]]:
    return [[round(x, 3), round(y, 3)] for x, y in line.coords]


def _bed_name(i: int) -> str:
    """A..Z, AA..AZ, BA.. - readable bed names."""
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def is_on_crop(sections: list[Section], x: float, y: float) -> bool:
    pt = Point(x, y)
    return any(Polygon(s.polygon).contains(pt) for s in sections)


__all__ = ["Layout", "LayoutParams", "Section", "Track", "generate_layout", "is_on_crop"]

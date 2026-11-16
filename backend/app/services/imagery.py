"""Digital image processing on satellite imagery: what already exists on the farm.

Pipeline (numpy + scipy.ndimage only - no ML, no OpenCV):

  1. acquisition    Esri World Imagery tiles over the boundary (+60 m), mosaicked at the zoom whose
                    ground sample distance is ~0.5 m (<= 2048 px a side), disk-cached
  2. features       Gaussian denoise; HSV saturation/value, luminance, excess-green index
                    ExG = 2g - r - b on chromatic coordinates, local standard deviation (texture)
  3. road evidence  colour likelihood of paved (grey, unsaturated) and dirt (brown) surfaces that are
                    smooth, times a multi-scale Hessian ridge measure (bright elongated structures of
                    3-14 m width; scale-normalised eigenvalues, Frangi-style anisotropy test)
  4. morphology     hysteresis threshold, closing to bridge gaps, opening, small-object removal,
                    elongation test (skeleton length^2 / area) per connected component
  5. vectorisation  Zhang-Suen thinning -> skeleton graph -> traced polylines between junctions and
                    ends -> spur pruning -> Douglas-Peucker; width from the Euclidean distance transform
  6. other classes  buildings (bright/unsaturated compact blobs, rectangularity >= 0.6),
                    water (dark smooth or blue-green), tree cover (green, dark, textured)

Everything is georeferenced back to WGS84 and local ENU metres for the planner.
"""
from __future__ import annotations

import base64
import io
import math
import os
from pathlib import Path

import numpy as np
from scipy import ndimage as ndi

TILE_URL = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
CACHE = Path(os.environ.get("ARCADIA_TILE_CACHE", "/tmp/arcadia_tiles"))
TILE = 256


# ----------------------------------------------------------------------------- web mercator
def lonlat_to_px(lon: float, lat: float, z: int) -> tuple[float, float]:
    n = TILE * 2 ** z
    x = (lon + 180.0) / 360.0 * n
    s = math.sin(math.radians(lat))
    y = (0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * n
    return x, y


def px_to_lonlat(x, y, z: int):
    n = TILE * 2 ** z
    lon = np.asarray(x) / n * 360.0 - 180.0
    lat = np.degrees(np.arctan(np.sinh(np.pi * (1 - 2 * np.asarray(y) / n))))
    return lon, lat


def m_per_px(lat: float, z: int) -> float:
    return 156543.03392 * math.cos(math.radians(lat)) / 2 ** z


def fetch_mosaic(bbox: tuple[float, float, float, float], lat: float, max_px: int = 2048, timeout: float = 20.0):
    """RGB mosaic covering bbox (minlon, minlat, maxlon, maxlat). Returns (img uint8, x0, y0, z)."""
    import httpx
    from PIL import Image

    minlon, minlat, maxlon, maxlat = bbox
    z = 18
    while z > 14:
        x0, y0 = lonlat_to_px(minlon, maxlat, z)
        x1, y1 = lonlat_to_px(maxlon, minlat, z)
        if max(x1 - x0, y1 - y0) <= max_px:
            break
        z -= 1
    x0, y0 = lonlat_to_px(minlon, maxlat, z)
    x1, y1 = lonlat_to_px(maxlon, minlat, z)
    tx0, ty0, tx1, ty1 = int(x0 // TILE), int(y0 // TILE), int(x1 // TILE), int(y1 // TILE)
    W, H = (tx1 - tx0 + 1) * TILE, (ty1 - ty0 + 1) * TILE
    mosaic = np.zeros((H, W, 3), np.uint8)
    CACHE.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=timeout, headers={"User-Agent": "ARCADIA farm planner"}) as client:
        for ty in range(ty0, ty1 + 1):
            for tx in range(tx0, tx1 + 1):
                f = CACHE / f"{z}_{tx}_{ty}.jpg"
                if not f.exists():
                    r = client.get(TILE_URL.format(z=z, x=tx, y=ty))
                    r.raise_for_status()
                    f.write_bytes(r.content)
                im = np.asarray(Image.open(f).convert("RGB"))
                mosaic[(ty - ty0) * TILE:(ty - ty0 + 1) * TILE, (tx - tx0) * TILE:(tx - tx0 + 1) * TILE] = im
    # crop to the bbox
    cx0, cy0 = int(x0 - tx0 * TILE), int(y0 - ty0 * TILE)
    cx1, cy1 = int(math.ceil(x1 - tx0 * TILE)), int(math.ceil(y1 - ty0 * TILE))
    return mosaic[cy0:cy1, cx0:cx1], tx0 * TILE + cx0, ty0 * TILE + cy0, z


# ----------------------------------------------------------------------------- image operators
def features(img: np.ndarray) -> dict:
    f = ndi.gaussian_filter(img.astype(np.float32) / 255.0, sigma=(1, 1, 0))
    r, g, b = f[..., 0], f[..., 1], f[..., 2]
    mx, mn = f.max(axis=2), f.min(axis=2)
    sat = np.where(mx > 1e-3, (mx - mn) / np.maximum(mx, 1e-3), 0)
    lum = 0.299 * r + 0.587 * g + 0.114 * b
    tot = r + g + b + 1e-3
    exg = 2 * g / tot - r / tot - b / tot
    m1 = ndi.uniform_filter(lum, 7)
    m2 = ndi.uniform_filter(lum * lum, 7)
    tex = np.sqrt(np.clip(m2 - m1 * m1, 0, None))
    return {"r": r, "g": g, "b": b, "sat": sat, "lum": lum, "exg": exg, "tex": tex}


def ridge(prob: np.ndarray, sigmas=(3.0, 5.0, 7.5)) -> np.ndarray:
    """Multi-scale bright-ridge strength (Hessian eigenvalues, scale-normalised, anisotropic)."""
    best = np.zeros_like(prob)
    for s in sigmas:
        hxx = ndi.gaussian_filter(prob, s, order=(0, 2))
        hyy = ndi.gaussian_filter(prob, s, order=(2, 0))
        hxy = ndi.gaussian_filter(prob, s, order=(1, 1))
        tmp = np.sqrt((hxx - hyy) ** 2 + 4 * hxy ** 2)
        l1 = (hxx + hyy + tmp) / 2   # larger eigenvalue
        l2 = (hxx + hyy - tmp) / 2   # smaller (most negative across a bright ridge)
        rb = np.abs(l1) / (np.abs(l2) + 1e-6)
        v = np.where(l2 < 0, (s * s) * (-l2) * np.exp(-(rb ** 2) / (2 * 0.5 ** 2)), 0.0)
        best = np.maximum(best, v)
    return best


def zhang_suen(mask: np.ndarray) -> np.ndarray:
    """Zhang-Suen (1984) thinning to a one-pixel-wide 8-connected skeleton."""
    img = np.pad(mask.astype(np.uint8), 1)
    while True:
        changed = False
        for step in (0, 1):
            P2, P3, P4 = img[:-2, 1:-1], img[:-2, 2:], img[1:-1, 2:]
            P5, P6, P7 = img[2:, 2:], img[2:, 1:-1], img[2:, :-2]
            P8, P9 = img[1:-1, :-2], img[:-2, :-2]
            seq = [P2, P3, P4, P5, P6, P7, P8, P9, P2]
            B = sum(seq[:8])
            A = sum(((seq[k] == 0) & (seq[k + 1] == 1)).astype(np.uint8) for k in range(8))
            c = img[1:-1, 1:-1]
            if step == 0:
                m1, m2 = P2 * P4 * P6, P4 * P6 * P8
            else:
                m1, m2 = P2 * P4 * P8, P2 * P6 * P8
            kill = (c == 1) & (B >= 2) & (B <= 6) & (A == 1) & (m1 == 0) & (m2 == 0)
            if kill.any():
                c[kill] = 0
                changed = True
        if not changed:
            return img[1:-1, 1:-1].astype(bool)


_N8 = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]


def trace_skeleton(skel: np.ndarray, min_len_px: float, spur_px: float) -> list[list[tuple[int, int]]]:
    """Skeleton pixels -> polylines between junctions/ends (spurs shorter than spur_px dropped)."""
    pts = set(zip(*np.nonzero(skel)))

    def m_adjacent(p):
        """m-adjacency: a diagonal link counts only when no 4-neighbour already joins the two pixels
        (otherwise every staircase step on a diagonal skeleton looks like a junction)."""
        out = []
        for dy, dx in _N8:
            q = (p[0] + dy, p[1] + dx)
            if q not in pts:
                continue
            if dy and dx and ((p[0] + dy, p[1]) in pts or (p[0], p[1] + dx) in pts):
                continue
            out.append(q)
        return out

    nb = {p: m_adjacent(p) for p in pts}
    nodes = {p for p, n in nb.items() if len(n) != 2}
    seen_edges: set[tuple] = set()
    paths = []

    def walk(a, b):
        path, prev, cur = [a, b], a, b
        while cur not in nodes:
            nxt = [q for q in nb[cur] if q != prev and (cur, q) not in seen_edges]
            if not nxt:
                break
            prev, cur = cur, nxt[0]
            seen_edges.add((prev, cur))
            seen_edges.add((cur, prev))
            path.append(cur)
            if cur == a:
                break
        return path

    for n in nodes:
        for q in nb[n]:
            if (n, q) in seen_edges:
                continue
            seen_edges.add((n, q))
            seen_edges.add((q, n))
            paths.append(walk(n, q))
    for p in pts:  # closed loops without a node
        for q in nb[p]:
            if (p, q) not in seen_edges:
                seen_edges.add((p, q))
                seen_edges.add((q, p))
                paths.append(walk(p, q))
    out = []
    deg = {p: len(n) for p, n in nb.items()}
    for path in paths:
        L = sum(math.dist(path[i], path[i + 1]) for i in range(len(path) - 1))
        dangling = deg.get(path[0], 0) == 1 or deg.get(path[-1], 0) == 1
        if L < min_len_px or (dangling and L < spur_px and not (deg.get(path[0], 0) == 1 and deg.get(path[-1], 0) == 1)):
            continue
        out.append(path)
    return out


def _components(mask: np.ndarray, min_px: int):
    lab, n = ndi.label(mask, structure=np.ones((3, 3)))
    if n == 0:
        return lab, []
    sizes = ndi.sum(np.ones_like(lab), lab, index=np.arange(1, n + 1))
    keep = [i + 1 for i, s in enumerate(sizes) if s >= min_px]
    return lab, keep


# ----------------------------------------------------------------------------- analysis
def analyse_image(img: np.ndarray, gsd: float) -> dict:
    """Classify an RGB image (ground sample distance gsd m/px). Pixel-space results."""
    F = features(img)
    sat, lum, exg, tex, r, g, b = F["sat"], F["lum"], F["exg"], F["tex"], F["r"], F["g"], F["b"]
    smooth = np.clip(1 - (tex - 0.025) / 0.08, 0, 1)  # cars and lane markings add some texture to real roads
    # asphalt is dark grey (luminance ~0.2-0.35), concrete and gravel lighter; both unsaturated
    paved = np.clip(1 - sat / 0.2, 0, 1) * np.clip((lum - 0.15) / 0.08, 0, 1) * np.clip((0.88 - lum) / 0.1, 0, 1)
    brown = (r > g) & (g > b)
    dirt = brown * np.clip((0.58 - sat) / 0.18, 0, 1) * np.clip((lum - 0.3) / 0.1, 0, 1) * np.clip((0.82 - lum) / 0.1, 0, 1)
    veg = np.clip((exg - 0.02) / 0.06, 0, 1)
    road_like = np.maximum(paved, 0.85 * dirt) * smooth * (1 - veg)
    road_like = ndi.gaussian_filter(road_like, 1.0)
    # ridge widths: 3-14 m road -> sigma ~ w / (2 sqrt 3) px
    sig = tuple(max(1.5, w / gsd / 3.46) for w in (4.0, 7.0, 12.0))
    rid = ridge(road_like, sig)
    rid = rid / (np.percentile(rid, 99.5) + 1e-6)
    score = road_like * np.clip(rid, 0, 1.5)
    hi, lo = 0.35, 0.18
    strong = score > hi
    weak = score > lo
    lab, n = ndi.label(weak, structure=np.ones((3, 3)))
    keep = np.unique(lab[strong])
    road = np.isin(lab, keep[keep > 0])
    road = ndi.binary_closing(road, structure=np.ones((5, 5)), iterations=2)
    road = ndi.binary_opening(road, structure=np.ones((3, 3)))
    holes = ndi.binary_fill_holes(road) & ~road        # fill only small holes (cars, markings),
    hl, hn = ndi.label(holes)                           # never the block a ring road encloses
    if hn:
        hs = ndi.sum(np.ones_like(hl), hl, index=np.arange(1, hn + 1))
        small = np.isin(hl, 1 + np.flatnonzero(hs < 60 / (gsd * gsd)))
        road = road | small
    min_px = int(40 / (gsd * gsd))  # >= 40 m2
    lab, comps = _components(road, min_px)
    keep_mask = np.zeros_like(road)
    for cid in comps:
        comp = lab == cid
        sk = zhang_suen(comp)
        L = sk.sum()
        if L * L / comp.sum() >= 10 and L * gsd >= 25:  # elongated and at least 25 m long
            keep_mask |= comp
    road = keep_mask
    skel = zhang_suen(road)
    dist = ndi.distance_transform_edt(road)
    paths = trace_skeleton(skel, min_len_px=20 / gsd, spur_px=14 / gsd)

    # buildings: bright / unsaturated compact blobs with strong edges
    edges = np.hypot(ndi.sobel(lum, 0), ndi.sobel(lum, 1))
    edge_d = ndi.uniform_filter(edges, 9)
    bright = ((lum > 0.6) & (sat < 0.25)) | ((lum > 0.45) & (sat < 0.12) & (edge_d > 0.25))
    bmask = ndi.binary_opening(bright & ~road, structure=np.ones((3, 3)), iterations=1)
    blab, bcomps = _components(bmask, int(25 / (gsd * gsd)))
    buildings = []
    from shapely.geometry import MultiPoint
    for cid in bcomps:
        ys, xs = np.nonzero(blab == cid)
        area = len(ys) * gsd * gsd
        if area > 5000:
            continue
        rect = MultiPoint(np.column_stack([xs, ys])).minimum_rotated_rectangle
        if rect.area <= 0 or len(ys) / rect.area < 0.6:
            continue
        buildings.append({"kind": "building", "px": [list(map(float, c)) for c in rect.exterior.coords], "area_m2": round(area, 1)})

    water_m = ((lum < 0.2) & (tex < 0.03) & (exg < 0.08) & (b >= r * 0.9)) | ((b > r + 0.08) & (g > r) & (sat > 0.25) & (lum > 0.3))
    water_m = ndi.binary_opening(water_m, structure=np.ones((3, 3)), iterations=2)
    trees_m = (exg > 0.05) & (lum < 0.3) & (tex > 0.018) & ~water_m
    trees_m = ndi.binary_closing(ndi.binary_opening(trees_m, structure=np.ones((3, 3))), structure=np.ones((5, 5)))
    blobs = []
    for kind, m, min_area in (("water", water_m, 60.0), ("trees", trees_m, 120.0)):
        lab2, comps2 = _components(m, int(min_area / (gsd * gsd)))
        for cid in comps2:
            blobs.append({"kind": kind, "mask_id": cid, "lab": lab2})

    roads = []
    for p in paths:
        arr = np.array(p, float)  # (y, x)
        w = float(np.median(dist[arr[:, 0].astype(int), arr[:, 1].astype(int)])) * 2 * gsd
        conf = float(np.mean(score[arr[:, 0].astype(int), arr[:, 1].astype(int)]))
        roads.append({"px": [[float(x), float(y)] for y, x in p], "width_m": round(max(2.5, min(w, 20.0)), 1),
                      "confidence": round(min(1.0, conf), 2)})
    classes = np.zeros(lum.shape, np.uint8)
    classes[veg > 0.5] = 1
    classes[trees_m] = 2
    classes[water_m] = 3
    classes[bmask] = 4
    classes[road] = 5
    return {"roads": roads, "buildings": buildings, "blobs": blobs, "classes": classes, "road_mask": road,
            "veg_cover": float((veg > 0.5).mean())}


def _blob_polygon(lab: np.ndarray, cid: int, step: int = 4):
    """Coarse polygon of a labelled component: union of `step`-pixel cells, simplified."""
    from shapely.geometry import box
    from shapely.ops import unary_union
    m = lab == cid
    ys, xs = np.nonzero(m[::step, ::step])
    if len(ys) == 0:
        return None
    poly = unary_union([box(x * step, y * step, (x + 1) * step, (y + 1) * step) for y, x in zip(ys, xs)])
    return poly.simplify(step * 0.75)


def preview_png(img: np.ndarray, classes: np.ndarray, max_px: int = 900) -> str:
    from PIL import Image
    pal = np.array([[0, 0, 0, 0], [80, 200, 90, 70], [20, 110, 40, 150], [40, 120, 230, 170], [250, 130, 60, 190], [255, 225, 70, 230]], np.uint8)
    over = pal[classes]
    base = img.astype(np.float32) * 0.65
    a = over[..., 3:4].astype(np.float32) / 255
    rgb = (base * (1 - a) + over[..., :3] * a).astype(np.uint8)
    im = Image.fromarray(rgb)
    s = min(1.0, max_px / max(im.size))
    if s < 1:
        im = im.resize((int(im.width * s), int(im.height * s)), Image.Resampling.BILINEAR)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=82)
    return base64.b64encode(buf.getvalue()).decode()


def analyse_boundary(boundary: dict, lat: float, lon: float, margin_m: float = 60.0) -> dict:
    """Fetch imagery around a GeoJSON boundary and return georeferenced detections."""
    from shapely.geometry import LineString, Polygon

    from app.services.geo import Origin, polygon_from_geojson
    poly = polygon_from_geojson(boundary)
    minlon, minlat, maxlon, maxlat = poly.bounds
    dlat = margin_m / 111_320
    dlon = margin_m / (111_320 * math.cos(math.radians(lat)))
    img, x0, y0, z = fetch_mosaic((minlon - dlon, minlat - dlat, maxlon + dlon, maxlat + dlat), lat)
    gsd = m_per_px(lat, z)
    res = analyse_image(img, gsd)
    org = Origin(lat, lon)
    field_xy = Polygon([org.to_xy(la, lo) for lo, la in poly.exterior.coords])

    def to_ll(pxs):
        a = np.asarray(pxs, float)
        lo, la = px_to_lonlat(a[:, 0] + x0, a[:, 1] + y0, z)
        return [[round(float(u), 7), round(float(v), 7)] for u, v in zip(lo, la)]

    def to_xy(ll):
        return [list(org.to_xy(la, lo)) for lo, la in ll]

    roads = []
    for rd in res["roads"]:
        ll = to_ll(rd["px"])
        line = LineString(to_xy(ll)).simplify(1.5)
        if line.length < 20:
            continue
        near = line.distance(field_xy)
        roads.append({"lnglat": ll, "xy": [[round(x, 2), round(y, 2)] for x, y in line.coords], "width_m": rd["width_m"],
                      "length_m": round(line.length, 1), "confidence": rd["confidence"],
                      "inside": bool(line.intersects(field_xy.buffer(-2))), "distance_m": round(near, 1)})
    obstacles = []
    for bd in res["buildings"]:
        ll = to_ll(bd["px"])
        p = Polygon(to_xy(ll))
        if p.distance(field_xy) > margin_m:
            continue
        obstacles.append({"kind": "building", "lnglat": ll, "xy": [[round(x, 2), round(y, 2)] for x, y in p.exterior.coords],
                          "area_m2": bd["area_m2"], "inside": bool(p.intersects(field_xy))})
    for bl in res["blobs"]:
        pp = _blob_polygon(bl["lab"], bl["mask_id"])
        if pp is None or pp.is_empty or pp.geom_type != "Polygon":
            continue
        ll = to_ll(list(pp.exterior.coords))
        p = Polygon(to_xy(ll))
        if not p.is_valid or p.distance(field_xy) > margin_m:
            continue
        obstacles.append({"kind": bl["kind"], "lnglat": ll, "xy": [[round(x, 2), round(y, 2)] for x, y in p.exterior.coords],
                          "area_m2": round(p.area, 1), "inside": bool(p.intersects(field_xy))})
    H, W = res["classes"].shape
    corners = to_ll([[0, 0], [W, 0], [W, H], [0, H]])
    return {"source": "Esri World Imagery", "zoom": z, "gsd_m": round(gsd, 3), "size_px": [W, H],
            "roads": roads, "obstacles": obstacles, "veg_cover_pct": round(100 * res["veg_cover"], 1),
            "preview_jpg": preview_png(img, res["classes"]), "preview_corners": corners}

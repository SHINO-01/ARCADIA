"""Digital image processing on a synthetic aerial scene with known ground truth."""
import math

import numpy as np
from shapely.geometry import LineString, Point, Polygon

from app.services.imagery import analyse_image, zhang_suen


def _scene(seed=0):
    rng = np.random.default_rng(seed)
    H = W = 700
    img = np.zeros((H, W, 3), float)
    # crop field: green with row texture and noise
    yy, xx = np.mgrid[0:H, 0:W]
    img[..., 0] = 0.30 + 0.03 * np.sin(xx / 3.0)
    img[..., 1] = 0.48 + 0.04 * np.sin(xx / 3.0)
    img[..., 2] = 0.22
    img += rng.normal(0, 0.025, img.shape)
    # paved road: diagonal, 16 px (~8 m) wide, grey
    d = np.abs((yy - 0.55 * xx - 80) / math.hypot(1, 0.55))
    img[d < 8] = [0.52, 0.52, 0.53]
    # dirt track: horizontal at y=520, 10 px wide, light brown
    img[(np.abs(yy - 520) < 5) & (xx > 60) & (xx < 680)] = [0.62, 0.52, 0.40]
    # building: bright rectangle
    img[100:150, 520:600] = [0.86, 0.86, 0.84]
    # pond: dark blue disc
    img[(yy - 600) ** 2 + (xx - 150) ** 2 < 45 ** 2] = [0.10, 0.16, 0.20]
    # trees: dark textured green blob
    tm = (yy - 300) ** 2 / 50 ** 2 + (xx - 560) ** 2 / 70 ** 2 < 1
    img[tm] = np.stack([0.10 + 0.08 * rng.random(tm.sum()), 0.24 + 0.14 * rng.random(tm.sum()), 0.08 + 0.05 * rng.random(tm.sum())], 1)
    img += rng.normal(0, 0.01, img.shape)
    return (np.clip(img, 0, 1) * 255).astype(np.uint8)


def test_detects_roads_buildings_water_trees():
    res = analyse_image(_scene(), gsd=0.5)
    lines = [LineString(r["px"]) for r in res["roads"]]
    assert lines, "no roads found"
    diag = LineString([(0, 80), (700, 80 + 0.55 * 700)])
    track = LineString([(60, 520), (680, 520)])
    # every true road is covered along most of its length by a detected centreline
    for truth, name in ((diag, "paved"), (track, "dirt")):
        covered = sum(l.intersection(truth.buffer(10)).length for l in lines)
        assert covered > 0.6 * truth.intersection(Polygon([(0, 0), (700, 0), (700, 700), (0, 700)])).length * 0.8, name
    # no long false road in the middle of the crop
    crop_probe = Polygon([(300, 30), (480, 30), (480, 110), (300, 110)])  # crop only, clear of every road
    assert sum(l.intersection(crop_probe).length for l in lines) < 20
    assert any(Polygon(b["px"]).contains(Point(560, 125)) for b in res["buildings"]), "building missed"
    kinds = {b["kind"] for b in res["blobs"]}
    assert "water" in kinds and "trees" in kinds
    assert all(r["width_m"] >= 2.5 for r in res["roads"])


def test_zhang_suen_thins_a_bar_to_a_line():
    m = np.zeros((40, 120), bool)
    m[15:26, 10:110] = True
    sk = zhang_suen(m)
    cols = sk[:, 20:100].sum(axis=0)
    assert (cols == 1).mean() > 0.95

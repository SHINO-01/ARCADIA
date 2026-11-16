#!/usr/bin/env python3
"""Create a demo farm end to end through the API (stdlib only):
boundary -> site analysis -> adaptive layout -> recommended crop -> activation."""
import json
import os
import sys
import time
import urllib.request

BASE = os.environ.get("ARCADIA_API", "http://localhost:8080/api")
DEMO = {
    "name": "Demo Farm - San Joaquin Valley",
    "seed": 42,
    "boundary": {"type": "Feature", "properties": {}, "geometry": {"type": "Polygon", "coordinates": [[
        [-119.8000, 36.7000], [-119.7965, 36.7000], [-119.7960, 36.7014], [-119.7975, 36.7026],
        [-119.8000, 36.7022], [-119.8000, 36.7000]]]}},
}


def call(method, path, body=None, timeout=300):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"null")


def say(msg):
    print(f"    {msg}", flush=True)


farm = call("POST", "/farms", DEMO)
fid = farm["id"]
say(f"farm {farm['name']}: {farm['area_m2'] / 10000:.1f} ha")
call("POST", f"/farms/{fid}/analyze")
for _ in range(200):
    st = (call("GET", f"/farms/{fid}/analysis").get("status") or {})
    if st.get("done"):
        break
    time.sleep(3)
a = call("GET", f"/farms/{fid}/analysis")
say(f"site: {len((a.get('imagery') or {}).get('roads', []))} roads detected, {(a.get('soil') or {}).get('texture')} soil, "
    f"{(a.get('climate') or {}).get('annual_rain_mm')} mm/yr ({(a.get('climate') or {}).get('source')})")
farm = call("POST", f"/farms/{fid}/plan", {})
L = farm["plan"]["layout"]
say(f"layout: {L['sections']} sections in {len(L.get('zones', []))} zone(s), {L['cropped_pct']}% cropped")
crops = call("GET", f"/farms/{fid}/crops")
best = next((c for c in crops if c["recommended"]), crops[0])
say(f"crop: {best['name']} (median {best['yield_t_ha']['p50']} t/ha, margin ${best['margin_usd_ha']['p50']:.0f}/ha)")
call("POST", f"/farms/{fid}/crop", {"crop": best["crop"]})
farm = call("POST", f"/farms/{fid}/activate")
say(f"activated: {farm['status']}")
sys.exit(0)

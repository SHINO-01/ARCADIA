#!/usr/bin/env python3
"""Create, plan and activate a demo farm (no dependencies beyond the stdlib)."""
import json
import os
import sys
import urllib.request

BASE = os.environ.get("ARCADIA_API", "http://localhost:8080/api")
DEMO = {
    "name": "Demo Farm - San Joaquin Valley",
    "seed": 42,
    "boundary": {"type": "Feature", "properties": {}, "geometry": {"type": "Polygon", "coordinates": [[
        [-119.8000, 36.7000], [-119.7965, 36.7000], [-119.7960, 36.7014], [-119.7975, 36.7026],
        [-119.8000, 36.7022], [-119.8000, 36.7000]]]}},
}


def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read() or b"null")


farm = call("POST", "/farms", DEMO)
print(f"farm {farm['id']}  cells={farm['cell_count']} res={farm['h3_resolution']} area={farm['area_m2']:.0f} m2")
farm = call("POST", f"/farms/{farm['id']}/plan", {})
p = farm["plan"]
print(f"planned: {p['k_docks']} docks, {p['k_storage']} storage, mean travel {p['mean_travel_m']} m")
farm = call("POST", f"/farms/{farm['id']}/activate")
print(f"activated farm {farm['id']} -> status {farm['status']}")
print(json.dumps(call("GET", "/system/status")["bridge"], indent=1))
sys.exit(0)

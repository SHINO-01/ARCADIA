"""Locate the Webots installation (snap or tarball) and make its Python controller API importable."""
from __future__ import annotations

import os
import sys

# Order matters: an explicit WEBOTS_HOME, then user-space/tarball installs, the snap last
# (the snap cannot open an Xwayland display on Ubuntu 24.04+/26.04 - see install_webots_tarball.sh).
CANDIDATES = [
    os.environ.get("WEBOTS_HOME"),
    os.path.expanduser("~/opt/webots"),
    os.path.expanduser("~/webots"),
    "/usr/local/webots",
    "/opt/webots",
    "/snap/webots/current/usr/share/webots",
]


def webots_home() -> str:
    for c in CANDIDATES:
        if c and os.path.isdir(os.path.join(c, "lib", "controller")):
            os.environ["WEBOTS_HOME"] = c
            return c
    raise RuntimeError("Webots not found; set WEBOTS_HOME")


def setup_controller_path() -> str:
    home = webots_home()
    py = os.path.join(home, "lib", "controller", "python")
    if py not in sys.path:
        sys.path.insert(0, py)
    lib = os.path.join(home, "lib", "controller")
    ld = os.environ.get("LD_LIBRARY_PATH", "")
    if lib not in ld.split(":"):
        os.environ["LD_LIBRARY_PATH"] = f"{lib}:{ld}" if ld else lib
    return home


def webots_binary() -> str:
    home = webots_home()
    if home.startswith("/snap/"):
        return "/snap/bin/webots"
    return os.path.join(home, "webots")

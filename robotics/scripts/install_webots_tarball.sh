#!/usr/bin/env bash
# ARCADIA — user-space Webots install (no root, no snap confinement).
#
# Why not the snap? The webots snap ships without the x11/wayland interfaces and runs with a
# private /tmp, so on a Wayland desktop (Ubuntu 24.04+/26.04 GNOME) it cannot open the Xwayland
# display at all ("Gtk-WARNING: cannot open display"). Cyberbotics also recommends the tarball
# for ROS 2 work because extern controllers need direct access to lib/controller.
#
# usage: bash robotics/scripts/install_webots_tarball.sh [install-dir]   (default: ~/opt/webots)
set -euo pipefail
VERSION="${WEBOTS_VERSION:-R2025a}"
DEST="${1:-$HOME/opt/webots}"
TARBALL="${WEBOTS_TARBALL:-}"   # optional: path to an already-downloaded tarball
URL="https://github.com/cyberbotics/webots/releases/download/${VERSION}/webots-${VERSION}-x86-64.tar.bz2"

mkdir -p "$(dirname "$DEST")"
if [[ -z "$TARBALL" ]]; then
  TARBALL="$(mktemp -d)/webots-${VERSION}-x86-64.tar.bz2"
  echo "==> Downloading Webots ${VERSION} (~350 MB)"
  curl -fL --progress-bar -o "$TARBALL" "$URL"
fi
echo "==> Extracting to ${DEST}"
rm -rf "${DEST}.tmp" && mkdir -p "${DEST}.tmp"
tar -xjf "$TARBALL" -C "${DEST}.tmp" --strip-components=1
rm -rf "$DEST" && mv "${DEST}.tmp" "$DEST"

# shell hook (idempotent)
HOOK="export WEBOTS_HOME=\"$DEST\"   # ARCADIA"
grep -qF 'WEBOTS_HOME="'"$DEST" ~/.bashrc 2>/dev/null || echo "$HOOK" >> ~/.bashrc
echo
echo "Webots ${VERSION} installed at ${DEST}"
echo "  export WEBOTS_HOME=${DEST}      (added to ~/.bashrc)"
echo "Then rebuild the ROS 2 workspace so the supervisor links against it:  make robotics-build"

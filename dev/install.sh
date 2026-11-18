#!/bin/sh
# Build bitmap2component (static on Linux) and install the extension for
# the current user, by symlinks into Inkscape's user extensions folder
# (the Flatpak's if installed). --uninstall removes it.
#
# Copyright 2026 David
# SPDX-License-Identifier: GPL-3.0-or-later
set -eu
repo=$(cd "$(dirname "$0")/.." && pwd)
if [ -n "${INKSCAPE_EXTENSIONS_DIR:-}" ]; then
    dir=$INKSCAPE_EXTENSIONS_DIR
elif flatpak info org.inkscape.Inkscape >/dev/null 2>&1; then
    dir=$HOME/.var/app/org.inkscape.Inkscape/config/inkscape/extensions
else
    dir=${XDG_CONFIG_HOME:-$HOME/.config}/inkscape/extensions
fi
if [ "${1:-}" = --uninstall ]; then
    rm -rf "$dir/svg2shenzhen" "$dir"/svg2shenzhen_*.inx
    echo "removed svg2shenzhen from $dir"
    exit 0
fi
cd "$repo"
make -j"$(nproc)" CXXFLAGS="-std=c++11 -O2" LIBS="-static" >/dev/null
cp bitmap2component inkscape/svg2shenzhen/bitmap2component_linux64
mkdir -p "$dir"
ln -sfn "$repo/inkscape/svg2shenzhen" "$dir/svg2shenzhen"
for f in "$repo"/inkscape/svg2shenzhen_*.inx; do
    ln -sf "$f" "$dir/$(basename "$f")"
done
echo "installed into $dir (restart Inkscape): Extensions > Export Kicad / Prepare Document"

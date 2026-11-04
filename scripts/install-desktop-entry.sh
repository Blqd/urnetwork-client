#!/usr/bin/env bash
# Install the URnetwork VPN desktop entry and its icon from this repo.
#
# The repo copy gui/urnetwork-vpn.desktop is the single source of truth; the
# copies in ~/.local/share/applications and on the Desktop are generated from it
# so they cannot drift apart again.
#
#   scripts/install-desktop-entry.sh             # application menu entry only
#   scripts/install-desktop-entry.sh --desktop   # also place a trusted icon on
#                                                # the XDG Desktop folder
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SRC_DESKTOP="$PROJECT_DIR/gui/urnetwork-vpn.desktop"
SRC_ICON="$PROJECT_DIR/gui/urnetwork-vpn.svg"
ICON_NAME="urnetwork-vpn"

APPS_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICONS_ROOT="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor"

WANT_DESKTOP=0
[ "${1:-}" = "--desktop" ] && WANT_DESKTOP=1

[ -f "$SRC_DESKTOP" ] || { echo "missing $SRC_DESKTOP" >&2; exit 1; }
[ -f "$SRC_ICON" ] || { echo "missing $SRC_ICON" >&2; exit 1; }

if command -v desktop-file-validate >/dev/null 2>&1; then
    if ! desktop-file-validate "$SRC_DESKTOP"; then
        echo "warning: $SRC_DESKTOP does not fully validate" >&2
    fi
fi

chmod +x "$PROJECT_DIR/scripts/urnetwork-gui-launch.sh" 2>/dev/null || true

# --- icon ---------------------------------------------------------------
install -d "$ICONS_ROOT/scalable/apps"
install -m 644 "$SRC_ICON" "$ICONS_ROOT/scalable/apps/$ICON_NAME.svg"

render_png() {  # $1 = size, $2 = destination
    local size="$1" dest="$2"
    if command -v rsvg-convert >/dev/null 2>&1; then
        rsvg-convert -w "$size" -h "$size" -o "$dest" "$SRC_ICON" && return 0
    elif command -v inkscape >/dev/null 2>&1; then
        inkscape "$SRC_ICON" --export-type=png --export-filename="$dest" \
            --export-width="$size" --export-height="$size" >/dev/null 2>&1 && return 0
    fi
    # Fall back to librsvg via python3-gi.
    local py=""
    for candidate in /usr/bin/python3 python3; do
        if command -v "$candidate" >/dev/null 2>&1 && \
           "$candidate" -c 'import gi; gi.require_version("Rsvg", "2.0")' >/dev/null 2>&1; then
            py="$candidate"; break
        fi
    done
    [ -n "$py" ] || return 1
    "$py" -W ignore::DeprecationWarning - "$SRC_ICON" "$size" "$dest" <<'PY'
import sys
import gi
gi.require_version("Rsvg", "2.0")
from gi.repository import Rsvg
import cairo
src, size, dest = sys.argv[1], int(sys.argv[2]), sys.argv[3]
handle = Rsvg.Handle.new_from_file(src)
surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
ctx = cairo.Context(surface)
ctx.scale(size / handle.get_dimensions().width, size / handle.get_dimensions().height)
handle.render_cairo(ctx)
surface.write_to_png(dest)
PY
}

# Hybrid icons drop sizes the theme already covers, so only ship what is needed.
for size in 16 24 32 48 64 128 256; do
    dir="$ICONS_ROOT/${size}x${size}/apps"
    install -d "$dir"
    if ! render_png "$size" "$dir/$ICON_NAME.png"; then
        echo "warning: could not render ${size}x${size} PNG (SVG is installed)" >&2
    fi
done

command -v gtk-update-icon-cache >/dev/null 2>&1 && \
    gtk-update-icon-cache -f -t "$ICONS_ROOT" >/dev/null 2>&1 || true

# --- desktop entry ------------------------------------------------------
install -d "$APPS_DIR"
install -m 755 "$SRC_DESKTOP" "$APPS_DIR/$ICON_NAME.desktop"

command -v update-desktop-database >/dev/null 2>&1 && \
    update-desktop-database "$APPS_DIR" >/dev/null 2>&1 || true

echo "installed: $APPS_DIR/$ICON_NAME.desktop"
echo "installed: $ICONS_ROOT/{scalable,16x16,...,256x256}/apps/$ICON_NAME.{svg,png}"

# --- optional desktop shortcut -----------------------------------------
if [ "$WANT_DESKTOP" -eq 1 ]; then
    DESKTOP_DIR="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"
    if [ -d "$DESKTOP_DIR" ]; then
        cp -f "$SRC_DESKTOP" "$DESKTOP_DIR/$ICON_NAME.desktop"
        chmod 755 "$DESKTOP_DIR/$ICON_NAME.desktop"
        # Nautilus refuses to launch an entry that is not marked trusted.
        gio set "$DESKTOP_DIR/$ICON_NAME.desktop" metadata::trusted true 2>/dev/null || true
        echo "installed: $DESKTOP_DIR/$ICON_NAME.desktop"
    else
        echo "warning: desktop directory not found: $DESKTOP_DIR" >&2
    fi
fi

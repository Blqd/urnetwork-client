#!/usr/bin/env bash
#
# Build a Debian package for urnetwork-client.
#
# Usage:
#   scripts/build-deb.sh              # version 0.1.0-1
#   VERSION=0.2.0-1 scripts/build-deb.sh
#
# The package is self-contained (binaries + GUI + helpers) and installs under
# /usr/lib/urnetwork; /usr/bin wrappers point at that tree.  Debian package
# files are not touched outside the staging directory, so no root is needed.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR"

PACKAGE="urnetwork-client"
VERSION="${VERSION:-0.1.0-1}"
ARCH="${ARCH:-$(dpkg --print-architecture)}"
LIBDIR="/usr/lib/urnetwork"
DIST_DIR="$PROJECT_DIR/dist"
MAINTAINER="${MAINTAINER:-$(git log -1 --format='%an <%ae>' 2>/dev/null || echo 'Blqd <blqd@users.noreply.github.com>')}"

if [[ ! "$VERSION" =~ ^[0-9] ]]; then
    echo "error: VERSION must start with a digit (got '$VERSION')" >&2
    exit 1
fi
if ! command -v dpkg-deb >/dev/null 2>&1; then
    echo "error: dpkg-deb is required to build a .deb" >&2
    exit 1
fi
for bin in build/urnetwork-vpn build/socksproxy; do
    if [[ ! -x "$bin" ]]; then
        echo "error: $bin is missing; run 'make build' first" >&2
        exit 1
    fi
done

WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT
STAGE="$WORKDIR/${PACKAGE}_${VERSION}_${ARCH}"
mkdir -p "$STAGE"

install_dir() { install -d -m 0755 "$@"; }
install_file() { install -m "$1" "$2" "$3"; }

install_dir \
    "$STAGE$LIBDIR/build" \
    "$STAGE$LIBDIR/gui" \
    "$STAGE$LIBDIR/scripts" \
    "$STAGE/usr/bin" \
    "$STAGE/usr/share/applications" \
    "$STAGE/usr/share/icons/hicolor/scalable/apps" \
    "$STAGE/usr/share/doc/$PACKAGE" \
    "$STAGE/usr/share/man/man1" \
    "$STAGE/usr/share/lintian/overrides" \
    "$STAGE/DEBIAN"

# --- binaries and application files -------------------------------------
install_file 0755 build/urnetwork-vpn "$STAGE$LIBDIR/build/urnetwork-vpn"
install_file 0755 build/socksproxy "$STAGE$LIBDIR/build/socksproxy"
install_file 0644 gui/urnetwork-gui.py "$STAGE$LIBDIR/gui/urnetwork-gui.py"
for script in \
    urnetwork-connect.sh \
    urnetwork-askpass.sh \
    urnetwork-credentials.py \
    urnetwork-gui-launch.sh; do
    install_file 0755 "scripts/$script" "$STAGE$LIBDIR/scripts/$script"
done

# --- command line entry points ------------------------------------------
cat > "$STAGE/usr/bin/urnetwork-gui" <<'EOF'
#!/bin/sh
exec /usr/lib/urnetwork/scripts/urnetwork-gui-launch.sh "$@"
EOF
cat > "$STAGE/usr/bin/urnetwork-connect" <<'EOF'
#!/bin/sh
exec /usr/lib/urnetwork/scripts/urnetwork-connect.sh "$@"
EOF
chmod 0755 "$STAGE/usr/bin/urnetwork-gui" "$STAGE/usr/bin/urnetwork-connect"
ln -s "../lib/urnetwork/build/urnetwork-vpn" "$STAGE/usr/bin/urnetwork-vpn"

# --- desktop entry and icon ---------------------------------------------
sed "s|@PROJECT_DIR@|$LIBDIR|g" gui/urnetwork-vpn.desktop.in \
    > "$STAGE/usr/share/applications/urnetwork-vpn.desktop"
chmod 0644 "$STAGE/usr/share/applications/urnetwork-vpn.desktop"
install_file 0644 gui/urnetwork-vpn.svg \
    "$STAGE/usr/share/icons/hicolor/scalable/apps/urnetwork-vpn.svg"

# --- documentation -------------------------------------------------------
{
    echo "$PACKAGE ($VERSION) unstable; urgency=low"
    echo
    echo "  * Package built from $(git rev-parse --short HEAD 2>/dev/null || echo 'unknown') on $(date -u +%Y-%m-%d)."
    echo "  * P2P VPN client: SOCKS5/TUN modes, GTK3 GUI, keyring credentials."
    echo
    echo " -- $MAINTAINER  $(date -u -R)"
} | gzip -9n > "$STAGE/usr/share/doc/$PACKAGE/changelog.Debian.gz"
chmod 0644 "$STAGE/usr/share/doc/$PACKAGE/changelog.Debian.gz"

cat > "$STAGE/usr/share/doc/$PACKAGE/copyright" <<EOF
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: urnetwork-client
Source: https://github.com/Blqd/urnetwork-client

Files: *
Copyright: $(date +%Y) Blqd
License: MPL-2.0
 This program is free software; you can redistribute it and/or modify it
 under the terms of the Mozilla Public License, version 2.0.  The full
 license text is distributed with the source as LICENSE, and is available
 at https://www.mozilla.org/en-US/MPL/2.0/.

Files: lib/*
Copyright: URnetwork upstream authors
License: MPL-2.0
 Vendored upstream libraries; see THIRD_PARTY.md in the source distribution.
EOF
chmod 0644 "$STAGE/usr/share/doc/$PACKAGE/copyright"

# --- manual pages --------------------------------------------------------
for manual in man/man1/*.1; do
    gzip -9n -c "$manual" > "$STAGE/usr/share/man/man1/$(basename "$manual").gz"
    chmod 0644 "$STAGE/usr/share/man/man1/$(basename "$manual").gz"
done

# Packaging metadata warnings that do not indicate a problem for a
# first upload from an upstream repository.
cat > "$STAGE/usr/share/lintian/overrides/$PACKAGE" <<'EOF'
# First upload of an upstream package; no Debian bug report exists yet.
urnetwork-client: initial-upload-closes-no-bugs
EOF
chmod 0644 "$STAGE/usr/share/lintian/overrides/$PACKAGE"

# --- package metadata ----------------------------------------------------
INSTALLED_SIZE="$(du -sk "$STAGE" | cut -f1)"

cat > "$STAGE/DEBIAN/control" <<EOF
Package: $PACKAGE
Version: $VERSION
Architecture: $ARCH
Maintainer: $MAINTAINER
Installed-Size: $INSTALLED_SIZE
Section: net
Priority: optional
Homepage: https://github.com/Blqd/urnetwork-client
Depends: python3 (>= 3.8), python3-gi, gir1.2-gtk-3.0, gir1.2-secret-1, sudo, iproute2, curl, libc6 (>= 2.34)
Recommends: gnome-keyring, x-terminal-emulator, desktop-file-utils, libnotify-bin, zenity
Description: peer-to-peer VPN client with a GTK3 interface
 URnetwork client for Ubuntu: a Go networking core (SOCKS5 no-root mode and
 TUN system-wide VPN mode) plus a GTK3 GUI. Credentials are kept in the
 desktop secret store; the TUN engine runs from a root-owned launcher, and a
 sudo askpass helper opens its own terminal window when a password is needed.
EOF
chmod 0644 "$STAGE/DEBIAN/control"

cat > "$STAGE/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database -q /usr/share/applications || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -q -t -f /usr/share/icons/hicolor || true
fi

exit 0
EOF

cat > "$STAGE/DEBIAN/postrm" <<'EOF'
#!/bin/sh
set -e

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database -q /usr/share/applications || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -q -t -f /usr/share/icons/hicolor || true
fi

exit 0
EOF
chmod 0755 "$STAGE/DEBIAN/postinst" "$STAGE/DEBIAN/postrm"

mkdir -p "$DIST_DIR"
DEB="$DIST_DIR/${PACKAGE}_${VERSION}_${ARCH}.deb"
dpkg-deb --root-owner-group -Zxz --build "$STAGE" "$DEB" >/dev/null

echo "built: $DEB ($(du -h "$DEB" | cut -f1))"
echo
dpkg-deb --info "$DEB" | sed -n '1,25p'
echo
echo "installed files:"
dpkg-deb --contents "$DEB" | awk '{print $6}' | sed 's|^\./|/|' | grep -v '/$' | sort

if command -v lintian >/dev/null 2>&1; then
    echo
    echo "lintian:"
    lintian --no-tag-display-limit "$DEB" 2>&1 | sed 's/^/  /' || true
fi

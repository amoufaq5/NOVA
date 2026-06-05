#!/usr/bin/env bash
# scripts/build-deb.sh
#
# Build a Debian package (.deb) for nova from the pre-built bin/nova
# binary. Uses plain `dpkg-deb --build` (no debuild / dh_make required)
# so it works from any host with dpkg-deb installed (Linux + Termux +
# WSL; not macOS unless you `brew install dpkg`).
#
# Usage:
#   make package-deb           # via the Makefile wrapper, recommended
#   scripts/build-deb.sh       # direct invocation
#
# Output:
#   dist/nova_<version>_amd64.deb
#
# The package metadata is sourced from packaging/debian/{control,
# changelog, copyright, postinst}. The version comes from VERSION.
#
# Exit codes:
#   0 = success
#   2 = pre-flight failure (missing dpkg-deb, missing bin/nova)
#   3 = build failure

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
DIST_DIR="$ROOT_DIR/dist"
STAGE_DIR="$DIST_DIR/deb-stage"

info() { printf 'build-deb: %s\n' "$*"; }
warn() { printf 'build-deb: WARN: %s\n' "$*" >&2; }
die()  { printf 'build-deb: ERROR: %s\n' "$*" >&2; exit "${2:-3}"; }

# ----------------------------------------------------------- preflight --
if ! command -v dpkg-deb >/dev/null 2>&1; then
    cat >&2 <<'EOF'
build-deb: dpkg-deb not found. Install on Debian/Ubuntu:
    sudo apt-get install dpkg-dev
On macOS:
    brew install dpkg
Falling back to documentation-only mode.
EOF
    # Emit a recipe file so CI can at least show what would have happened.
    mkdir -p "$DIST_DIR"
    cat > "$DIST_DIR/nova_deb_recipe.txt" <<'EOF'
nova .deb build recipe (no dpkg-deb available)
==============================================

To build the .deb on a Debian/Ubuntu host:

  1. sudo apt-get install dpkg-dev binutils
  2. scripts/build-deb.sh
  3. The artifact lands at dist/nova_<version>_amd64.deb
  4. Test with: dpkg-deb --info dist/nova_*_amd64.deb
                dpkg-deb --contents dist/nova_*_amd64.deb
                sudo dpkg -i dist/nova_*_amd64.deb

Package layout that gets baked in:
  /usr/local/bin/nova                        the compiler binary
  /usr/share/man/man1/nova.1                 manual page
  /usr/share/doc/nova/README.md
  /usr/share/doc/nova/INSTALL.md
  /usr/share/doc/nova/examples/*.nova        curated examples
  /DEBIAN/control                            metadata
  /DEBIAN/postinst                           runs mandb + prints hint
EOF
    info "wrote recipe: $DIST_DIR/nova_deb_recipe.txt"
    exit 2
fi

[ -f "$ROOT_DIR/bin/nova" ] || die "bin/nova missing — run 'make bin/nova' first" 2
[ -f "$ROOT_DIR/packaging/debian/control" ] || die "packaging/debian/control missing" 2

# ----------------------------------------------------------- version --
VERSION="$(tr -d ' \t\r\n' < "$ROOT_DIR/VERSION")"
[ -n "$VERSION" ] || die "VERSION file is empty" 2
ARCH="amd64"
PKG_NAME="nova_${VERSION}_${ARCH}"
info "version=$VERSION arch=$ARCH name=$PKG_NAME"

# ----------------------------------------------------------- stage --
rm -rf "$STAGE_DIR"
mkdir -p "$STAGE_DIR/$PKG_NAME/DEBIAN"
mkdir -p "$STAGE_DIR/$PKG_NAME/usr/local/bin"
mkdir -p "$STAGE_DIR/$PKG_NAME/usr/share/man/man1"
mkdir -p "$STAGE_DIR/$PKG_NAME/usr/share/doc/nova/examples"

# Binary — strip if `strip` is available; harmless if not.
install -m 0755 "$ROOT_DIR/bin/nova" "$STAGE_DIR/$PKG_NAME/usr/local/bin/nova"
if command -v strip >/dev/null 2>&1; then
    strip --strip-unneeded "$STAGE_DIR/$PKG_NAME/usr/local/bin/nova" 2>/dev/null || true
fi

# Man page — gzip per Debian Policy 12.3.
gzip -9 -n -c "$ROOT_DIR/packaging/man/nova.1" \
    > "$STAGE_DIR/$PKG_NAME/usr/share/man/man1/nova.1.gz"
chmod 0644 "$STAGE_DIR/$PKG_NAME/usr/share/man/man1/nova.1.gz"

# Docs.
install -m 0644 "$ROOT_DIR/README.md"  "$STAGE_DIR/$PKG_NAME/usr/share/doc/nova/" 2>/dev/null || true
install -m 0644 "$ROOT_DIR/INSTALL.md" "$STAGE_DIR/$PKG_NAME/usr/share/doc/nova/" 2>/dev/null || true
install -m 0644 "$ROOT_DIR/packaging/debian/copyright" \
    "$STAGE_DIR/$PKG_NAME/usr/share/doc/nova/copyright"

# changelog.Debian.gz — Debian Policy 12.7.
gzip -9 -n -c "$ROOT_DIR/packaging/debian/changelog" \
    > "$STAGE_DIR/$PKG_NAME/usr/share/doc/nova/changelog.Debian.gz"
chmod 0644 "$STAGE_DIR/$PKG_NAME/usr/share/doc/nova/changelog.Debian.gz"

# Examples — small curated set.
for ex in hello.nova hello_macos.nova hello_wasm.nova basic_mind.nova; do
    if [ -f "$ROOT_DIR/examples/$ex" ]; then
        install -m 0644 "$ROOT_DIR/examples/$ex" \
            "$STAGE_DIR/$PKG_NAME/usr/share/doc/nova/examples/$ex"
    fi
done

# DEBIAN/control — substitute size after computing it.
INSTALL_SIZE_KB="$(du -sk "$STAGE_DIR/$PKG_NAME" | awk '{print $1}')"
sed -e "s/^Version:.*/Version: ${VERSION}/" \
    -e "/^Description:/i Installed-Size: ${INSTALL_SIZE_KB}" \
    "$ROOT_DIR/packaging/debian/control" \
    > "$STAGE_DIR/$PKG_NAME/DEBIAN/control"

# DEBIAN/postinst (runs mandb + prints hint).
install -m 0755 "$ROOT_DIR/packaging/debian/postinst" \
    "$STAGE_DIR/$PKG_NAME/DEBIAN/postinst"

# DEBIAN/conffiles is empty -- no /etc files shipped.

# ----------------------------------------------------------- build --
mkdir -p "$DIST_DIR"
DEB_PATH="$DIST_DIR/${PKG_NAME}.deb"
info "running dpkg-deb --build --root-owner-group ..."
dpkg-deb --build --root-owner-group "$STAGE_DIR/$PKG_NAME" "$DEB_PATH" >/dev/null

# ----------------------------------------------------------- verify --
[ -f "$DEB_PATH" ] || die "dpkg-deb did not produce $DEB_PATH"
DEB_SIZE="$(stat -c%s "$DEB_PATH" 2>/dev/null || stat -f%z "$DEB_PATH")"
info "built  : $DEB_PATH  (size=${DEB_SIZE} bytes)"

info "info   :"
dpkg-deb --info "$DEB_PATH" | sed 's/^/  /'

info "contents (head) :"
dpkg-deb --contents "$DEB_PATH" | sed 's/^/  /' | head -20

info "done."

#!/usr/bin/env bash
# scripts/build-pkg.sh
#
# Build a macOS Installer package (.pkg) for nova.
#
# Pipeline:
#   1. pkgbuild  --root <stage> ... nova-component.pkg
#   2. productbuild --distribution distribution.xml ... nova-<version>.pkg
#
# Usage:
#   make package-pkg                              # via Makefile, recommended
#   scripts/build-pkg.sh                          # direct invocation
#   SIGN_IDENTITY="Developer ID Installer: ..." scripts/build-pkg.sh
#
# Real codesigning requires a paid Apple Developer ID Installer
# certificate in the host keychain. Set SIGN_IDENTITY to enable.
# Without it, an unsigned .pkg is produced (still installs, but Gatekeeper
# blocks until the user right-clicks -> Open).
#
# This script must run on macOS proper (pkgbuild + productbuild ship
# with Xcode Command Line Tools). On Linux it falls back to writing a
# recipe file documenting the steps.
#
# Notarization is a separate manual step on the build host -- see
# scripts/sign.sh.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
DIST_DIR="$ROOT_DIR/dist"
STAGE_DIR="$DIST_DIR/pkg-stage"
PKG_RES="$ROOT_DIR/packaging/macos"

info() { printf 'build-pkg: %s\n' "$*"; }
warn() { printf 'build-pkg: WARN: %s\n' "$*" >&2; }
die()  { printf 'build-pkg: ERROR: %s\n' "$*" >&2; exit "${2:-3}"; }

VERSION="$(tr -d ' \t\r\n' < "$ROOT_DIR/VERSION")"
[ -n "$VERSION" ] || die "VERSION file is empty" 2
PKG_BASENAME="nova-${VERSION}"

# ----------------------------------------------------------- preflight --
HAVE_PKGBUILD=0
HAVE_PRODUCTBUILD=0
command -v pkgbuild     >/dev/null 2>&1 && HAVE_PKGBUILD=1
command -v productbuild >/dev/null 2>&1 && HAVE_PRODUCTBUILD=1

if [ "$HAVE_PKGBUILD" = "0" ] || [ "$HAVE_PRODUCTBUILD" = "0" ]; then
    info "pkgbuild/productbuild not available on this host (need macOS + Xcode CLT)."
    mkdir -p "$DIST_DIR"
    cat > "$DIST_DIR/${PKG_BASENAME}.pkg.txt" <<EOF
nova .pkg build recipe (no pkgbuild/productbuild on this host)
==============================================================

This recipe documents what scripts/build-pkg.sh runs when invoked on
macOS. It exists so the GitHub Actions macos-latest runner can build a
real .pkg, but the script can be inspected and audited from Linux.

Required commands (all shipped by Xcode Command Line Tools):
  pkgbuild      builds the component package from the payload tree
  productbuild  wraps it in a distribution .xml installer

Steps:

  1. Stage the payload at dist/pkg-stage/payload:
       payload/usr/local/bin/nova                  # the compiler
       payload/usr/local/share/man/man1/nova.1     # man page
       payload/usr/local/share/doc/nova/README.md
       payload/usr/local/share/doc/nova/examples/*.nova

  2. Build the component:
       pkgbuild --root dist/pkg-stage/payload \\
                --identifier org.nova-lang.nova \\
                --version ${VERSION} \\
                --install-location / \\
                --scripts dist/pkg-stage/scripts \\
                dist/pkg-stage/nova-component.pkg

  3. Build the product:
       productbuild --distribution packaging/macos/nova-distribution.xml \\
                    --resources    packaging/macos/resources \\
                    --package-path dist/pkg-stage \\
                    dist/${PKG_BASENAME}.pkg

  4. (Optional) Sign with Developer ID Installer:
       productsign --sign "Developer ID Installer: ..." \\
                   dist/${PKG_BASENAME}.pkg \\
                   dist/${PKG_BASENAME}-signed.pkg

  5. (Optional) Notarize:
       xcrun notarytool submit dist/${PKG_BASENAME}-signed.pkg \\
                                --apple-id you@example.com \\
                                --team-id  XXXXXXXXXX \\
                                --wait
       xcrun stapler staple    dist/${PKG_BASENAME}-signed.pkg

This recipe was generated from packaging/macos/nova-distribution.xml
and packaging/macos/resources/{welcome,license,conclusion}.txt.
EOF
    info "wrote recipe: $DIST_DIR/${PKG_BASENAME}.pkg.txt"
    exit 0
fi

[ -f "$ROOT_DIR/bin/nova" ] || die "bin/nova missing -- run 'make bin/nova' first" 2

# ----------------------------------------------------------- stage --
rm -rf "$STAGE_DIR"
mkdir -p "$STAGE_DIR/payload/usr/local/bin"
mkdir -p "$STAGE_DIR/payload/usr/local/share/man/man1"
mkdir -p "$STAGE_DIR/payload/usr/local/share/doc/nova/examples"
mkdir -p "$STAGE_DIR/scripts"

install -m 0755 "$ROOT_DIR/bin/nova" "$STAGE_DIR/payload/usr/local/bin/nova"
install -m 0644 "$ROOT_DIR/packaging/man/nova.1" \
    "$STAGE_DIR/payload/usr/local/share/man/man1/nova.1"

for ex in hello.nova hello_macos.nova basic_mind.nova; do
    [ -f "$ROOT_DIR/examples/$ex" ] && install -m 0644 \
        "$ROOT_DIR/examples/$ex" \
        "$STAGE_DIR/payload/usr/local/share/doc/nova/examples/$ex"
done
[ -f "$ROOT_DIR/README.md" ]  && install -m 0644 "$ROOT_DIR/README.md"  "$STAGE_DIR/payload/usr/local/share/doc/nova/"
[ -f "$ROOT_DIR/INSTALL.md" ] && install -m 0644 "$ROOT_DIR/INSTALL.md" "$STAGE_DIR/payload/usr/local/share/doc/nova/"

install -m 0755 "$PKG_RES/scripts/postinstall" "$STAGE_DIR/scripts/postinstall"

# ----------------------------------------------------------- component --
info "running pkgbuild ..."
pkgbuild \
    --root "$STAGE_DIR/payload" \
    --identifier org.nova-lang.nova \
    --version "$VERSION" \
    --install-location / \
    --scripts "$STAGE_DIR/scripts" \
    "$STAGE_DIR/nova-component.pkg"

# ----------------------------------------------------------- product --
info "running productbuild ..."
mkdir -p "$DIST_DIR"
productbuild \
    --distribution "$PKG_RES/nova-distribution.xml" \
    --resources    "$PKG_RES/resources" \
    --package-path "$STAGE_DIR" \
    "$DIST_DIR/${PKG_BASENAME}.pkg"

# ----------------------------------------------------------- sign --
if [ -n "${SIGN_IDENTITY:-}" ]; then
    info "signing with: $SIGN_IDENTITY"
    productsign --sign "$SIGN_IDENTITY" \
        "$DIST_DIR/${PKG_BASENAME}.pkg" \
        "$DIST_DIR/${PKG_BASENAME}-signed.pkg"
    mv "$DIST_DIR/${PKG_BASENAME}-signed.pkg" "$DIST_DIR/${PKG_BASENAME}.pkg"
    info "signed: $DIST_DIR/${PKG_BASENAME}.pkg"
else
    info "SIGN_IDENTITY not set -- producing unsigned .pkg"
    info "(Gatekeeper will require right-click -> Open until signed + notarized)"
fi

PKG_PATH="$DIST_DIR/${PKG_BASENAME}.pkg"
PKG_SIZE="$(stat -f%z "$PKG_PATH" 2>/dev/null || stat -c%s "$PKG_PATH")"
info "built  : $PKG_PATH  (size=${PKG_SIZE} bytes)"
info "done."

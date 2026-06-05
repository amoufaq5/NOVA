#!/usr/bin/env bash
# scripts/build-msi.sh
#
# Build a Windows MSI installer for nova using the WiX toolset (candle
# + light) v3.x. Requires:
#   - bin/nova.exe                  (from `make cross-windows`)
#   - WiX toolset (candle, light)   (https://wixtoolset.org/)
#
# Hosts:
#   - Windows + WiX installed natively         (recommended; GitHub Actions windows-latest)
#   - Linux + wine + WiX     (e.g. via `apt install wine; ./install-wix-wine.sh`)
#   - Linux without WiX                        (recipe-only mode, no failure)
#
# Usage:
#   make package-msi                                 # via Makefile, recommended
#   scripts/build-msi.sh                             # direct invocation
#   WIX_BIN=/path/to/wix scripts/build-msi.sh        # custom WiX prefix
#
# Output:
#   dist/nova-<version>.msi
#
# Signing is done with signtool.exe in a separate step; see scripts/sign.sh.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
DIST_DIR="$ROOT_DIR/dist"
WX_DIR="$ROOT_DIR/packaging/windows"

info() { printf 'build-msi: %s\n' "$*"; }
warn() { printf 'build-msi: WARN: %s\n' "$*" >&2; }
die()  { printf 'build-msi: ERROR: %s\n' "$*" >&2; exit "${2:-3}"; }

VERSION="$(tr -d ' \t\r\n' < "$ROOT_DIR/VERSION")"
[ -n "$VERSION" ] || die "VERSION file is empty" 2
MSI_BASENAME="nova-${VERSION}"

# ----------------------------------------------------------- preflight --
HAVE_WIX=0
CANDLE_CMD=""
LIGHT_CMD=""

# Look for candle / light in PATH or under WIX_BIN.
for prefix in "${WIX_BIN:-}" "" "/usr/bin" "/usr/local/bin"; do
    if [ -n "$prefix" ] && [ -x "$prefix/candle" ] && [ -x "$prefix/light" ]; then
        CANDLE_CMD="$prefix/candle"; LIGHT_CMD="$prefix/light"; HAVE_WIX=1; break
    fi
done
if [ "$HAVE_WIX" = "0" ] && command -v candle >/dev/null 2>&1 && command -v light >/dev/null 2>&1; then
    CANDLE_CMD="$(command -v candle)"; LIGHT_CMD="$(command -v light)"; HAVE_WIX=1
fi
# WiX v4+ uses a single `wix` binary -- detect that too.
if [ "$HAVE_WIX" = "0" ] && command -v wix >/dev/null 2>&1; then
    info "found WiX v4 (single 'wix' binary)"
    mkdir -p "$DIST_DIR"
    [ -f "$ROOT_DIR/bin/nova.exe" ] || die "bin/nova.exe missing -- run 'make cross-windows'" 2
    wix build \
        -d ProductVersion="$VERSION" \
        -d SourceDir="$ROOT_DIR" \
        "$WX_DIR/nova.wxs" \
        -o "$DIST_DIR/${MSI_BASENAME}.msi"
    info "built  : $DIST_DIR/${MSI_BASENAME}.msi"
    exit 0
fi

if [ "$HAVE_WIX" = "0" ]; then
    info "WiX toolset (candle/light, or wix v4) not available on this host."
    mkdir -p "$DIST_DIR"
    cat > "$DIST_DIR/${MSI_BASENAME}.msi.txt" <<EOF
nova .msi build recipe (no WiX toolset on this host)
====================================================

This recipe documents what scripts/build-msi.sh runs when invoked on a
host with WiX installed. The .wxs source at packaging/windows/nova.wxs
is auditable from any host.

WiX install options:
  * Windows:  download https://wixtoolset.org/releases/ (v3.14 stable)
              OR  dotnet tool install --global wix       (v4+)
  * Linux:    apt install wine; download WiX MSI; install under wine
  * GH Actions windows-latest: nuget install WiX -Version 3.14.1.8722

Build steps (v3.x):

  1. cd packaging/windows
  2. candle nova.wxs -dSourceDir=../.. -dProductVersion=${VERSION} -o nova.wixobj
  3. light  -ext WixUIExtension -ext WixUtilExtension nova.wixobj -o ../../dist/${MSI_BASENAME}.msi
  4. Verify with:
       msiinfo suminfo dist/${MSI_BASENAME}.msi
     or, on Windows:
       msiexec /i dist\\${MSI_BASENAME}.msi /quiet /lv install.log

Build steps (v4+):

  1. wix build -d SourceDir=. -d ProductVersion=${VERSION} \\
       packaging/windows/nova.wxs -o dist/${MSI_BASENAME}.msi

Signing (separate step, requires EV / OV code-signing cert in cert store):

  signtool sign /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 \\
      dist/${MSI_BASENAME}.msi
EOF
    info "wrote recipe: $DIST_DIR/${MSI_BASENAME}.msi.txt"
    exit 0
fi

[ -f "$ROOT_DIR/bin/nova.exe" ] || die "bin/nova.exe missing -- run 'make cross-windows' first" 2

# ----------------------------------------------------------- compile + link --
mkdir -p "$DIST_DIR"
WIXOBJ="$DIST_DIR/nova.wixobj"

info "running candle ..."
"$CANDLE_CMD" \
    -dSourceDir="$ROOT_DIR" \
    -dProductVersion="$VERSION" \
    -arch x64 \
    -o "$WIXOBJ" \
    "$WX_DIR/nova.wxs"

info "running light ..."
"$LIGHT_CMD" \
    -ext WixUIExtension \
    -ext WixUtilExtension \
    -o "$DIST_DIR/${MSI_BASENAME}.msi" \
    "$WIXOBJ"

rm -f "$WIXOBJ"

MSI_PATH="$DIST_DIR/${MSI_BASENAME}.msi"
[ -f "$MSI_PATH" ] || die "light did not produce $MSI_PATH"
MSI_SIZE="$(stat -c%s "$MSI_PATH" 2>/dev/null || stat -f%z "$MSI_PATH")"
info "built  : $MSI_PATH  (size=${MSI_SIZE} bytes)"
info "done."

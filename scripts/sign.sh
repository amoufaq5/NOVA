#!/usr/bin/env bash
# scripts/sign.sh
#
# Wrapper for the three platform-specific code-signing flows. None of
# these can run on the Linux sandbox -- they all need real certs in the
# host keychain / cert store. This script exists so CI can call a single
# entry point and so the steps are documented in one place.
#
# Usage:
#   scripts/sign.sh deb           # detached signature (gpg)
#   scripts/sign.sh pkg           # productsign + notarytool
#   scripts/sign.sh msi           # signtool
#
# Required env:
#   For deb: NOVA_GPG_KEY      gpg key id
#   For pkg: NOVA_SIGN_PKG     "Developer ID Installer: ..." identity
#            NOVA_APPLE_ID     apple id email for notarytool
#            NOVA_TEAM_ID      apple developer team id
#            NOVA_APPLE_PWD    app-specific password (or use keychain profile)
#   For msi: NOVA_SIGN_PFX     path to .pfx (PKCS#12) cert
#            NOVA_SIGN_PWD     password for the .pfx
#
# Exit codes:
#   0 = signed (or skipped because credentials weren't supplied)
#   2 = pre-flight failure
#   3 = signing failure

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
DIST_DIR="$ROOT_DIR/dist"

info() { printf 'sign: %s\n' "$*"; }
warn() { printf 'sign: WARN: %s\n' "$*" >&2; }
die()  { printf 'sign: ERROR: %s\n' "$*" >&2; exit "${2:-3}"; }

usage() { cat <<'EOF'
Usage: sign.sh {deb|pkg|msi}

Signs the matching artifact in dist/. Credentials are read from the
environment -- see the header comment of this script.
EOF
exit 2
}

[ $# -eq 1 ] || usage

case "$1" in
    deb)
        DEB="$(ls "$DIST_DIR"/nova_*_amd64.deb 2>/dev/null | head -1)"
        [ -n "$DEB" ] || die "no .deb in dist/" 2
        if [ -z "${NOVA_GPG_KEY:-}" ]; then
            info "NOVA_GPG_KEY not set -- skipping detached signature"
            exit 0
        fi
        if ! command -v gpg >/dev/null 2>&1; then
            die "gpg not installed" 2
        fi
        info "signing $DEB with key $NOVA_GPG_KEY"
        gpg --batch --yes --armor --detach-sign --local-user "$NOVA_GPG_KEY" \
            --output "${DEB}.asc" "$DEB"
        info "wrote: ${DEB}.asc"
        ;;
    pkg)
        PKG="$(ls "$DIST_DIR"/nova-*.pkg 2>/dev/null | head -1)"
        [ -n "$PKG" ] || die "no .pkg in dist/" 2
        if [ -z "${NOVA_SIGN_PKG:-}" ]; then
            info "NOVA_SIGN_PKG not set -- skipping productsign"
            exit 0
        fi
        if ! command -v productsign >/dev/null 2>&1; then
            die "productsign not available (need macOS)" 2
        fi
        info "productsign $PKG ..."
        productsign --sign "$NOVA_SIGN_PKG" "$PKG" "${PKG%.pkg}-signed.pkg"
        mv "${PKG%.pkg}-signed.pkg" "$PKG"

        if [ -n "${NOVA_APPLE_ID:-}" ] && [ -n "${NOVA_TEAM_ID:-}" ]; then
            info "notarytool submit ..."
            xcrun notarytool submit "$PKG" \
                --apple-id "$NOVA_APPLE_ID" \
                --team-id  "$NOVA_TEAM_ID" \
                --password "${NOVA_APPLE_PWD:-}" \
                --wait
            xcrun stapler staple "$PKG"
            info "notarized + stapled: $PKG"
        else
            info "NOVA_APPLE_ID / NOVA_TEAM_ID unset -- skipped notarization"
        fi
        ;;
    msi)
        MSI="$(ls "$DIST_DIR"/nova-*.msi 2>/dev/null | head -1)"
        [ -n "$MSI" ] || die "no .msi in dist/" 2
        if [ -z "${NOVA_SIGN_PFX:-}" ]; then
            info "NOVA_SIGN_PFX not set -- skipping signtool"
            exit 0
        fi
        if ! command -v signtool >/dev/null 2>&1 && ! command -v signtool.exe >/dev/null 2>&1; then
            die "signtool not available (need Windows SDK)" 2
        fi
        info "signtool sign $MSI ..."
        signtool sign \
            /fd SHA256 \
            /tr "http://timestamp.digicert.com" \
            /td SHA256 \
            /f "$NOVA_SIGN_PFX" \
            /p "${NOVA_SIGN_PWD:-}" \
            "$MSI"
        info "signed: $MSI"
        ;;
    *)
        usage
        ;;
esac

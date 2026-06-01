#!/usr/bin/env bash
# packaging/homebrew/bump-formula.sh
#
# Non-interactive Homebrew formula bumper. Equivalent to running
# `brew bump-formula-pr` but works on Linux runners and uses gh.
#
# Pipeline:
#   1. Read VERSION (or the $VERSION env override).
#   2. For each platform tarball that exists on the GitHub release,
#      download it and compute the SHA256.
#   3. Rewrite tools/Formula/nova.rb in place with the new URLs +
#      SHA256s + version.
#   4. (Optional, if PUSH_TAP=1) push the updated formula to the
#      nova-lang/homebrew-nova tap as a new branch + PR.
#
# Required:
#   curl, sha256sum (or shasum), sed, awk, grep
#   gh             (only if PUSH_TAP=1)
#
# Usage:
#   VERSION=0.2.0 packaging/homebrew/bump-formula.sh           # local rewrite
#   VERSION=0.2.0 PUSH_TAP=1 GH_TOKEN=... packaging/homebrew/bump-formula.sh
#
# Exit codes:
#   0 = success
#   2 = pre-flight failure
#   3 = network / download failure
#   4 = tap push failure

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
FORMULA="$ROOT_DIR/tools/Formula/nova.rb"

info() { printf 'bump-formula: %s\n' "$*"; }
die()  { printf 'bump-formula: ERROR: %s\n' "$*" >&2; exit "${2:-3}"; }

[ -f "$FORMULA" ] || die "formula not found at $FORMULA" 2

VERSION="${VERSION:-$(tr -d ' \t\r\n' < "$ROOT_DIR/VERSION")}"
[ -n "$VERSION" ] || die "VERSION env var or VERSION file is required" 2
TAG="v${VERSION}"
REPO="${NOVA_REPO:-nova-lang/nova}"
TAP_REPO="${NOVA_TAP_REPO:-nova-lang/homebrew-nova}"

info "version=${VERSION} tag=${TAG} repo=${REPO}"

# ----------------------------------------------------------- helpers --
have() { command -v "$1" >/dev/null 2>&1; }
sha256_of() {
    if have sha256sum; then sha256sum "$1" | awk '{print $1}';
    elif have shasum;  then shasum -a 256 "$1" | awk '{print $1}';
    else die "neither sha256sum nor shasum available" 2; fi
}

TMPDIR_BUMP="$(mktemp -d)"
trap 'rm -rf "$TMPDIR_BUMP"' EXIT INT TERM

fetch_sha() {
    local platform="$1"      # macos-x86_64 / linux-x86_64 / ...
    local tarball="nova-${platform}.tar.gz"
    local url="https://github.com/${REPO}/releases/download/${TAG}/${tarball}"
    local dst="${TMPDIR_BUMP}/${tarball}"
    info "fetching ${platform} -> ${url}"
    if curl -fSL --retry 3 -o "$dst" "$url" 2>/dev/null; then
        sha256_of "$dst"
    else
        info "  (no asset for ${platform} -- skipping)"
        echo ""
    fi
}

SHA_MACOS_X86="$(fetch_sha macos-x86_64)"
SHA_LINUX_X86="$(fetch_sha linux-x86_64)"
SHA_MACOS_ARM="$(fetch_sha macos-arm64)"     || true
SHA_LINUX_ARM="$(fetch_sha linux-arm64)"     || true

# ----------------------------------------------------------- rewrite --
info "rewriting $FORMULA"

# In-place sed: bump version, then replace each sha placeholder if we
# actually fetched something.
sed -i.bak \
    -e "s|version \"[0-9]\+\.[0-9]\+\.[0-9]\+\"|version \"${VERSION}\"|" \
    -e "s|releases/download/v[0-9]\+\.[0-9]\+\.[0-9]\+/|releases/download/${TAG}/|g" \
    "$FORMULA"

if [ -n "$SHA_MACOS_X86" ]; then
    sed -i.bak -e "s|REPLACE_WITH_RELEASE_SHA256_MACOS_X86_64|${SHA_MACOS_X86}|" "$FORMULA"
fi
if [ -n "$SHA_LINUX_X86" ]; then
    sed -i.bak -e "s|REPLACE_WITH_RELEASE_SHA256_LINUX_X86_64|${SHA_LINUX_X86}|" "$FORMULA"
fi
if [ -n "$SHA_MACOS_ARM" ]; then
    sed -i.bak -e "s|REPLACE_WITH_RELEASE_SHA256_MACOS_ARM64|${SHA_MACOS_ARM}|" "$FORMULA"
fi
if [ -n "$SHA_LINUX_ARM" ]; then
    sed -i.bak -e "s|REPLACE_WITH_RELEASE_SHA256_LINUX_ARM64|${SHA_LINUX_ARM}|" "$FORMULA"
fi
rm -f "${FORMULA}.bak"

info "rewritten:"
grep -E "^\s+(version|url|sha256)" "$FORMULA" | sed 's/^/  /'

# ----------------------------------------------------------- push tap --
if [ "${PUSH_TAP:-0}" = "1" ]; then
    have gh || die "gh CLI required for PUSH_TAP=1" 2
    [ -n "${GH_TOKEN:-}" ] || die "GH_TOKEN required for PUSH_TAP=1" 2

    TAP_DIR="${TMPDIR_BUMP}/tap"
    git clone "https://github.com/${TAP_REPO}.git" "$TAP_DIR" 2>&1 \
        || die "clone of ${TAP_REPO} failed" 4
    mkdir -p "${TAP_DIR}/Formula"
    cp "$FORMULA" "${TAP_DIR}/Formula/nova.rb"

    cd "$TAP_DIR"
    git checkout -b "bump-nova-${VERSION}"
    git add Formula/nova.rb
    git -c user.email="bot@nova-lang.org" -c user.name="nova-bot" \
        commit -m "nova ${VERSION}"
    git push origin "bump-nova-${VERSION}"
    gh pr create --title "nova ${VERSION}" \
                 --body "Auto-bumped by packaging/homebrew/bump-formula.sh"
    info "PR opened against ${TAP_REPO}"
fi

info "done."

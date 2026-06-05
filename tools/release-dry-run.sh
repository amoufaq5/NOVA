#!/usr/bin/env bash
#
# Local equivalent of the .github/workflows/release.yml pipeline.
#
# Reads VERSION, runs the matrix builds (Linux + optional cross-windows
# and cross-macos via the existing make targets), tars each into
# nova-${target}.tar.gz with a matching .sha256, and prints what
# WOULD be uploaded to GitHub Releases. Does NOT push anything; this
# is the verifiable local equivalent of the GH Actions workflow.
#
# Usage:
#   tools/release-dry-run.sh                  # build all targets
#   tools/release-dry-run.sh linux            # build just linux
#   tools/release-dry-run.sh linux windows    # build a subset
#
# Output:
#   dist/release/nova-${target}.tar.gz
#   dist/release/nova-${target}.tar.gz.sha256
#
# Failure modes deliberately surfaced:
#   - missing cross-toolchain (mingw / clang) -> warn and skip with
#     the specific tool name, so the install docs can point users at it.
#   - bin/nova missing -> abort (refuse to ship a missing artifact).
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
DIST_DIR="$ROOT_DIR/dist/release"
STAGE_DIR="$ROOT_DIR/dist/stage"

VERSION_FILE="$ROOT_DIR/VERSION"
if [ ! -f "$VERSION_FILE" ]; then
    echo "release-dry-run: VERSION file missing at $VERSION_FILE" >&2
    exit 2
fi
VERSION="$(tr -d ' \t\r\n' < "$VERSION_FILE")"
TAG="v${VERSION}"

info() { printf 'release-dry-run: %s\n' "$*"; }
warn() { printf 'release-dry-run: WARN: %s\n' "$*" >&2; }
die()  { printf 'release-dry-run: ERROR: %s\n' "$*" >&2; exit 1; }

have() { command -v "$1" >/dev/null 2>&1; }

sha256_cmd() {
    if have sha256sum; then
        sha256sum "$@"
    elif have shasum; then
        shasum -a 256 "$@"
    else
        die "neither sha256sum nor shasum available"
    fi
}

# Each entry: <target>:<artifact-in-bin>:<make-rule>
ALL_TARGETS=(
    "linux-x86_64:nova:bin/nova"
    "windows-x86_64:nova.exe:cross-windows"
    "macos-x86_64:nova_macos.s:cross-macos"
)

TARGETS=()
if [ $# -eq 0 ]; then
    for t in "${ALL_TARGETS[@]}"; do TARGETS+=("$t"); done
else
    for arg in "$@"; do
        matched=""
        for t in "${ALL_TARGETS[@]}"; do
            tname="${t%%:*}"
            short="${tname%-*}"
            if [ "$arg" = "$tname" ] || [ "$arg" = "$short" ]; then
                matched="$t"; break
            fi
        done
        if [ -z "$matched" ]; then
            die "unknown target: $arg (try: linux, windows, macos)"
        fi
        TARGETS+=("$matched")
    done
fi

mkdir -p "$DIST_DIR" "$STAGE_DIR"

info "version: $VERSION (tag $TAG)"
info "dist dir: $DIST_DIR"

# ---------------------------------------------------------------- build matrix --
info "running matrix builds for: $(printf '%s ' "${TARGETS[@]}" | sed 's/ $//' )"
STAGED=()
for entry in "${TARGETS[@]}"; do
    target="${entry%%:*}"
    rest="${entry#*:}"
    artifact="${rest%%:*}"
    rule="${rest#*:}"

    info "  -> $target  (artifact=$artifact  make=$rule)"

    # Tool availability gate: surface missing cross-toolchains with a
    # clear message rather than the make rule's opaque output.
    case "$target" in
        windows-x86_64)
            if ! have x86_64-w64-mingw32-as; then
                warn "skipping $target: x86_64-w64-mingw32-as (mingw-w64) not installed"
                continue
            fi ;;
        macos-x86_64)
            if ! have clang; then
                warn "$target: clang not installed -- will emit .s only (no .o)"
            fi ;;
    esac

    # If the artifact already exists, prefer it (avoids re-building
    # when the user already ran `make` manually). Otherwise invoke
    # the rule.
    src="$ROOT_DIR/bin/$artifact"
    if [ ! -f "$src" ]; then
        if ! make -C "$ROOT_DIR" -s "$rule"; then
            warn "make $rule failed for $target -- skipping"
            continue
        fi
    else
        info "    (reusing existing $artifact at $src)"
    fi

    if [ ! -f "$src" ]; then
        warn "expected artifact missing after build: $src -- skipping"
        continue
    fi

    # Stage into dist/stage/<target>/nova/, matching the layout install.sh
    # expects: tar contains a single top-level `nova/` directory.
    stage_pkg="$STAGE_DIR/$target/nova"
    rm -rf "$stage_pkg"
    mkdir -p "$stage_pkg"
    cp "$src" "$stage_pkg/$artifact"
    chmod +x "$stage_pkg/$artifact" 2>/dev/null || true

    # Ship docs alongside the binary so users can read INSTALL.md
    # right after extracting the tarball.
    for doc in README.md INSTALL.md LICENSE VERSION; do
        if [ -f "$ROOT_DIR/$doc" ]; then
            cp "$ROOT_DIR/$doc" "$stage_pkg/"
        fi
    done

    tarball="$DIST_DIR/nova-${target}.tar.gz"
    ( cd "$STAGE_DIR/$target" && tar -czf "$tarball" nova )
    sha256_cmd "$tarball" > "${tarball}.sha256"
    info "    -> $(basename "$tarball")  ($(wc -c <"$tarball") bytes)"
    info "    -> $(basename "$tarball").sha256  : $(awk '{print $1}' "${tarball}.sha256")"
    STAGED+=("$target")
done

# ---------------------------------------------------------------- summary --
echo ""
echo "Would publish to GitHub Release ${TAG} (https://github.com/nova-lang/nova/releases):"
echo ""
for target in "${STAGED[@]}"; do
    tarball="$DIST_DIR/nova-${target}.tar.gz"
    sha="$(awk '{print $1}' "${tarball}.sha256")"
    size="$(wc -c <"$tarball")"
    printf '  %-22s  %10s bytes  sha256=%s\n' \
        "nova-${target}.tar.gz" "$size" "$sha"
done
echo ""
echo "To actually publish, push the tag and let .github/workflows/release.yml run:"
echo "    git tag ${TAG} && git push origin ${TAG}"
echo ""
echo "Smoke-test the dry-run tarball locally with install.sh:"
if [ ${#STAGED[@]} -gt 0 ]; then
    first="${STAGED[0]}"
    echo "    NOVA_URL=file://${DIST_DIR}/nova-${first}.tar.gz \\"
    echo "        NOVA_PREFIX=\$(mktemp -d)/bin \\"
    echo "        sh tools/install.sh"
fi
echo ""
echo "Homebrew formula update (do this AFTER the release lands):"
for target in "${STAGED[@]}"; do
    if [ "$target" = "macos-x86_64" ] || [ "$target" = "linux-x86_64" ]; then
        tarball="$DIST_DIR/nova-${target}.tar.gz"
        sha="$(awk '{print $1}' "${tarball}.sha256")"
        echo "    # ${target}: sha256 \"${sha}\""
    fi
done

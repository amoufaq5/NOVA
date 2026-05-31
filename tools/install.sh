#!/usr/bin/env sh
# Nova one-shot installer.
#
# Usage:
#   curl -sSL https://raw.githubusercontent.com/nova-lang/nova/main/tools/install.sh | sh
#
# Environment overrides:
#   NOVA_VERSION   pin to a specific release tag (default: latest published)
#   NOVA_PREFIX    install destination (default: $HOME/.local/bin)
#   NOVA_REPO      GitHub repo, owner/name (default: nova-lang/nova)
#
# The script detects OS + arch, downloads the matching .tar.gz from
# GitHub Releases, extracts the `nova` binary to NOVA_PREFIX, and
# updates the user's PATH on a best-effort basis.

set -eu

NOVA_REPO="${NOVA_REPO:-nova-lang/nova}"
NOVA_PREFIX="${NOVA_PREFIX:-$HOME/.local/bin}"
LATEST_VERSION="${NOVA_VERSION:-v0.1.0}"

# ---------------------------------------------------------------- helpers --
die() { printf 'install: %s\n' "$*" >&2; exit 1; }
info() { printf 'install: %s\n' "$*"; }

have() { command -v "$1" >/dev/null 2>&1; }

# ------------------------------------------------------- platform detection --
detect_os() {
    case "$(uname -s)" in
        Linux*)   echo linux ;;
        Darwin*)  echo macos ;;
        MINGW*|MSYS*|CYGWIN*) echo windows ;;
        *)        die "unsupported OS: $(uname -s)" ;;
    esac
}

detect_arch() {
    case "$(uname -m)" in
        x86_64|amd64) echo x86_64 ;;
        aarch64|arm64) echo arm64 ;;
        *) die "unsupported arch: $(uname -m)" ;;
    esac
}

OS="$(detect_os)"
ARCH="$(detect_arch)"
TARGET="${OS}-${ARCH}"

# Note: the release matrix in .github/workflows/release.yml currently
# publishes linux-x86_64, windows-x86_64 (.exe), and macos-x86_64 (.o
# for local link). arm64 builds will be added once cross-toolchains land.
case "$TARGET" in
    linux-x86_64)   ARTIFACT="nova" ; TARBALL="nova-linux-x86_64.tar.gz" ;;
    windows-x86_64) ARTIFACT="nova.exe" ; TARBALL="nova-windows-x86_64.tar.gz" ;;
    macos-x86_64)   ARTIFACT="nova_macos.o" ; TARBALL="nova-macos-x86_64.tar.gz" ;;
    *) die "no prebuilt release for ${TARGET} yet — please build from source (see INSTALL.md)" ;;
esac

# Default URL points at GitHub release; can be overridden via NOVA_URL
# for testing (e.g. `file:///path/to/local/dry-run/tarball`). The
# release-dry-run.sh script prints a one-liner using NOVA_URL.
URL="${NOVA_URL:-https://github.com/${NOVA_REPO}/releases/download/${LATEST_VERSION}/${TARBALL}}"
info "platform     : ${TARGET}"
info "version      : ${LATEST_VERSION}"
info "downloading  : ${URL}"

# --------------------------------------------------------------- fetcher --
TMPDIR_NOVA="$(mktemp -d 2>/dev/null || mktemp -d -t novainst)"
trap 'rm -rf "$TMPDIR_NOVA"' EXIT INT TERM

fetch() {
    src="$1"; dst="$2"
    # file:// URLs: just copy. Lets tools/release-dry-run.sh smoke-test
    # this script end-to-end without round-tripping through GitHub.
    case "$src" in
        file://*)
            cp "${src#file://}" "$dst" || die "failed to copy local file: ${src#file://}"
            return 0 ;;
    esac
    if have curl; then
        curl -fSL --retry 3 -o "$dst" "$src"
    elif have wget; then
        wget -q -O "$dst" "$src"
    else
        die "need curl or wget to download release artifacts"
    fi
}

fetch "$URL" "${TMPDIR_NOVA}/${TARBALL}"

# --------------------------------------------------------------- extract --
info "extracting   : ${TMPDIR_NOVA}/${TARBALL}"
( cd "$TMPDIR_NOVA" && tar -xzf "$TARBALL" )

SRC_BIN="${TMPDIR_NOVA}/nova/${ARTIFACT}"
[ -f "$SRC_BIN" ] || die "tarball missing expected artifact: ${ARTIFACT}"

mkdir -p "$NOVA_PREFIX"
DEST_NAME="nova"
case "$OS" in
    windows) DEST_NAME="nova.exe" ;;
    macos)
        # macOS ships as a relocatable .o file for local linking.
        DEST_NAME="nova_macos.o"
        info "macOS object file extracted — link with: clang -o nova ${DEST_NAME}"
        ;;
esac
cp "$SRC_BIN" "${NOVA_PREFIX}/${DEST_NAME}"
chmod +x "${NOVA_PREFIX}/${DEST_NAME}" 2>/dev/null || true
info "installed    : ${NOVA_PREFIX}/${DEST_NAME}"

# ---------------------------------------------------------------- PATH ----
update_path_hint() {
    case ":${PATH}:" in
        *":${NOVA_PREFIX}:"*) info "PATH         : already contains ${NOVA_PREFIX}"; return ;;
    esac
    profile=""
    if [ -n "${ZSH_VERSION:-}" ] || [ "$(basename "${SHELL:-/bin/sh}")" = "zsh" ]; then
        profile="$HOME/.zshrc"
    elif [ -f "$HOME/.bashrc" ]; then
        profile="$HOME/.bashrc"
    elif [ -f "$HOME/.profile" ]; then
        profile="$HOME/.profile"
    fi
    if [ -n "$profile" ] && ! grep -qs "${NOVA_PREFIX}" "$profile"; then
        printf '\n# Added by Nova installer\nexport PATH="%s:$PATH"\n' "$NOVA_PREFIX" >>"$profile"
        info "PATH         : appended to ${profile} (re-source it to pick up nova)"
    else
        info "PATH         : add ${NOVA_PREFIX} to your shell init manually"
    fi
}
update_path_hint

info "done. Try:  nova --version"

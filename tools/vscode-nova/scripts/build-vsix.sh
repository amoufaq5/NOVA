#!/usr/bin/env bash
# build-vsix.sh — produce nova-language-<version>.vsix
#
# Steps:
#   1. cd to the extension root
#   2. npm install (idempotent)
#   3. npm run compile (tsc → out/)
#   4. npx vsce package --no-dependencies
#
# Outputs the VSIX path on stdout (last line).
#
# Failure modes:
#   - npm not on PATH:            prints a hint and exits 2.
#   - tsc compile fails:          tsc's own diagnostics, exits non-zero.
#   - vsce missing required field: vsce's own diagnostic.
#   - vsce rejects publisher:     use `--no-dependencies` + a
#                                 placeholder publisher; install via
#                                 `code --install-extension <file>.vsix`.
#
# Re-run safely; npm install is incremental.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

cd "${EXT_ROOT}"

if ! command -v npm > /dev/null 2>&1; then
    echo "build-vsix.sh: ERROR: npm not found on PATH." >&2
    echo "Install Node.js 18+ (https://nodejs.org/) and re-run." >&2
    exit 2
fi

echo "==> npm install"
npm install

echo "==> npm run compile"
npm run compile

echo "==> npx vsce package --no-dependencies"
npx --yes @vscode/vsce@latest package --no-dependencies --allow-missing-repository

VSIX_FILE="$(ls -t -- *.vsix 2> /dev/null | head -n 1 || true)"
if [[ -z "${VSIX_FILE}" ]]; then
    echo "build-vsix.sh: ERROR: no .vsix produced." >&2
    exit 3
fi

echo
echo "==> Built: ${EXT_ROOT}/${VSIX_FILE}"
echo
echo "Install locally via:"
echo "    code --install-extension ${EXT_ROOT}/${VSIX_FILE}"
echo

# Last line is the absolute VSIX path (machine-parseable).
echo "${EXT_ROOT}/${VSIX_FILE}"

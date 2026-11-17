#!/bin/bash
# The compiled-in version (src/compiler/compiler.nova NOVA_VERSION_FALLBACK)
# must match VERSION; `nova version` (launcher) reads VERSION directly.
cd "$(dirname "$0")/.." || exit 2
want=$(tr -d '[:space:]' < VERSION)
have=$(sed -n 's/^let NOVA_VERSION_FALLBACK = "\(.*\)"$/\1/p' src/compiler/compiler.nova)
if [ "$want" != "$have" ]; then
    echo "VERSION is '$want' but src/compiler/compiler.nova says '$have'"
    exit 1
fi
echo "version: $want"

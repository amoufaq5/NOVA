#!/bin/bash
# Regenerate the bootstrap seed (boot/nova_boot.s) from the current compiler
# source, then check that the new seed reaches the self-host fixpoint from a
# clean state. Commit boot/nova_boot.s in its own PR.
#
# Everything is regenerated here: /tmp/nova_* files are shared by every
# checkout on the machine, so nothing left there by an earlier build is
# trusted. The seed is only replaced once a fresh stage 2 exists.
set -e
cd "$(dirname "$0")/.."
SRC=$(sed -n '/^COMPILER_SRC *=/,/^$/p' Makefile | tr -d '\\' | tr ' ' '\n' | grep '\.nova$')
rm -f bin/nova
make bin/nova
cat $SRC > /tmp/nova_combined.nova
NEW=$(mktemp)
trap 'rm -f "$NEW"' EXIT
bin/nova /tmp/nova_combined.nova -o "$NEW" >/dev/null
cp "$NEW" boot/nova_boot.s
rm -f boot/nova_boot boot/nova_boot.o bin/nova
make bin/nova
make self-host
cmp boot/nova_boot.s /tmp/nova_stage2.s
echo "reseeded: boot/nova_boot.s == stage2 ($(wc -l < boot/nova_boot.s) lines)"

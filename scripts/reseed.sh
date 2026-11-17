#!/bin/bash
# Regenerate the bootstrap seed (boot/nova_boot.s) from the current compiler
# source: build bin/nova from the existing seed, then make the seed equal to
# the stage-2 output and check that the new seed reaches the self-host
# fixpoint from a clean state. Commit boot/nova_boot.s in its own PR.
set -e
cd "$(dirname "$0")/.."
make bin/nova
cp /tmp/nova_stage2.s boot/nova_boot.s
rm -f boot/nova_boot boot/nova_boot.o bin/nova
make bin/nova
make self-host
cmp boot/nova_boot.s /tmp/nova_stage2.s
echo "reseeded: boot/nova_boot.s == stage2 ($(wc -l < boot/nova_boot.s) lines)"

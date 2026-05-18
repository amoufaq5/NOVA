#!/bin/bash
COMBINED=/tmp/nova_combined.nova
BOOT=$(dirname "$0")/../boot/nova_boot
$BOOT $COMBINED "$@"

#!/bin/bash
# Build the Nova compiler by concatenating all source files
# and running them through the bootstrap interpreter.
# Usage: ./build_compiler.sh <input.nova> [-o output.s]

# Create combined compiler source
cat src/compiler/ast.nova \
    src/compiler/lexer.nova \
    src/compiler/parser.nova \
    src/compiler/codegen.nova \
    src/compiler/compiler.nova \
    > /tmp/nova_compiler_combined.nova

# Run it through the bootstrap, passing the target file as arg
./boot/nova_boot /tmp/nova_compiler_combined.nova "$@"

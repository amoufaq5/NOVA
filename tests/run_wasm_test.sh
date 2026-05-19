#!/bin/bash
# Run a Nova program compiled to WASM through Node.js WASI
# Usage: bash tests/run_wasm_test.sh <file.nova>

set -e

NOVA_FILE="${1:?Usage: run_wasm_test.sh <file.nova>}"
WAT_FILE="/tmp/nova_wasm_test.wat"
WASM_FILE="/tmp/nova_wasm_test.wasm"

# Step 1: Compile .nova -> .wat
bin/nova "$NOVA_FILE" --target=wasm -o "$WAT_FILE" 2>/dev/null

# Step 2: .wat -> .wasm
node -e "
const wabt = require('wabt');
const fs = require('fs');
wabt().then(w => {
  const src = fs.readFileSync('$WAT_FILE', 'utf8');
  const mod = w.parseWat('test.wat', src);
  mod.validate();
  const {buffer} = mod.toBinary({});
  fs.writeFileSync('$WASM_FILE', Buffer.from(buffer));
});
" 2>/dev/null

# Step 3: Run
node --experimental-wasi-unstable-preview1 -e "
const {WASI} = require('wasi');
const fs = require('fs');
const wasi = new WASI({version: 'preview1', args: [], env: {}, preopens: {}});
const importObject = {wasi_snapshot_preview1: wasi.wasiImport};
const wasm = fs.readFileSync('$WASM_FILE');
WebAssembly.instantiate(wasm, importObject).then(({instance}) => {
  wasi.start(instance);
}).catch(e => { console.error('WASM runtime error:', e.message); process.exit(1); });
" 2>/dev/null

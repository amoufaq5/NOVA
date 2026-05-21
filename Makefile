# Nova Build System
# Builds the self-hosting Nova compiler from source.
#
# Prerequisites: `as` and `ld` (standard GNU binutils)
# No libc, no external dependencies.

AS = as
LD = ld
BOOT = boot/nova_boot
COMPILER_SRC = src/compiler/ast.nova \
               src/compiler/lexer.nova \
               src/compiler/parser.nova \
               src/compiler/ir.nova \
               src/compiler/regalloc.nova \
               src/compiler/lower_x64.nova \
               src/compiler/codegen.nova \
               src/pkg/pkg.nova \
               src/compiler/compiler.nova

.PHONY: all clean test bootstrap stage1 self-host test-all examples cross-macos

all: bin/nova

# Step 1: Build the assembly bootstrap
bootstrap: $(BOOT)

$(BOOT): boot/nova_boot.s
	$(AS) -o boot/nova_boot.o boot/nova_boot.s
	$(LD) -o $(BOOT) boot/nova_boot.o

# Step 2: Use bootstrap to compile Nova compiler (stage 1)
bin/nova: $(BOOT) $(COMPILER_SRC)
	@mkdir -p bin
	cat $(COMPILER_SRC) > /tmp/nova_combined.nova
	$(BOOT) /tmp/nova_combined.nova /tmp/nova_combined.nova -o /tmp/nova_stage1.s
	$(AS) -o /tmp/nova_stage1.o /tmp/nova_stage1.s
	$(LD) -o bin/nova /tmp/nova_stage1.o

# Step 3: Verify self-hosting (stage2 output == stage3 output)
self-host: bin/nova
	@cat $(COMPILER_SRC) > /tmp/nova_combined.nova
	bin/nova /tmp/nova_combined.nova -o /tmp/nova_stage2.s
	$(AS) -o /tmp/nova_stage2.o /tmp/nova_stage2.s
	$(LD) -o /tmp/nova_stage2 /tmp/nova_stage2.o
	/tmp/nova_stage2 /tmp/nova_combined.nova -o /tmp/nova_stage3.s
	diff /tmp/nova_stage2.s /tmp/nova_stage3.s
	@echo "=== SELF-HOSTING VERIFIED ==="

# Cross-compile for macOS (generates .s file; assemble on macOS with: as -o out.o out.s && ld -e _main -o out out.o)
cross-macos: bin/nova
	@mkdir -p bin
	cat $(COMPILER_SRC) > /tmp/nova_combined.nova
	bin/nova /tmp/nova_combined.nova --target=macos -o bin/nova_macos.s
	@echo "macOS assembly written to bin/nova_macos.s"
	@echo "Transfer to macOS and build with:"
	@echo "  as -o nova.o nova_macos.s"
	@echo "  ld -e _main -o nova nova.o"

# Compile to WASM and run (requires Node.js + wabt npm package)
wasm: bin/nova
	@if [ -z "$(FILE)" ]; then echo "Usage: make wasm FILE=path/to/file.nova"; exit 1; fi
	@bin/nova $(FILE) --target=wasm -o /tmp/nova_wasm.wat
	@node -e "\
	const wabt = require('wabt'); const fs = require('fs');\
	wabt().then(w => {\
	  const src = fs.readFileSync('/tmp/nova_wasm.wat', 'utf8');\
	  const mod = w.parseWat('test.wat', src); mod.validate();\
	  const {buffer} = mod.toBinary({});\
	  fs.writeFileSync('/tmp/nova_wasm.wasm', Buffer.from(buffer));\
	});" 2>/dev/null
	@node --experimental-wasi-unstable-preview1 -e "\
	const {WASI} = require('wasi'); const fs = require('fs');\
	const wasi = new WASI({version: 'preview1', args: [], env: {}, preopens: {}});\
	const importObject = {wasi_snapshot_preview1: wasi.wasiImport};\
	const wasm = fs.readFileSync('/tmp/nova_wasm.wasm');\
	WebAssembly.instantiate(wasm, importObject).then(({instance}) => {\
	  wasi.start(instance);\
	}).catch(e => { console.error('WASM error:', e.message); process.exit(1); });" 2>/dev/null

# Compile a .nova file to a binary
%.out: %.nova bin/nova
	bin/nova $< -o /tmp/$*.s
	$(AS) -o /tmp/$*.o /tmp/$*.s
	$(LD) -o $@ /tmp/$*.o

# Run individual tests
test: bin/nova
	@echo "--- test_add ---"
	@bin/nova tests/test_add.nova -o /tmp/t.s && $(AS) -o /tmp/t.o /tmp/t.s && $(LD) -o /tmp/t /tmp/t.o && /tmp/t
	@echo ""
	@echo "--- test_complex ---"
	@bin/nova tests/test_complex.nova -o /tmp/t.s && $(AS) -o /tmp/t.o /tmp/t.s && $(LD) -o /tmp/t /tmp/t.o && /tmp/t
	@echo ""
	@echo "--- test_while ---"
	@bin/nova tests/test_while.nova -o /tmp/t.s && $(AS) -o /tmp/t.o /tmp/t.s && $(LD) -o /tmp/t /tmp/t.o && /tmp/t
	@echo ""
	@echo "--- test_smart_ops ---"
	@bin/nova tests/test_smart_ops.nova -o /tmp/t.s && $(AS) -o /tmp/t.o /tmp/t.s && $(LD) -o /tmp/t /tmp/t.o && /tmp/t
	@echo ""
	@echo "--- test_runtime ---"
	@bin/nova tests/test_runtime.nova -o /tmp/t.s && $(AS) -o /tmp/t.o /tmp/t.s && $(LD) -o /tmp/t /tmp/t.o && /tmp/t
	@echo ""
	@echo "--- All tests passed ---"

# Run all tests using test runner
test-all: bin/nova
	@bash tests/run_tests.sh

# Build and run all examples
examples: bin/nova
	@echo "=== Building Examples ==="
	@for f in examples/*.nova; do \
		name=$$(basename "$$f" .nova); \
		echo "--- $$name ---"; \
		bin/nova "$$f" -o /tmp/ex_$$name.s 2>/dev/null && \
		$(AS) -o /tmp/ex_$$name.o /tmp/ex_$$name.s 2>/dev/null && \
		$(LD) -o /tmp/ex_$$name /tmp/ex_$$name.o 2>/dev/null && \
		timeout 5 /tmp/ex_$$name 2>/dev/null; \
		echo ""; \
	done

# Run the cognitive agent
agent: bin/nova
	@cat src/core/moment.nova src/core/signal.nova src/core/similarity.nova \
		src/core/node.nova src/core/channel.nova src/core/path.nova \
		src/core/soul.nova \
		src/mind/academic.nova src/mind/experiential.nova src/mind/emotion.nova \
		src/mind/memory.nova src/mind/reasoning.nova \
		src/agent/agent.nova > /tmp/nova_agent.nova
	@bin/nova /tmp/nova_agent.nova -o /tmp/nova_agent.s 2>/dev/null && \
	$(AS) -o /tmp/nova_agent.o /tmp/nova_agent.s && \
	$(LD) -o /tmp/nova_agent /tmp/nova_agent.o && \
	/tmp/nova_agent

# Compile a single .nova file and run it
run: bin/nova
	@if [ -z "$(FILE)" ]; then echo "Usage: make run FILE=path/to/file.nova"; exit 1; fi
	@bin/nova $(FILE) -o /tmp/nova_run.s && \
	$(AS) -o /tmp/nova_run.o /tmp/nova_run.s && \
	$(LD) -o /tmp/nova_run /tmp/nova_run.o && \
	/tmp/nova_run

# Show compiler stats
stats:
	@echo "=== Nova Codebase Stats ==="
	@echo -n "Bootstrap assembly: "; wc -l boot/nova_boot.s | awk '{print $$1 " lines"}'
	@echo -n "Compiler (Nova):    "; cat $(COMPILER_SRC) | wc -l | awk '{print $$0 " lines"}'
	@echo -n "Core types:         "; cat src/core/*.nova 2>/dev/null | wc -l | awk '{print $$0 " lines"}'
	@echo -n "Mind systems:       "; cat src/mind/*.nova 2>/dev/null | wc -l | awk '{print $$0 " lines"}'
	@echo -n "Runtime:            "; cat src/runtime/*.nova 2>/dev/null | wc -l | awk '{print $$0 " lines"}'
	@echo -n "Tests:              "; cat tests/*.nova 2>/dev/null | wc -l | awk '{print $$0 " lines"}'
	@echo -n "Examples:           "; cat examples/*.nova 2>/dev/null | wc -l | awk '{print $$0 " lines"}'
	@echo -n "Total Nova:         "; find . -name "*.nova" | xargs cat | wc -l | awk '{print $$0 " lines"}'

clean:
	rm -rf bin/ /tmp/nova_*.s /tmp/nova_*.o /tmp/t /tmp/t.s /tmp/t.o /tmp/ex_*

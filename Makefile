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

.PHONY: all clean test bootstrap stage1 self-host test-all examples cross-macos cross-windows smoke-windows smoke-macos smoke-wasm smoke-gpu bench-simd

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

# Cross-compile for Windows (generates .exe; requires mingw-w64 toolchain)
cross-windows: bin/nova
	@mkdir -p bin
	cat $(COMPILER_SRC) > /tmp/nova_combined.nova
	bin/nova /tmp/nova_combined.nova --target=windows -o bin/nova_windows.s
	x86_64-w64-mingw32-as -o bin/nova_windows.o bin/nova_windows.s
	x86_64-w64-mingw32-ld -o bin/nova.exe bin/nova_windows.o -L/usr/x86_64-w64-mingw32/lib -lkernel32 -lws2_32
	@echo "Windows executable written to bin/nova.exe"
	@echo "Transfer to Windows and run: nova.exe <file.nova> -o output.s"

# Build the Windows smoke test (hello-world + concat + int_to_str + file IO).
# Set WINE_OK=1 to also execute it under wine (requires a working wine + writable
# XDG_RUNTIME_DIR -- the target sets one if missing).
smoke-windows: bin/nova examples/hello_win32.nova
	@mkdir -p bin
	bin/nova examples/hello_win32.nova --target=windows -o /tmp/hello_win32.s
	x86_64-w64-mingw32-as -o /tmp/hello_win32.o /tmp/hello_win32.s
	x86_64-w64-mingw32-ld -o bin/hello_win32.exe /tmp/hello_win32.o \
		-L/usr/x86_64-w64-mingw32/lib -lkernel32 -lws2_32
	@echo "Windows smoke test written to bin/hello_win32.exe"
	@file bin/hello_win32.exe
	@if [ "$$WINE_OK" = "1" ]; then \
		echo "--- wine bin/hello_win32.exe ---"; \
		mkdir -p $${XDG_RUNTIME_DIR:-/tmp/xdg-runtime}; \
		chmod 700 $${XDG_RUNTIME_DIR:-/tmp/xdg-runtime}; \
		XDG_RUNTIME_DIR=$${XDG_RUNTIME_DIR:-/tmp/xdg-runtime} \
			WINEDEBUG=-all wine bin/hello_win32.exe; \
		echo "wine exit=$$?"; \
	else \
		echo "(skipping wine run; set WINE_OK=1 to enable)"; \
	fi

# Build the macOS smoke test (hello-world + concat + int_to_str).
# Pipeline: NOVA --target=macos emits GAS-syntax x86-64 asm with __TEXT/__DATA
# Mach-O sections and Darwin BSD syscall numbers (write=0x2000004, exit=0x2000001).
# This target assembles with `clang -target x86_64-apple-darwin -c` (LLVM MC
# parser, accepts the same .intel_syntax noprefix dialect) and links with
# `ld64.lld` to produce a real Mach-O 64-bit executable.
#
# The resulting binary CANNOT run on Linux (it needs the Darwin kernel for the
# syscall path), but `file` and `llvm-objdump` verify the format. Run it on a
# real macOS host or under an XNU emulator.
#
# Skips cleanly if the cross-toolchain isn't installed. On macOS proper,
# clang/as/ld are all in the system toolchain; on Linux you need:
#   apt install lld llvm clang   (or `brew install llvm lld` on macOS hosts
#                                 that want the LLVM linker instead of system ld)
smoke-macos: bin/nova examples/hello_macos.nova
	@mkdir -p bin
	@if ! command -v clang >/dev/null 2>&1; then \
		echo "(skip: macos toolchain not available -- need clang with x86_64-apple-darwin support)"; \
		exit 0; \
	fi
	@if ! command -v ld64.lld-18 >/dev/null 2>&1 && ! command -v ld64.lld >/dev/null 2>&1; then \
		echo "(skip: macos toolchain not available -- need ld64.lld; apt install lld)"; \
		exit 0; \
	fi
	@LD64=$$(command -v ld64.lld-18 || command -v ld64.lld); \
	bin/nova examples/hello_macos.nova --target=macos -o /tmp/hello_macos.s && \
	clang -target x86_64-apple-darwin -c /tmp/hello_macos.s -o /tmp/hello_macos.o && \
	$$LD64 -arch x86_64 -platform_version macos 10.13 10.13 \
		-e _main -o bin/hello_macos /tmp/hello_macos.o
	@echo "macOS smoke test written to bin/hello_macos"
	@file bin/hello_macos
	@echo "(binary needs a real Darwin host to actually run; format verified above)"

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

# Build the WASM smoke test (hello-world via WASI fd_write + proc_exit).
# Pipeline: NOVA --target=wasm emits WebAssembly Text (WAT) referencing
# wasi_snapshot_preview1.{fd_write,proc_exit}. wat2wasm (from the `wabt`
# package: apt install wabt) finalizes to a real .wasm binary. Optionally
# runs under wasmtime (preferred) or node's --experimental-wasi-unstable-
# preview1 if WASM_OK=1 is set in the environment.
#
# Skips cleanly with a clear message if the toolchain isn't installed.
# See WASM_AUDIT.md for the full design + gap list.
smoke-wasm: bin/nova examples/hello_wasm.nova
	@mkdir -p bin
	@bin/nova examples/hello_wasm.nova --target=wasm -o bin/hello.wat
	@echo "WAT written to bin/hello.wat ($$(wc -l < bin/hello.wat) lines)"
	@if ! command -v wat2wasm >/dev/null 2>&1; then \
		echo "(skip: wasm toolchain not available -- need wat2wasm; apt install wabt)"; \
		echo "(bin/hello.wat is still produced and inspectable.)"; \
		exit 0; \
	fi
	@wat2wasm bin/hello.wat -o bin/hello.wasm
	@echo "WASM written to bin/hello.wasm"
	@file bin/hello.wasm
	@if [ "$$WASM_OK" = "1" ]; then \
		if command -v wasmtime >/dev/null 2>&1; then \
			echo "--- wasmtime bin/hello.wasm ---"; \
			wasmtime bin/hello.wasm; \
			echo "wasmtime exit=$$?"; \
		elif command -v node >/dev/null 2>&1; then \
			echo "--- node --experimental-wasi-unstable-preview1 bin/hello.wasm ---"; \
			node --experimental-wasi-unstable-preview1 -e "\
				const {WASI} = require('node:wasi'); const fs = require('node:fs'); \
				const wasi = new WASI({version: 'preview1', args: [], env: {}, preopens: {}}); \
				const wasm = fs.readFileSync('bin/hello.wasm'); \
				WebAssembly.instantiate(wasm, {wasi_snapshot_preview1: wasi.wasiImport}).then(({instance}) => { \
				  wasi.start(instance); \
				}).catch(e => { console.error('WASM error:', e.message); process.exit(1); });" 2>&1 | grep -v "ExperimentalWarning\|trace-warnings"; \
		else \
			echo "(WASM_OK=1 set but no wasmtime or node available)"; \
		fi; \
	else \
		echo "(skipping wasm run; set WASM_OK=1 to enable via wasmtime/node)"; \
	fi

# Compile a .nova file to a binary
%.out: %.nova bin/nova
	bin/nova $< -o /tmp/$*.s
	$(AS) -o /tmp/$*.o /tmp/$*.s
	$(LD) -o $@ /tmp/$*.o

# Compile a .nova file with FFI support (links libc + libdl)
ffi: bin/nova
	@if [ -z "$(FILE)" ]; then echo "Usage: make ffi FILE=path/to/file.nova"; exit 1; fi
	@bin/nova $(FILE) --link-libc -o /tmp/nova_ffi.s && \
	$(AS) -o /tmp/nova_ffi.o /tmp/nova_ffi.s && \
	gcc -o /tmp/nova_ffi /tmp/nova_ffi.o -ldl -no-pie && \
	/tmp/nova_ffi

# Build + run the AVX2 dot-product benchmark.
# Linux x86-64 only. Prints scalar ns, SIMD ns, speedup ratio.
# Documented in SIMD_AUDIT.md.
bench-simd: bin/nova examples/bench_dot_i32.nova
	@bin/nova examples/bench_dot_i32.nova -o /tmp/bench_dot_i32.s
	@$(AS) -o /tmp/bench_dot_i32.o /tmp/bench_dot_i32.s
	@$(LD) -o /tmp/bench_dot_i32 /tmp/bench_dot_i32.o
	@/tmp/bench_dot_i32

# Build + run the GPU vector-add smoke test (WGSL via wgpu).
# Pipeline:
#   1. examples/gpu_vector_add.nova emits the WGSL shader + a small
#      "key=value" config file (bin/gpu_vector_add.{wgsl,cfg}).
#   2. scripts/wgpu_dispatch is a standalone Rust binary that uses
#      the `wgpu` crate to dispatch the compute kernel, validate
#      against a CPU baseline, and print timings.
#
# Skips cleanly with a clear message if `cargo` is missing OR if
# `wgpu` cannot find a usable adapter on this host (the dispatcher
# exits 0 with `(skip: no wgpu adapter available)` in that case).
#
# See GPU_AUDIT.md for the full design + alternatives (CUDA, OpenCL).
smoke-gpu: bin/nova examples/gpu_vector_add.nova
	@mkdir -p bin
	@bin/nova examples/gpu_vector_add.nova -o /tmp/gpu_vector_add.s
	@$(AS) -o /tmp/gpu_vector_add.o /tmp/gpu_vector_add.s
	@$(LD) -o /tmp/gpu_vector_add /tmp/gpu_vector_add.o
	@/tmp/gpu_vector_add
	@if ! command -v cargo >/dev/null 2>&1; then \
		echo "(skip: rust toolchain / wgpu unavailable -- cargo not found)"; \
	elif ! ( cd scripts/wgpu_dispatch && cargo build --release 2>&1 ); then \
		echo "(skip: rust toolchain / wgpu unavailable -- cargo build failed)"; \
	elif [ ! -x scripts/wgpu_dispatch/target/release/wgpu_dispatch ]; then \
		echo "(skip: rust toolchain / wgpu unavailable -- dispatcher binary missing)"; \
	else \
		echo "--- dispatching ---"; \
		./scripts/wgpu_dispatch/target/release/wgpu_dispatch \
			bin/gpu_vector_add.wgsl bin/gpu_vector_add.cfg; \
	fi

# Mobile native (iOS / Android ARM64). Both targets ship reference hand-
# written ARM64 assembly that demonstrates exactly what NOVA's --target=arm64
# codegen SHOULD emit; the toolchain end-to-end is validated, the codegen
# gap is documented in MOBILE_AUDIT.md.
#
# NOVA's cg_target == 4 is currently a single-stub `_start: exit` and crashes
# on real input -- the la_lower_function ARM64 lowering is the next codegen
# task. These targets therefore assemble the hand-written REFERENCE .s files
# (examples/hello_arm64_{android,ios}_reference.s) instead of calling
# `bin/nova --target=arm64`, until the codegen is wired.
#
# Skips cleanly if clang's AArch64 backend isn't available.
smoke-mobile-android: bin/nova examples/hello_arm64_android_reference.s
	@mkdir -p bin
	@if ! command -v clang >/dev/null 2>&1; then \
		echo "(skip: mobile cross-toolchain not available -- need clang)"; \
		exit 0; \
	fi
	@if ! clang --print-targets 2>&1 | grep -q aarch64; then \
		echo "(skip: clang does not support aarch64 target)"; \
		exit 0; \
	fi
	@clang -target aarch64-linux-android30 -c \
		examples/hello_arm64_android_reference.s -o bin/hello_android.o
	@echo "Android ARM64 reference object written to bin/hello_android.o"
	@file bin/hello_android.o
	@echo "(this .o links into an NDK .so via System.loadLibrary; see MOBILE_AUDIT.md)"

smoke-mobile-ios: bin/nova examples/hello_arm64_ios_reference.s
	@mkdir -p bin
	@if ! command -v clang >/dev/null 2>&1; then \
		echo "(skip: mobile cross-toolchain not available -- need clang)"; \
		exit 0; \
	fi
	@if ! clang --print-targets 2>&1 | grep -q arm64; then \
		echo "(skip: clang does not support arm64 target)"; \
		exit 0; \
	fi
	@clang -target arm64-apple-ios13.0 -c \
		examples/hello_arm64_ios_reference.s -o bin/hello_ios.o
	@echo "iOS ARM64 reference object written to bin/hello_ios.o"
	@file bin/hello_ios.o
	@echo "(this .o links into an Xcode .app bundle via bridging headers; see MOBILE_AUDIT.md)"

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

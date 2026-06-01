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

.PHONY: all clean test bootstrap stage1 self-host test-all examples cross-macos cross-windows cross-winarm64 smoke-windows smoke-winarm64 smoke-macos smoke-wasm smoke-wasm-file smoke-wasi-preopens smoke-gpu smoke-dwarf bench-simd bench-int-safe hello hello-windows hello-windows-arm64 hello-macos hello-wasm hello-arm64-linux

all: bin/nova

# Step 1: Build the assembly bootstrap
bootstrap: $(BOOT)

$(BOOT): boot/nova_boot.s
	$(AS) -o boot/nova_boot.o boot/nova_boot.s
	$(LD) -o $(BOOT) boot/nova_boot.o

# Step 2: Use bootstrap to compile Nova compiler.
#
# We rebuild twice: once via the bootstrap (stage 1, OLD runtime
# appended by boot/nova_boot.s) and a second time via that stage-1 binary
# (stage 2, NEW runtime emitted by the current src/compiler/codegen.nova
# gen_runtime). bin/nova IS the stage-2 binary so its INTERNAL smart-op
# helpers (_nova_check_rdi/_rsi etc.) reflect the current source — this
# matters for compiling tests that lex large hex literals like
# test_ptr_threshold_fix.nova, which would crash inside the bootstrap's
# old `cmp rdi, 0x100000; jge .mul_ptr` heuristic.
bin/nova: $(BOOT) $(COMPILER_SRC)
	@mkdir -p bin
	cat $(COMPILER_SRC) > /tmp/nova_combined.nova
	$(BOOT) /tmp/nova_combined.nova /tmp/nova_combined.nova -o /tmp/nova_stage1.s
	$(AS) -o /tmp/nova_stage1.o /tmp/nova_stage1.s
	$(LD) -o /tmp/nova_stage1 /tmp/nova_stage1.o
	/tmp/nova_stage1 /tmp/nova_combined.nova -o /tmp/nova_stage2.s
	$(AS) -o /tmp/nova_stage2.o /tmp/nova_stage2.s
	$(LD) -o bin/nova /tmp/nova_stage2.o

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
# msvcrt is now part of the link line so the new signal()/raise() shims
# (used by _nova_signal_install / _nova_raise_sig on the Windows target)
# resolve at link time. See WIN32_AUDIT.md.
cross-windows: bin/nova
	@mkdir -p bin
	cat $(COMPILER_SRC) > /tmp/nova_combined.nova
	bin/nova /tmp/nova_combined.nova --target=windows -o bin/nova_windows.s
	x86_64-w64-mingw32-as -o bin/nova_windows.o bin/nova_windows.s
	x86_64-w64-mingw32-ld -o bin/nova.exe bin/nova_windows.o -L/usr/x86_64-w64-mingw32/lib -lkernel32 -lws2_32 -lmsvcrt -lbcrypt
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
		-L/usr/x86_64-w64-mingw32/lib -lkernel32 -lws2_32 -lmsvcrt -lbcrypt
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
	@echo ""
	@echo "--- secure_random.exe (BCryptGenRandom roundtrip) ---"
	@bin/nova tests/test_secure_random.nova --target=windows -o /tmp/secure_random.s
	@x86_64-w64-mingw32-as -o /tmp/secure_random.o /tmp/secure_random.s 2>&1 | grep -v "Warning: end of file" || true
	@x86_64-w64-mingw32-ld -o bin/secure_random.exe /tmp/secure_random.o \
		-L/usr/x86_64-w64-mingw32/lib -lkernel32 -lws2_32 -lmsvcrt -lbcrypt
	@file bin/secure_random.exe
	@if [ "$$WINE_OK" = "1" ]; then \
		echo "--- wine bin/secure_random.exe ---"; \
		mkdir -p $${XDG_RUNTIME_DIR:-/tmp/xdg-runtime}; \
		chmod 700 $${XDG_RUNTIME_DIR:-/tmp/xdg-runtime}; \
		XDG_RUNTIME_DIR=$${XDG_RUNTIME_DIR:-/tmp/xdg-runtime} \
			WINEDEBUG=-all wine bin/secure_random.exe 2>&1 | grep -v "wine: configuration\|^$$" | head -10; \
		echo "wine secure_random exit=$$?"; \
	fi

# Build the Windows ARM64 (PE32+ AArch64) smoke test.
#
# Pipeline:
#   1. NOVA --target=windows-arm64 emits ARM64 GAS assembly with PE
#      section directives (.section .text,"xr", .section .rdata,"dr")
#      and .extern __imp_<API> declarations for the IAT-resolved
#      KERNEL32.DLL + BCRYPT.DLL imports.
#   2. clang -target aarch64-windows-gnu -c assembles to an Aarch64
#      COFF object file.
#   3. llvm-dlltool -m arm64 fabricates ARM64 import libs from .def
#      files at build time (no upstream mingw-w64 aarch64 import lib
#      package exists on Debian/Ubuntu).
#   4. lld-link /machine:arm64 produces a PE32+ ARM64 executable.
#
# Verification on the Linux host (cannot execute — requires ARM-Windows
# tester for runtime confirmation):
#   * `file` reports "PE32+ executable (console) Aarch64, for MS Windows"
#   * llvm-readobj reports IMAGE_FILE_MACHINE_ARM64 (0xAA64) in the
#     COFF header at signature+4
#   * llvm-objdump -p reports KERNEL32.DLL + BCRYPT.DLL in the import
#     directory.
#
# Skips cleanly if clang or lld-link are missing.
smoke-winarm64: bin/nova examples/hello.nova examples/hello_secure_random_winarm64.nova
	@mkdir -p bin
	@if ! command -v clang >/dev/null 2>&1; then \
		echo "(skip: winarm64 toolchain not available -- need clang with aarch64-windows-gnu)"; \
		exit 0; \
	fi
	@if ! command -v lld-link >/dev/null 2>&1; then \
		echo "(skip: winarm64 toolchain not available -- need lld-link; apt install lld)"; \
		exit 0; \
	fi
	@if ! command -v llvm-dlltool >/dev/null 2>&1; then \
		echo "(skip: winarm64 toolchain not available -- need llvm-dlltool; apt install llvm)"; \
		exit 0; \
	fi
	@printf 'LIBRARY KERNEL32.DLL\nEXPORTS\nExitProcess\nGetStdHandle\nWriteFile\n' > /tmp/winarm64_k32.def
	@printf 'LIBRARY BCRYPT.DLL\nEXPORTS\nBCryptGenRandom\n' > /tmp/winarm64_bcrypt.def
	@llvm-dlltool -m arm64 -d /tmp/winarm64_k32.def -l /tmp/libkernel32-arm64.a
	@llvm-dlltool -m arm64 -d /tmp/winarm64_bcrypt.def -l /tmp/libbcrypt-arm64.a
	@echo "--- hello_winarm64.exe (PE32+ ARM64 + KERNEL32 imports) ---"
	@bin/nova examples/hello.nova --target=windows-arm64 -o /tmp/hello_winarm64.s
	@clang -target aarch64-windows-gnu -c /tmp/hello_winarm64.s -o /tmp/hello_winarm64.o
	@lld-link /machine:arm64 /subsystem:console /entry:mainCRTStartup \
		/out:bin/hello_winarm64.exe \
		/tmp/hello_winarm64.o /tmp/libkernel32-arm64.a /tmp/libbcrypt-arm64.a
	@file bin/hello_winarm64.exe
	@echo "--- COFF machine type ---"
	@llvm-readobj --file-headers bin/hello_winarm64.exe | grep -E "Machine:" | head -1
	@echo "--- Import directory ---"
	@llvm-objdump -p bin/hello_winarm64.exe | awk '/The Import Tables:/,/^Library/' | head -30
	@echo ""
	@echo "--- secure_random_winarm64.exe (PE32+ ARM64 + BCRYPT.dll) ---"
	@bin/nova examples/hello_secure_random_winarm64.nova --target=windows-arm64 -o /tmp/sr_winarm64.s
	@clang -target aarch64-windows-gnu -c /tmp/sr_winarm64.s -o /tmp/sr_winarm64.o
	@lld-link /machine:arm64 /subsystem:console /entry:mainCRTStartup \
		/out:bin/secure_random_winarm64.exe \
		/tmp/sr_winarm64.o /tmp/libkernel32-arm64.a /tmp/libbcrypt-arm64.a
	@file bin/secure_random_winarm64.exe
	@echo "--- Import directory ---"
	@llvm-objdump -p bin/secure_random_winarm64.exe | awk '/The Import Tables:/,/^Library/' | head -30
	@echo "(binary needs a real ARM-Windows host to actually run; format verified above)"

# Cross-compile for Windows ARM64 — convenience alias for smoke-winarm64.
cross-winarm64: smoke-winarm64

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
	@echo ""
	@echo "--- secure_random macOS (BSD getentropy syscall #500) ---"
	@LD64=$$(command -v ld64.lld-18 || command -v ld64.lld); \
	bin/nova tests/test_secure_random.nova --target=macos -o /tmp/secure_random_macos.s && \
	clang -target x86_64-apple-darwin -c /tmp/secure_random_macos.s -o /tmp/secure_random_macos.o && \
	$$LD64 -arch x86_64 -platform_version macos 10.13 10.13 \
		-e _main -o bin/secure_random_macos /tmp/secure_random_macos.o
	@file bin/secure_random_macos
	@echo "(BSD syscall 0x2000000 + 500 emitted; needs Darwin host to actually run)"

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
	@echo ""
	@echo "--- secure_random WASM (wasi_snapshot_preview1.random_get import) ---"
	@bin/nova examples/hello_secure_random_wasm.nova --target=wasm -o /tmp/hsr.wat
	@if command -v wat2wasm >/dev/null 2>&1; then \
		wat2wasm /tmp/hsr.wat -o bin/hello_secure_random.wasm && \
		echo "secure_random.wasm built ($$(stat -c%s bin/hello_secure_random.wasm) bytes)"; \
		if [ "$$WASM_OK" = "1" ] && command -v node >/dev/null 2>&1; then \
			node --experimental-wasi-unstable-preview1 -e "\
				const {WASI} = require('node:wasi'); const fs = require('node:fs'); \
				const wasi = new WASI({version: 'preview1', args: [], env: {}, preopens: {}}); \
				const wasm = fs.readFileSync('bin/hello_secure_random.wasm'); \
				WebAssembly.instantiate(wasm, {wasi_snapshot_preview1: wasi.wasiImport}).then(({instance}) => { \
				  wasi.start(instance); \
				}).catch(e => { console.error('WASM error:', e.message); process.exit(1); });" 2>&1 | grep -v "ExperimentalWarning\|trace-warnings"; \
		fi; \
	else \
		echo "(skip: wat2wasm not present; .wat still produced)"; \
	fi

# Build + run the WASM file I/O round-trip smoke test.
# Pipeline:
#   1. NOVA compiles examples/file_wasm.nova -> bin/file_wasm.wat with
#      --target=wasm. The .wat imports wasi_snapshot_preview1.{path_open,
#      fd_read, fd_write, fd_close, fd_seek, proc_exit}.
#   2. wat2wasm finalizes to bin/file_wasm.wasm.
#   3. node --experimental-wasi-unstable-preview1 runs it with
#      preopens={'/tmp':'/tmp'} so the WASM sandbox sees /tmp as dirfd 3.
#   4. The program writes "nova" to /tmp/out.txt, reads it back, and
#      asserts the round-trip succeeded. Exit 0 = pass, 1 = fail.
#
# Skips cleanly with a clear message if wat2wasm or node is missing.
# See WASM_AUDIT.md for the full WASI surface.
smoke-wasm-file: bin/nova examples/file_wasm.nova
	@mkdir -p bin
	@bin/nova examples/file_wasm.nova --target=wasm -o bin/file_wasm.wat
	@echo "WAT written to bin/file_wasm.wat ($$(wc -l < bin/file_wasm.wat) lines)"
	@if ! command -v wat2wasm >/dev/null 2>&1; then \
		echo "(skip: wasm toolchain not available -- need wat2wasm; apt install wabt)"; \
		exit 0; \
	fi
	@wat2wasm bin/file_wasm.wat -o bin/file_wasm.wasm
	@echo "WASM written to bin/file_wasm.wasm"
	@if ! command -v node >/dev/null 2>&1; then \
		echo "(skip: node not available -- file I/O round-trip needs node WASI)"; \
		exit 0; \
	fi
	@rm -f /tmp/out.txt
	@echo "--- node --experimental-wasi-unstable-preview1 (preopens=/tmp) ---"
	@node --experimental-wasi-unstable-preview1 -e "\
		const {WASI} = require('node:wasi'); const fs = require('node:fs'); \
		const wasi = new WASI({version: 'preview1', args: [], env: {}, preopens: {'/tmp': '/tmp'}}); \
		const wasm = fs.readFileSync('bin/file_wasm.wasm'); \
		WebAssembly.instantiate(wasm, {wasi_snapshot_preview1: wasi.wasiImport}).then(({instance}) => { \
		  wasi.start(instance); \
		}).catch(e => { console.error('WASM error:', e.message); process.exit(1); });" 2>&1 \
		| grep -v "ExperimentalWarning\|trace-warnings"
	@if [ -f /tmp/out.txt ] && [ "$$(cat /tmp/out.txt)" = "nova" ]; then \
		echo "--- /tmp/out.txt contents verified: nova ---"; \
	else \
		echo "FAIL: /tmp/out.txt missing or wrong content"; \
		[ -f /tmp/out.txt ] && cat /tmp/out.txt; \
		exit 1; \
	fi

# WASI preopens / filesystem smoke test (R8A).
#
# Closes the WASM serverless deployment gap by validating that a NOVA
# program can drive the full wasi_snapshot_preview1 filesystem surface
# (path_open, fd_read, fd_write, fd_close, fd_seek, path_filestat_get
# plus args_*/environ_*) end-to-end under wasmtime --dir=/tmp.
#
# Two layers under test:
#   1. examples/file_wasm.nova  -- high-level read_file/write_file
#      (covered by smoke-wasm-file above). Unchanged contract.
#   2. examples/wasi_file_roundtrip.nova -- low-level wasi_open,
#      wasi_read, wasi_write, wasi_seek, wasi_close, wasi_filestat
#      (R8A new builtins). This target exercises that layer.
#
# Pipeline:
#   * NOVA --target=wasm emits a .wat with 12 wasi_snapshot_preview1
#     imports (fd_write, fd_read, fd_close, fd_seek, path_open,
#     path_filestat_get, args_*, environ_*, random_get, proc_exit).
#   * wat2wasm finalizes to .wasm.
#   * wasmtime --dir=/tmp runs it with the host /tmp mounted as the
#     sandbox's first preopen (dirfd 3). The program creates
#     /tmp/wasi_rt.txt, writes "hello wasi", stats it, re-opens,
#     reads it back, and verifies byte-for-byte.
#   * wasm-objdump confirms every expected import is present.
#
# Skips cleanly with a clear message if wat2wasm or wasmtime is
# missing. See tests/test_wasi_preopens.sh for the full script.
smoke-wasi-preopens: bin/nova examples/wasi_file_roundtrip.nova
	@bash tests/test_wasi_preopens.sh

# Build the DWARF .debug_line smoke test.
# Compiles examples/hello_dwarf.nova to bin/hello_dwarf (Linux ELF), then
# verifies:
#   1. objdump --dwarf=decodedline prints a real PC->line table
#   2. gdb resolves `b main` to the source line in hello_dwarf.nova
#
# This is the MVP DWARF coverage: .debug_line only (no .debug_info /
# variable tracking / type info). Windows PE CodeView and macOS Mach-O
# DWARF are documented as deferred — see DWARF_AUDIT.md.
#
# Skips cleanly if `objdump` or `gdb` aren't present.
smoke-dwarf: bin/nova examples/hello_dwarf.nova
	@mkdir -p bin
	@bin/nova examples/hello_dwarf.nova -o /tmp/hello_dwarf.s
	@$(AS) -o /tmp/hello_dwarf.o /tmp/hello_dwarf.s
	@$(LD) -o bin/hello_dwarf /tmp/hello_dwarf.o
	@echo "Linux ELF binary: bin/hello_dwarf"
	@file bin/hello_dwarf
	@echo ""
	@if ! command -v objdump >/dev/null 2>&1; then \
		echo "(skip: objdump not installed -- apt install binutils)"; \
		exit 0; \
	fi
	@echo "--- objdump --dwarf=decodedline bin/hello_dwarf ---"
	@objdump --dwarf=decodedline bin/hello_dwarf | sed -n '1,30p'
	@if ! objdump --dwarf=decodedline bin/hello_dwarf | grep -q hello_dwarf.nova; then \
		echo "FAIL: .debug_line section missing or empty"; exit 1; \
	fi
	@echo ""
	@if ! command -v gdb >/dev/null 2>&1; then \
		echo "(skip: gdb not installed)"; exit 0; \
	fi
	@echo "--- gdb b main / r / where ---"
	@gdb bin/hello_dwarf -ex 'b main' -ex 'r' -ex 'where' -ex 'c' -ex 'q' -batch 2>&1 | tail -8
	@echo ""
	@echo "=== DWARF smoke PASSED ==="

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

# Build + run the int_* scalar-builtin microbench.
# Compares NOVA's smart `+` / `*` (which dispatch through PTR_THRESHOLD)
# against the `int_add` / `int_mul` / ... scalar builtins. Also exercises
# correctness above the smart-op threshold (where `*` would crash).
# Documented in NOVA_BUG_THRESHOLD.md.
bench-int-safe: bin/nova examples/bench_int_safe.nova
	@bin/nova examples/bench_int_safe.nova -o /tmp/bench_int_safe.s
	@$(AS) -o /tmp/bench_int_safe.o /tmp/bench_int_safe.s
	@$(LD) -o /tmp/bench_int_safe /tmp/bench_int_safe.o
	@/tmp/bench_int_safe

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

# Mobile native (iOS / Android ARM64).
#
# Android: NOVA's --target=arm64 backend now compiles hello_arm64.nova
# end-to-end (see codegen.nova arm64_gen_program). The smoke assembles
# the NOVA-emitted .s instead of the hand-written reference, proving the
# codegen path is real. Also re-asserts the reference path to catch
# toolchain regressions.
#
# iOS: still uses the hand-written reference .s (NOVA's adrp/lo12
# relocations are GAS/Android-flavoured; iOS Mach-O wants @PAGE/@PAGEOFF
# which is not yet emitted). Tracked in MOBILE_AUDIT.md.
#
# Skips cleanly if clang's AArch64 backend isn't available.
smoke-mobile-android: bin/nova examples/hello_arm64.nova examples/hello_arm64_android_reference.s
	@mkdir -p bin
	@if ! command -v clang >/dev/null 2>&1; then \
		echo "(skip: mobile cross-toolchain not available -- need clang)"; \
		exit 0; \
	fi
	@if ! clang --print-targets 2>&1 | grep -q aarch64; then \
		echo "(skip: clang does not support aarch64 target)"; \
		exit 0; \
	fi
	@echo "--- NOVA --target=arm64 -> Android ARM64 ELF ---"
	@bin/nova examples/hello_arm64.nova --target=arm64 -o /tmp/hello_arm64.s
	@clang -target aarch64-linux-android30 -c /tmp/hello_arm64.s -o bin/hello_android.o
	@echo "Android ARM64 object written to bin/hello_android.o (compiled by NOVA)"
	@file bin/hello_android.o
	@echo ""
	@echo "--- reference .s -> Android ARM64 ELF (sanity) ---"
	@clang -target aarch64-linux-android30 -c \
		examples/hello_arm64_android_reference.s -o /tmp/hello_android_ref.o
	@file /tmp/hello_android_ref.o
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

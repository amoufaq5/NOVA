# Getting Started with NOVA

This guide walks new users from "I just cloned the NOVA repo" to "I
can compile a NOVA program and verify the self-hosting invariant."

NOVA is a self-hosting compiled language that produces native x86-64
machine code via direct Linux / Windows syscalls. The compiler is
written in NOVA and bootstraps from a handwritten x86-64 assembly
seed.

## Section 1 -- Requirements

| Host                  | Status             | Notes                                       |
|-----------------------|--------------------|---------------------------------------------|
| Linux x86_64 (native) | **Recommended**    | First-class. `make` + `make self-host`.     |
| Windows               | Supported via WSL2 | NOVA emits Linux ELF here; Windows PE32+    |
|                       |                    | is a separate target tracked in WIN32_AUDIT.|
| macOS Intel           | Supported          | Native macOS x86_64 target exists; see      |
|                       |                    | MACOS_AUDIT.md.                             |
| macOS Apple Silicon   | Supported with caveats | ARM64 codegen pass exists (v4.2); some  |
|                       |                    | features still x86_64-only.                 |

Required tools:
  - `gcc` or `clang` (only for the assembly seed's link step).
  - `make`.
  - GNU `as` + `ld` from binutils.
  - `git`.

For IDE setup, you additionally need:
  - Python 3.10+ (for `nova-lsp` and `nova-dap`).
  - `gdb` 9.0+ (for `nova-dap`).
  - `objdump` (for `nova-dap` disassembly view).
  - Node.js (for the tree-sitter grammar build).

## Section 2 -- Install on Linux native

### 2.1 Install system packages

Ubuntu / Debian:

```bash
sudo apt-get update
sudo apt-get install -y build-essential binutils gdb git
```

Fedora / RHEL:

```bash
sudo dnf install -y gcc make binutils gdb git
```

Arch:

```bash
sudo pacman -S base-devel binutils gdb git
```

### 2.2 Clone the repo

```bash
mkdir -p ~/src && cd ~/src
git clone https://github.com/amoufaq5/NOVA.git
cd NOVA
```

### 2.3 Build the compiler

```bash
make
```

This runs the three-stage bootstrap:
  - stage1: handwritten x86-64 assembly seed (106,045 lines).
  - stage2: stage1 compiles `compiler/main.nova` to `stage2.s`.
  - stage3: stage2 compiles `compiler/main.nova` to `stage3.s`.

The final binary lands at `bin/nova`. Verify with:

```bash
./bin/nova --version
```

### 2.4 Verify self-hosting invariant

```bash
make self-host
```

This asserts `stage2.s == stage3.s` byte-identical. If this passes,
the compiler is self-consistent. If it fails, the diff between the
two files localises the bug -- see ADR 0002 for the rationale.

### 2.5 Compile and run a NOVA program

```bash
make run FILE=examples/hello.nova
```

Or step by step:

```bash
./bin/nova compile examples/hello.nova -o /tmp/hello
/tmp/hello
```

### 2.6 Run the test suite

```bash
make test-all
```

You should see 176 pass + 6 skip (the 6 skips are documented
platform-specific tests like the Windows PE32+ smoke test on a
non-Windows host). The exact counts shift with each round; check
`NEXT_SESSION.md` for the current baseline.

## Section 3 -- Install on Windows (WSL2)

NOVA has a Windows PE32+ codegen target (see `WIN32_AUDIT.md`), but
the development workflow targets Linux ELF. Use WSL2 as the
development host.

### 3.1 Install WSL2

From an Administrator PowerShell:

```powershell
wsl --install -d Ubuntu
```

Reboot when prompted. Ubuntu launches on first run; create your
Linux user.

### 3.2 Follow the Linux native steps inside WSL2

```bash
sudo apt-get update && sudo apt-get install -y build-essential binutils gdb git
mkdir -p ~/src && cd ~/src
git clone https://github.com/amoufaq5/NOVA.git
cd NOVA && make && make self-host
make test-all
```

### 3.3 Cross-compile to Windows PE32+ from WSL2

```bash
./bin/nova compile examples/hello.nova --target win64 -o /tmp/hello.exe
# Run from PowerShell:
# .\hello.exe
```

See `WIN32_AUDIT.md` for the current Windows codegen status and the
ARM64 Windows path (`--target win-arm64`).

## Section 4 -- Install on macOS

### 4.1 macOS Intel (x86_64)

NOVA has a native macOS x86_64 target. See `MACOS_AUDIT.md`. Install
via Homebrew:

```bash
brew install binutils gdb
```

Note: macOS `as` and `ld` are LLVM-based; NOVA's Linux-shaped
assembly may need adjustment for the macOS Mach-O linker. The
`MACOS_AUDIT.md` document tracks the current status; some features
work natively, others need a Linux container.

For a guaranteed working development environment on macOS Intel,
use Docker:

```bash
docker run -it -v "$PWD:/work" ubuntu:24.04 bash
# Inside the container, follow Section 2.
```

### 4.2 macOS Apple Silicon (M1/M2/M3/M4)

The ARM64 codegen pass landed in v4.2 with NEON SIMD + ARM64 syscall
translation. Some features are still x86_64-only -- check
`MACOS_AUDIT.md` for the current matrix.

For x86_64 NOVA development on Apple Silicon, use Docker with
Rosetta:

```bash
softwareupdate --install-rosetta --agree-to-license
# Enable Rosetta emulation in Docker Desktop -> Settings -> General.
docker run --platform linux/amd64 -it -v "$PWD:/work" ubuntu:24.04 bash
```

Honest performance note: x86_64 emulation under Rosetta is 3-5x
slower than native; expect slower build + test cycles.

For native ARM64 NOVA development, build directly:

```bash
make TARGET=linux-arm64
```

ARM64-target features are tracked separately in `MACOS_AUDIT.md`.

## Section 5 -- IDE setup

After the compiler builds and `make self-host` passes, set up the
IDE per [`docs/IDE_SETUP.md`](IDE_SETUP.md). The short version:

```bash
python3 -m venv $HOME/.local/share/nova-tools-venv
source $HOME/.local/share/nova-tools-venv/bin/activate
pip install -e tools/nova-lsp
pip install -e tools/nova-dap
```

Then point VS Code at the venv's `nova-lsp` binary.

For tree-sitter syntax highlighting:

```bash
cd tools/tree-sitter-nova
npm install && npx tree-sitter generate && npx tree-sitter test
```

See `docs/IDE_SETUP.md` for the VS Code `.vscode/settings.json` and
`.vscode/launch.json` configuration.

## Section 6 -- Writing your first NOVA program

```nova
// examples/hello.nova
fn main() {
  print("hello from nova\n");
}
```

Compile + run:

```bash
./bin/nova compile examples/hello.nova -o /tmp/hello
/tmp/hello
```

Output:

```
hello from nova
```

For a richer example, see:
  - `examples/` (top-level) -- runnable programs.
  - `core/` -- canonical NOVA modules used by both the compiler and
    applications.
  - `docs/LANGUAGE_REFERENCE.md` -- the language reference.
  - `docs/STDLIB.md` -- the standard library reference.
  - `docs/USE_CASES.md` -- worked examples covering cognitive
    primitives, syscalls, FFI.

## Section 7 -- Verifying your setup

```bash
# Compiler built
./bin/nova --version

# Self-host invariant
make self-host

# Full test suite
make test-all

# Run an example
make run FILE=examples/hello.nova
```

If all four succeed, you're ready to contribute.

## Section 8 -- Where to go next

  - [`docs/IDE_SETUP.md`](IDE_SETUP.md) -- VS Code tree-sitter +
    LSP + DAP wiring.
  - [`docs/LANGUAGE_REFERENCE.md`](LANGUAGE_REFERENCE.md) -- learn
    the language.
  - [`docs/STDLIB.md`](STDLIB.md) -- standard library.
  - [`docs/USE_CASES.md`](USE_CASES.md) -- worked examples.
  - [`docs/COMPARISON.md`](COMPARISON.md) -- NOVA vs Rust / Go / C /
    Python.
  - [`docs/adr/`](adr/) -- architecture decisions:
    - `0001-nova-language-design.md` -- minimalism + what's absent.
    - `0002-self-hosting.md` -- the bootstrap path + invariant.
    - `0003-pattern-matching-evolution.md` -- how pattern matching
      grew across R17A -> R33C.
    - `0004-lsp-dap-tooling-strategy.md` -- why Python + tree-sitter
      for tooling.
    - `0005-static-slot-closure-lowering.md` -- R35C's closure
      design + R37+ migration plan.
  - `NEXT_SESSION.md` -- per-round detail of what shipped.
  - `ARCHITECTURE.md` -- system-level overview.
  - `INSTALL.md` -- the existing install document (this file is the
    narrative on-ramp; `INSTALL.md` has the reference matrix).

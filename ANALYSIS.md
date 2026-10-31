# NOVA — Project Analysis

A candid, repo-grounded assessment of the NOVA programming language: what it
actually is, what is verifiably built, where it is honestly still experimental,
how you can install it, and how it stacks up against neighbouring systems
languages. Claims below were checked against the source tree, `Makefile`,
`INSTALL.md`, and the packaging/release tooling.

---

## Known inconsistency: which version is NOVA?

There are **two different version numbers** in this repository, and they
disagree:

| Source | Value | Role |
|---|---|---|
| `README.md` (status table, "New in v4.2", codegen targets "v4.2") | **4.2.0** | Marketing / feature-history version |
| `VERSION` file (single source consumed by all packaging) | **0.1.0** | Release / packaging version |

**Why they differ.** The README's `4.x` numbering is a *feature-history*
narrative — it counts cumulative capability waves ("New in v4.0 / v4.1 / v4.2").
The `VERSION` file is the *release* number: it is the single source of truth for
every piece of build and distribution tooling.

**What consumes `VERSION` (verified):**

- `tools/release-dry-run.sh` reads `VERSION` and forms `TAG="v${VERSION}"` -> `v0.1.0`
- `scripts/build-deb.sh`, `scripts/build-pkg.sh`, `scripts/build-msi.sh`,
  `packaging/windows/build-msi.bat`, `packaging/homebrew/bump-formula.sh` all
  read `VERSION` to name their artifacts
- `tools/install.sh` defaults `LATEST_VERSION="v0.1.0"`
- `packaging/debian/changelog` hard-codes `nova (0.1.0-1)`
- `.github/workflows/release.yml` `workflow_dispatch` default tag is `v0.1.0`
- `INSTALL.md` states the release pipeline fires on a **`v0.1.0`** tag push and
  that `nova --version` should print `nova 0.1.0`

**Decision: leave `VERSION` at `0.1.0`.** Flipping it to `4.2.0` would
desynchronise the entire release pipeline — the dry-run and CI would build a
`v4.2.0` tarball while `install.sh`, the Debian changelog, the release-workflow
default, and the install docs all still expect `v0.1.0`. The `0.1.0` number
also honestly reflects maturity: this is a first, unpublished release.

**Recommendation.** Treat `0.1.0` as the canonical product version and reword
the README so its `4.x` labels read as *feature milestones* ("Capability wave
4.2") rather than a competing version string. Reconcile to a single number only
in tandem with a deliberate release-tooling bump, never as a silent edit.

---

## 1. Project analysis

### What it is

NOVA is a self-hosting, compiled programming language aimed at building AGI-style
systems through **Moment-Signal Computing**: a paradigm in which cognition is
expressed as signals flowing through specialised processing nodes. It compiles
straight to native x86-64 machine code using direct Linux/Windows syscalls —
**no libc, no garbage collector, no runtime interpreter**. The compiler is itself
written in NOVA and bootstrapped from a handwritten x86-64 assembly seed.

### Verified achievements

| Claim | Evidence in repo |
|---|---|
| Self-hosting fixed point | `make self-host` asserts `stage2.s == stage3.s` byte-for-byte (ADR 0002). |
| Large assembly bootstrap | `boot/nova_boot.s` is **106,045 lines** of x86-64 assembly. |
| Compiler written in NOVA | `src/compiler/` — lexer, parser, AST, IR, register allocator, x86-64 lowering, codegen (~16.5k lines per README). |
| Multi-target codegen | Linux x86-64, Windows x86-64 (PE32+), macOS x86-64, WASM/WASI, Linux ARM64, Windows ARM64 — exercised by `hello_*`/`hello_arm64*`/`hello_win32`/`hello_wasm` examples and cross-compile Make rules. |
| Editor/tooling stack | `tools/nova-lsp/` (pygls LSP), `tools/nova-dap/` (DAP over gdb-mi), `tools/tree-sitter-nova/` grammar, `tools/vscode-nova/` VSIX. |
| Cognitive primitives as language features | `mind {}`, `soul {}`, `system {}` blocks; seven flow operators (`~>`, `<~`, `=>>`, `<<~`, `~~>`, `<=>`, `|~>`); core types under `src/core/` (moment, signal, node, channel, path, belief, goal, soul). |
| Test suite | README reports 182 tests (176 pass, 6 skip); `tests/` tree is present. |

### Honest risks and caveats

- **Experimental maturity.** Despite the README's `4.x` labels, the release
  version is `0.1.0` and nothing has been published yet. Several features carry
  explicit boundary caveats in the README (e.g. floats: no transcendentals,
  no implicit int->float coercion, SysV mixed-arg float ABI not modelled — NOVA-to-NOVA only;
  tuples lower to tagged lists with AST-level typing only).
- **Zero ecosystem.** No package registry, no third-party libraries, no users
  beyond the project. `src/pkg/` exists but there is nothing to install from it.
- **Version inconsistency.** README says `4.2.0`; `VERSION`/packaging say
  `0.1.0` (see the "Known inconsistency" note above).
- **AGI claims are aspirational.** The cognitive primitives (`mind`, `soul`,
  beliefs, goals, imagination) are real *language constructs* and data
  structures, but "AGI" is a direction of intent, not a demonstrated
  capability. NOVA gives you ergonomic syntax for cognitive architectures; it
  does not ship a working general intelligence.
- **Single-architecture host reality.** The self-hosting fixed point and most
  tests target Linux x86-64; the other targets are codegen passes and reference
  examples rather than a fully self-hosted multi-platform toolchain.

---

## 2. Deployment / install options

`INSTALL.md` documents **seven install paths**. Only the first works today; the
rest are wired up but **gated on pushing the `v0.1.0` release tag**, which fires
`.github/workflows/release.yml` to build and attach the artifacts.

| # | Path | Mechanism | Readiness |
|---|---|---|---|
| 1 | Build from source | `git clone` + `make` (needs only `as`, `ld`, `make`, GCC) | **Ready today.** No prebuilt binary required. |
| 2 | `curl \| sh` one-liner | `tools/install.sh` pulls the matching tarball from GitHub Releases | Wired; **blocked on `v0.1.0` tag** firing `release.yml`. |
| 3 | Homebrew | `brew tap nova-lang/nova && brew install nova` (formula in `tools/Formula/nova.rb`) | Wired; **blocked** on tag + tap-repo publish. |
| 4 | Manual download | Per-platform tarball + `.sha256` from the Releases page | Wired; **blocked** on tag (no release assets yet). |
| 5 | Debian/Ubuntu `.deb` | `sudo apt install ./nova_0.1.0_amd64.deb` (`scripts/build-deb.sh`) | Buildable locally; **release artifact blocked** on tag. |
| 6 | macOS `.pkg` | `sudo installer -pkg nova-0.1.0.pkg -target /` (`scripts/build-pkg.sh`) | Buildable locally; unsigned until an Apple Developer ID cert is configured; **release artifact blocked** on tag. |
| 7 | Windows `.msi` | `msiexec /i nova-0.1.0.msi` (`scripts/build-msi.sh`, WiX 3.x) | Buildable locally; **release artifact blocked** on tag. |

**Bottom line:** paths 2-7 all assume GitHub Release assets that exist only
after the `v0.1.0` tag is pushed. `tools/release-dry-run.sh` lets maintainers
build the would-be artifacts locally without pushing anything. Until then,
**build from source is the one true path.**

---

## 3. NOVA vs other languages

A pragmatic comparison against neighbouring systems / AI-adjacent languages.
"Cognitive primitives" means first-class language constructs for cognition
(NOVA's `mind`/`soul`/signal model), not a library you import.

| Dimension | NOVA | C | Rust | Zig | Go | Mojo |
|---|---|---|---|---|---|---|
| Compiles to / no-libc | Native x86-64+, **no libc** (raw syscalls) | Native; libc-centric | Native; libc by default (no_std possible) | Native; libc optional | Native; own runtime, no libc | Native (MLIR/LLVM); libc-linked |
| Dependencies to build | `as`, `ld`, `make`, GCC only | C toolchain | rustc + cargo | zig toolchain | go toolchain | MAX/Mojo SDK (proprietary) |
| Self-hosting | **Yes — verified fixed point** | Yes (mature) | Yes | Yes | Yes | No (not yet) |
| Memory model | Arena allocator, manual/deterministic, no GC | Manual | Ownership + borrow checker | Manual + allocators | Garbage collected | Manual + value semantics |
| Safety | Minimal (experimental) | Minimal | **Strong, compile-time** | Moderate (safety-conscious) | Moderate (GC + race detector) | Moderate, evolving |
| Concurrency | Coroutines + signal scheduler; `sys_poll` | Threads/OS | async + threads, Send/Sync | async + threads | **Goroutines + channels (core strength)** | Parallel/SIMD focus |
| SIMD | SSE2 intrinsics, tensor/BLAS dispatch | Intrinsics/auto-vec | `std::simd`/intrinsics | Vectors built-in | Limited | **First-class SIMD/MLIR (core strength)** |
| AI / cognitive primitives | **`mind`/`soul`/`system` + flow operators built into the grammar** | None | None | None | None | AI-perf focused, but no cognitive constructs |
| Ecosystem | **None** | Vast | Large (crates.io) | Growing | Large | Small, proprietary |
| Tooling | LSP + DAP + tree-sitter + VSIX (in-repo) | Mature | Mature (rust-analyzer, cargo) | Good, improving | Mature | Improving |
| Maturity | **Experimental (`0.1.0`)** | Decades | Production | Pre-1.0 but solid | Production | Early, proprietary |

### Where NOVA is genuinely differentiated

- **No-libc self-hosting from an assembly seed.** Very few languages compile to
  native code through raw syscalls with zero C-library dependency *and* prove a
  byte-identical self-hosting fixed point. That combination is a real, verifiable
  engineering achievement.
- **Cognitive primitives as language features.** `mind {}`, `soul {}`,
  `system {}`, and the seven flow operators make cognitive-architecture wiring
  *syntax* rather than a framework. No mainstream systems language does this; it
  is NOVA's distinctive bet.

### Where NOVA cannot compete (yet)

- **Ecosystem.** C, Rust, and Go have decades of libraries, packages, and
  hiring pools. NOVA has zero third-party packages.
- **Maturity & stability.** At `0.1.0`, unpublished, with documented feature
  boundaries and a single primary host target, NOVA is not production-ready.
- **Compile-time safety vs Rust.** Rust's ownership/borrow checker is a category
  NOVA does not attempt; NOVA's arena model is deterministic but offers no
  comparable compile-time memory-safety guarantees.

**Net:** NOVA is best understood not as a Rust/Go competitor but as a research
language exploring one specific, well-executed idea — a dependency-free,
self-hosting native compiler whose grammar treats cognition as a first-class
construct. Its differentiation is real; its readiness is early.

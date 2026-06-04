# Nova

**A self-hosting compiled language for AGI through Moment-Signal Computing.**

Compiles to native x86-64 machine code. Zero dependencies. No libc. Direct Linux/Windows syscalls.

---

| | |
|---|---|
| **Status** | Self-hosting verified (`stage2.s == stage3.s`) |
| **Version** | 4.2.0 |
| **Bootstrap** | 106,045 lines of x86-64 assembly (self-compiled) |
| **Compiler** | 16,467 lines of Nova (lexer, parser, AST, IR, register allocator, x86-64 lowering, codegen) |
| **Core Types** | 5,306 lines (moment, signal, node, channel, path, similarity, soul, system, belief, goal, safety, imagination, concept) |
| **Mind Systems** | 2,800 lines (academic, experiential, emotion, memory, reasoning) |
| **Runtime** | 7,717 lines (syscall, alloc, string, io, scheduler, SIMD, tensor, BLAS, embedding, LLM, FFI, Python bridge, etc.) |
| **Agent** | 2,151 lines (cognitive agent, cognitive LLM pipeline, RAG, preprocessing) |
| **Total Nova** | ~68,000 lines across compiler, runtime, core, mind, agent, and package manager |
| **Tests** | 182 tests (176 pass, 6 skip) |
| **Targets** | Linux x86-64, macOS x86-64, WebAssembly (WASI), Windows x86-64, ARM64-Linux, Windows ARM64 (PE32+ AArch64) |

---

## What is Nova?

Nova is a compiled programming language designed for building AGI systems through **Moment-Signal Computing** -- a paradigm where cognition emerges from signals flowing through specialized processing nodes. Nova compiles to native x86-64 machine code via direct Linux and Windows syscalls with no C library, no garbage collector, and no runtime interpreter.

The language is **fully self-hosting**: the Nova compiler is written in Nova, bootstrapped from handwritten x86-64 assembly. The resulting native binary compiles its own source code to produce byte-identical output -- a verified fixed point.

**New in v4.0:** SSE2-vectorized SIMD operations, tiled matrix multiplication for cache efficiency, OpenBLAS FFI for large matrices, a cognitive LLM pipeline with confidence annotation and episodic memory, BM25-scored n-gram embeddings for competitive RAG, and a unified embedding interface with cognitive dimensions.

**New in v4.2:** IEEE 754 double-precision float utilities (classification, rounding, formatting, parsing, statistics), syscall-based FFI without libc (ELF parser, SYSV hash lookup, raw socket wrappers), ARM64/AArch64 code generation pass (register mapping, NEON SIMD, syscall translation), file-backed persistent allocator (mmap MAP_SHARED, checksummed header, survives arena resets), hash-accelerated O(1) function lookup in codegen, and tensor performance benchmarks.

**New in v4.1:** Bayesian belief system (Beta distribution replacing flat 0-100 confidence), goal engine with four drive generators (curiosity, social, task, homeostasis), safety/audit layer with permission tiers and reversibility classification, imagination subsystem (world model, forward simulation, counterfactual reasoning, dream recombination), concept hierarchy with property inheritance and taxonomic similarity, schema system for entity type validation, multi-vector embeddings for rich semantic representation, OCEAN personality vectors and constitutional rules in the soul, multi-loop agent architecture replacing the sequential pipeline, and structural analogy via Jaccard similarity replacing substring matching.

At its foundation, Nova is a practical systems language with structs, enums, lambdas, coroutines, pattern matching, try/catch/finally, 270+ built-in functions, and an arena allocator for deterministic memory management. You can write a TCP server with raw syscalls, parse JSON, manage processes, or do bitwise manipulation -- all without any external dependency.

What sets Nova apart is its first-class support for cognitive computing. Where other languages treat AI as a library concern, Nova builds it into the language itself:

- **Moments** capture structured experiences -- what happened, who was involved, what was felt, and what the consequences were.
- **Signals** carry moments between processing nodes with typed routing, priority, and trace metadata. Streaming signals enable lazy, incremental processing via `signal_stream_new()` and `stream_pipe()`.
- **Nodes** are specialized cognitive processors -- perceivers, knowers, rememberers, reasoners, feelers, and actors -- each with domain-specific computation. Dynamic node scaling via `node_pool_new()` auto-scales when queue depth exceeds thresholds.
- **Flow operators** (`~>`, `<~`, `=>>`, `<<~`, `~~>`, `<=>`, `|~>`) express signal routing as concisely as arithmetic.
- **Mind declarations** wire an entire cognitive architecture in a single declarative block.
- **Soul declarations** define first-class identity and behavior constructs -- purpose, values, drives, and feelings -- giving each agent a persistent personality.
- **System declarations** compose multiple minds, bridges between them, and a soul into a unified multi-mind agent.
- **Knowledge persistence** provides file-based key-value stores and knowledge graphs for long-term memory across sessions.
- **Concept hierarchy** with `is_a` inheritance, property propagation, taxonomic similarity, schemas for entity type validation, and multi-vector embeddings for rich multi-faceted semantic representation.
- **Bayesian belief system** using Beta distributions (alpha/beta pseudocounts) for probabilistic belief representation, with evidence accumulation, decay with prior floors, conflict detection, and confidence conversion.
- **Goal engine** with priority-sorted goals, hierarchical subgoals, four drive generators (curiosity, social, task, homeostasis), and goal-reasoning integration.
- **Safety/audit layer** with three permission tiers (observe, respond, full), reversibility classification, circular-buffer decision logging, one-shot override mechanism, content filtering, and rate limiting.
- **Imagination subsystem** with a world model (entities, relations, causal patterns), forward simulation, consequence prediction, counterfactual reasoning, dream recombination, and scenario planning.
- **Security primitives** include SHA-256 hashing, input validation, secure memory allocation, and rate limiting.
- **SIMD-accelerated tensor math** provides SSE2-vectorized dot product, element-wise operations, and tiled matrix multiplication with automatic OpenBLAS dispatch for large matrices.
- **Cognitive LLM pipeline** routes LLM output through confidence estimation, episodic memory, and symbolic reasoning -- not just wrapping llama.cpp, but integrating it into Nova's cognitive architecture.
- **Competitive RAG embeddings** use BM25-scored character n-grams with cognitive dimensions (emotion, recency, reasoning depth) for retrieval that captures subword similarity and episodic context.
- **Foreign Function Interface** enables calling into shared libraries (`.so`/`.dylib`) with full ABI support, including a Python bridge for bidirectional interop.

The result is a language where you can write a TCP server with raw syscalls on one line and declare a reasoning pipeline with memory enrichment on the next -- all compiling to the same native binary. Nova targets Linux, macOS, WebAssembly (WASI), and Windows.

## Quick Start

```bash
# Clone and build
git clone https://github.com/amoufaq5/nova.git
cd nova

# Build the compiler (requires only GNU as + ld)
make

# Compile and run a program
make run FILE=examples/hello.nova

# Run all 170 tests (164 pass, 6 skip — see tests/run_tests.sh)
make test-all

# Verify self-hosting (stage2.s == stage3.s)
make self-host

# Syntax check without compiling
bin/nova myprogram.nova --check

# See codebase statistics
make stats
```

### Installing a release build

Pre-built packages are attached to every tagged release on GitHub:

```bash
# Linux one-liner (any distro)
curl -sSL https://raw.githubusercontent.com/nova-lang/nova/main/tools/install.sh | sh

# Debian / Ubuntu
sudo apt install ./nova_0.1.0_amd64.deb

# macOS
sudo installer -pkg nova-0.1.0.pkg -target /
# or
brew tap nova-lang/nova && brew install nova

# Windows
msiexec /i nova-0.1.0.msi /quiet
```

See `INSTALL.md` for the full per-platform breakdown, including
manual tarball install and the system-wide layout each package uses.

## Hello World

```nova
fn main() {
    println("Hello, World!")
}

main()
```

There is no implicit entry point. Execution begins at the first top-level statement and proceeds sequentially. By convention, programs define a `main()` function and call it at the end of the file.

## Key Features

### Cognitive Architecture (Moment-Signal Computing)

- **Moment literals** -- structured experience records with entities, emotions, and consequences
- **Signal types** -- event, question, command, request, response, correction, reflection
- **Streaming signals** -- lazy, incremental signal processing via `signal_stream_new()`, `signal_stream_next()`, and `stream_pipe()`
- **6 cognitive node types** -- perceiver, knower, rememberer, reasoner, feeler, actor
- **Dynamic node scaling** -- `node_pool_new()` with auto-scaling when queue depth exceeds thresholds
- **7 flow operators** -- `~>` forward, `<~` backward, `=>>` broadcast, `<<~` memory enrichment, `~~>` tentative, `<=>` resonance, `|~>` filtered
- **Mind declarations** -- `mind Nova { nodes { ... } channels { ... } }` for declarative cognitive architecture
- **Soul declarations** -- first-class identity/behavior construct with identity, values, drives, and feelings sections
- **System declarations** -- multi-mind composition with bridges and soul binding: `system FullAgent { minds { ... } bridges { ... } soul: Aurora }`
- **Signal scheduler** -- priority-based dispatch with batching for cache-friendly processing
- **Path declarations** -- named signal processing pipelines with enrichment stages
- **5 mind systems** -- academic learning, experiential learning, emotion modeling, memory, reasoning
- **Bayesian beliefs** -- Beta distribution (α, β) pseudocounts with evidence accumulation, decay floors, and conflict detection
- **Goal engine** -- priority-sorted goals, hierarchical subgoals, four drive generators (curiosity, social, task, homeostasis)
- **Safety layer** -- permission tiers (observe/respond/full), reversibility classification, decision logging, override mechanism
- **Imagination** -- world model with causal patterns, forward simulation, consequence prediction, counterfactual reasoning, dream recombination
- **Concept hierarchy** -- `is_a` inheritance with property propagation, taxonomic similarity, schemas, multi-vector embeddings
- **Multi-loop agent** -- concurrent perception/memory/reasoning/emotion/action/goal loops with signal queues
- **Structural analogy** -- Jaccard similarity on content words with word-pair order bonus, replacing substring matching

### SIMD & Tensor Math

- **SSE2-vectorized operations** -- `simd_dot_f64` (4 doubles/iteration, 2x unrolled), `simd_add_f64`, `simd_mul_f64`, `simd_sub_f64`, `simd_div_f64`, `simd_fma_f64`, `simd_relu_f64`, `simd_max_f64`, `simd_scale_f64`, `simd_sum_f64`, `simd_norm_f64`
- **Tensor library** -- `tensor_new`, `tensor_matmul` (auto-dispatches: tiled for 64+ cols, transpose+dot for small), `tensor_add`, `tensor_sub`, `tensor_scale`, `tensor_relu`, `tensor_softmax`, `tensor_cosine_sim`, `tensor_transpose`
- **Tiled matrix multiplication** -- 32x32 block tiling for L1 cache efficiency on matrices >= 64 columns
- **OpenBLAS FFI** -- `blas_matmul` auto-detects and calls `cblas_dgemm` for large matrices via runtime FFI

### Embeddings & RAG

- **Unified embedding interface** -- `embedding_init`, `embedding_encode`, `embedding_similarity` with selectable backends (TF-IDF, n-gram+BM25, neural)
- **BM25-scored character n-grams** -- subword tokenization captures morphological similarity ("running"/"runner" share "run")
- **Cognitive embeddings** -- `embedding_cognitive` augments base vectors with emotion (valence/arousal/dominance), episodic (recency/frequency), and reasoning depth dimensions
- **Document indexing** -- `embedding_add_document` builds vocabulary and IDF statistics incrementally

### Cognitive LLM Pipeline

- **Confidence-annotated generation** -- `cognitive_generate` estimates certainty/uncertainty from text markers and returns `conf_new` results
- **Episodic chat** -- `cognitive_chat` retrieves similar past interactions as context, self-corrects when confidence is below threshold
- **Cognitive text embedding** -- `cognitive_embed_text` produces hash-based 64-dimensional embeddings with SIMD-accelerated normalization
- **Interaction history** -- stores prompt/response/confidence triples with configurable max history

### IEEE 754 Float Utilities

- **Constants** -- `float_zero`, `float_one`, `float_inf`, `float_neg_inf`, `float_nan`, `float_epsilon`, `float_max`, `float_min_positive`
- **Classification** -- `float_is_nan`, `float_is_inf`, `float_is_finite`, `float_is_negative`, `float_is_zero`
- **Rounding** -- `float_floor`, `float_ceil`, `float_round`, `float_trunc`
- **Arithmetic** -- `float_abs`, `float_neg`, `float_min_of`, `float_max_of`, `float_clamp_range`, `float_lerp`, `float_fma`, `float_reciprocal`, `float_mod`
- **Formatting** -- `float_format(f, decimals)` produces "3.14", `float_parse(s)` parses from string
- **Statistics** -- `float_sum_list`, `float_mean_list`, `float_min_list`, `float_max_list`, `float_variance_list`

### Foreign Function Interface

- **Dynamic library loading** -- `ffi_open`, `ffi_sym`, `ffi_call` for calling C functions from Nova
- **Syscall-based FFI** -- `ffi_syscall_init`, `ffi_load`, `ffi_lookup` for loading ELF shared libraries without libc, using direct syscalls and SYSV hash lookup
- **Raw sockets** -- `sys_socket`, `sys_bind`, `sys_listen`, `sys_accept`, `sys_connect`, `sys_send`, `sys_recv` via direct Linux syscalls
- **Network utilities** -- `make_sockaddr_in_raw`, `ip_to_int` for constructing socket addresses
- **Python bridge** -- `py_init`, `py_exec`, `py_eval`, `py_import`, `py_call` for bidirectional Python interop
- **LLM bridge** -- C bridge to llama.cpp for model loading, text generation, tokenization, and embedding extraction

### Knowledge & Persistence

- **File-based key-value store** -- `db_open`, `db_put`, `db_get`, `db_prefix`, `db_close` for persistent storage across sessions
- **Persistent allocator** -- `persistent_open`, `persistent_alloc`, `persistent_free`, `persistent_sync`, `persistent_close` for file-backed memory pools using mmap(MAP_SHARED) that survive arena resets with checksummed headers
- **Embeddings** -- integer vectors for semantic similarity: `embed_new`, `embed_set`, `embed_cosine`, `embed_distance`
- **Knowledge graphs** -- entity-relation graphs with nearest-neighbor lookup: `kg_new`, `kg_add_entity`, `kg_add_relation`, `kg_nearest`

### Security

- **SHA-256 hashing** -- `sha256` and `sha256_verify` for cryptographic integrity checks
- **Input validation** -- `sanitize` for string cleaning, `validate_range` for bounds checking
- **Secure memory** -- `secure_alloc` and `secure_free` for sensitive data that is zeroed on deallocation
- **Rate limiting** -- built-in throttling primitives for controlling signal and request throughput

### Functions and Control Flow

- First-class functions, recursion, forward references, closures
- Lambda expressions: `fn(x) { return x * 2 }`
- Higher-order functions: `map`, `filter`, `reduce`, `any`, `all`, `zip`, `enumerate`
- Pipe operator: `data |> transform |> output`
- `if` / `else if` / `else`, `unless`, `guard` clauses
- `while`, `until`, `do..while`, `loop`
- `for item in list`, `for i, item in list`, `for i in range(n)`, `for i in 0..10`, `for i in 0..=10`
- `match` expressions with literal patterns, guards, and wildcard default; returns a value when used in expression position (RHS of `let`, function call argument, arithmetic operand), including block-body arms with inner `let` bindings
- `try` / `catch` / `finally` with cross-function throw
- `defer` statements for cleanup
- Labeled loops with `break` and `continue` (`@outer`)
- Ternary expressions (`? :`), `do` expressions
- Nullish coalescing (`??`)

### Types and Data

- 64-bit signed integers, booleans, strings (with escape sequences)
- String interpolation: `"Hello, ${name}!"`
- Multiline strings: `"""..."""`
- Lists with bracket indexing, negative indices, slicing, repetition
- Maps (hash maps) with literal syntax and iteration
- Sets
- Structs with dot-notation field access and method-style calls; R25A brace-init `Foo { field: val, ... }` (with source-order to declaration-order reordering, `Box<int> { value: 99 }` generic spelling, and destructure patterns `let Foo { field: binder } = x` plus match-arm struct patterns `Foo { x: 0, y: _ } => body`); R26A functional update-syntax `Point { x: 10, ..p }` (the `..base` spread copies any field not explicitly overridden from `base`; pure clone via `Foo { ..p }`; one `..base` per construction enforced by the parser; non-trivial base expressions auto-cached in a fresh temp so they evaluate exactly once)
- Enums with variant access (`Op.Add`) — and R17A sum types with payloads (`enum Option { Some(int) None }`, `Option::Some(42)`) plus destructuring match arms (`Option::Some(n) => n`) and compile-time exhaustiveness WARN — R19B extends sum-type lowering to all 6 targets (Linux x86-64, macOS x86-64, Windows x86-64, ARM64-Linux, Windows ARM64, WASM). R20A adds the postfix `?` Result-propagation operator: `let n = parse_num(s)?` unwraps `Result::Ok(n)` to `n` or returns the whole `Result::Err(e)` Result from the enclosing function. The disambiguator from ternary peeks one token past `?`: literals / identifiers / parens / unary / `if` / `match` keep ternary semantics, anything else (binary op, closer, terminator, statement keyword) treats `?` as Result propagation. Const-fold collapses `Result::Ok(constant)?` straight to the constant. R21A adds generic enum payload types: `enum Result<T, E> { Ok(T) Err(E) }` declares a parametric enum, then `Result<int, str>` and `Result<list, int>` annotate `let` bindings — the same decl carries any payload value at construction. Type parameters are erased at codegen (NOVA's tagged-value runtime already handles polymorphic payloads), so the syntax is a zero-cost parser-level metadata layer; `par_skip_type` was extended to handle `>>` (TOK_SHR) for tight-nested generics like `Result<Result<int, str>, str>`. R22B extends parser-only generics from enums to functions: `fn map<T, U>(xs: list<T>, f: T -> U) -> list<U> { ... }` parses generic type parameters between the fn name and the parameter list, accepts `: T` parameter type annotations (legacy), the new `-> T` return-type spelling (Rust-like, alongside the legacy `: T`), and function-type parameters via `T -> U` inside `par_skip_type`. Generic fns compose with R21A generic enums (`fn unwrap<T, E>(r: Result<T, E>) -> T { ... }`). All annotations erased at codegen — same dynamic-runtime tagged-value execution.
- Destructuring assignment and rest patterns
- Fixed-point floats (`3.14` stored as `3140`, scale 1000)
- Hex (`0xFF`), octal (`0o777`), binary (`0b1010`), numeric separators (`1_000_000`)
- Null safety with nullish coalescing (`??`) and safe access
- Type annotations (documentation-level)

### Operators

- Arithmetic: `+` `-` `*` `/` `%` `**` (power)
- Comparison: `==` `!=` `<` `>` `<=` `>=`, chainable (`1 < x < 10`)
- Logical: `&&` `||` `!` (short-circuit)
- Bitwise: `&` `|` `^` `~` `<<` `>>`
- Compound assignment: `+=` `-=` `*=` `/=` `%=` `&=` `|=` `^=` `<<=` `>>=`
- Membership: `in`, `not in`, `is` type checking
- String concatenation via `+`, string multiplication via `*`

### Concurrency

- Coroutines with `yield` and resume
- Channels for inter-coroutine communication
- Task pools for concurrent workloads

### Systems Programming

- Inline assembly via `asm{}` blocks
- Memory primitives: `store64`, `load64`, `store8`, `load8`, `memcpy_raw`
- Arena allocator (mmap-backed bump allocation with instant reset)
- TCP networking via raw syscalls: `socket`, `bind`, `listen`, `accept`, `send`, `recv`
- UDP networking via raw syscalls: `sys_socket_udp`, `sys_sendto`, `sys_recvfrom`, `sys_setsockopt_so_reuseaddr` (R28C; enables NAT hole-punching after TCP-based STUN discovery)
- Multi-FD wait via `sys_poll(fds, nfds, timeout_ms)` raw syscall (R29A; unblocks pipelined I/O over multiple sockets — R28A's single-threaded peer handlers no longer stall their accept queue behind one fd)
- Process management: `fork`, `exec`, `waitpid`, `pipe`
- File I/O, directory operations, environment variables
- JSON parsing and serialization
- `import` for source-level file inclusion
- Extern function declarations

### Compiler and Tooling

- Cross-compilation: `--target=linux` (default), `--target=macos`, `--target=wasm`, `--target=windows`, `--target=arm64` (ARM64-Linux/Android), `--target=windows-arm64` (PE32+ AArch64)
- Hash-accelerated O(1) function lookup in codegen (8-bucket list-of-lists)
- `--check` for syntax validation without code generation
- `--stats` for compilation statistics
- `--version` and `--debug` flags
- Rich error messages with line numbers and source context
- List comprehensions: `[x * x for x in range(10)]`
- Map comprehensions
- Default parameters and named arguments
- Trailing commas, multiline strings
- Package manager: `nova pkg init`, `nova pkg install <name>`, `nova pkg build`
- DWARF `.debug_line` + `.debug_info` on Linux ELF for source-level debugging (see `DWARF_AUDIT.md`)
- **Debug Adapter Protocol** server (`tools/nova-dap`) — 22 capabilities: breakpoints, conditional breakpoints (`condition: "x > 5"`), hit-count breakpoints (R29E — `hitCondition: ">3"` skips first 3, `"%2"` fires every other hit, `"==5"` fires on the 5th, server-side counter + parsed predicate gates the DAP `stopped` event so the IDE only sees condition-passing-Nth hits; malformed strings come back `verified: false` with `breakpoint-validation-error`), data breakpoints / watchpoints (`dataBreakpointInfo` + `setDataBreakpoints` driving gdb hardware watchpoints; stops emit `reason: "data breakpoint"` with before/after values in the description), function breakpoints by name (`setFunctionBreakpoints` — break on entry to any function whose symbol matches, optional condition, unresolved names return `verified: false`; stops emit `reason: "function breakpoint"` with description `"Entry to <name>"`), instruction breakpoints (`setInstructionBreakpoints` — break at a specific machine address `*0xADDR`, used by the IDE's disassembly view), step in/out/over at line OR instruction granularity (`granularity: "instruction"` reroutes to gdb's `-exec-step-instruction` / `-exec-next-instruction` for single-machine-instruction stepping; each `stopped` event carries `instructionPointerReference` so the disassembly view can pin to the PC), disassembly view (`disassemble` request returns `DisassembledInstruction[]` for a memory range, driven by gdb's `-data-disassemble`), stack traces, scopes, live locals (via R4A's `.debug_info` DIE entries), expression evaluation (`evaluate` request for watch panel / REPL / hover tooltips), pause, continue, exception breakpoints, plus full multi-thread coordination (per-thread step/pause/continue, `threads` request, stop events with `threadId` + `allThreadsStopped`). gdb non-stop mode by default. R28F adds custom-request channel extensions for **sample-based profiling**: `nova/profile/start({frequency_hz})` installs a background sampler that periodically pauses the inferior, captures the call stack via `-stack-list-frames`, and resumes (transient `stopped`/`continued` events suppressed); `nova/profile/stop()` returns flame-graph-shaped aggregate `{samples, frames, total_samples, duration_s}` with frame deduplication keyed on (function, file, line); `nova/profile/report({format})` re-formats the captured samples as human-readable text (top-10 outline), Brendan-Gregg folded stacks (`frame;frame;... count` per line, parseable by flamegraph.pl), or full JSON dump — useful for finding perf bottlenecks without instrumentation overhead
- **Language Server Protocol** server (`tools/nova-lsp`) — 17 capabilities: completion (R24E deepens this with a context-aware layer: `Name::` -> enum variants, `var.` -> struct fields, `let x: ` / fn-param `(p: ` / `Box<` -> enum + struct + primitive type names; R26D adds brace-init field completion: `Point { ` -> remaining un-typed field names from `Point`'s declaration, `Point { x: 10, ` -> just `y` once `x` is already specified, cross-file via imports + workspace index, multi-line aware so the trigger still fires after the user presses Enter inside the brace body, suppressed inside string literals + comments; R26A.2 follow-up adds base-spread completion: `Point { ..` -> every in-scope variable whose type is `Point` (Variable-kind items with `name: Point` detail strings), the same enclosing-fn walk handles annotated `let p: Point`, inferred `let p = Point(...)`, brace-init `let p = Point { ... }`, and fn parameter `(p: Point)` bindings; type filtering excludes Box / other-struct values; the binding being declared on the cursor line is filtered out so `let q = Point { ..|` won't suggest `q`; falls back to the legacy text-based list when no trigger applies; capability shape unchanged), hover (signatures + `///` doc-comment blocks rendered as markdown, cross-file via the import graph), definition (cross-file), references, workspace-wide rename (F2 across imports for top-level fn/let/const/type, with name-conflict detection), code actions (extract function via the R21F `extract_function.py` analysis pipeline — selection -> free-variable analysis -> `fn extracted_N(args...)` helper at file top-level; rejects empty / single-line / cross-fn selections so the lightbulb stays clean; **inline variable** via the R25F `inline_variable.py` module — cursor on a `let NAME = RHS` -> every use of `NAME` in the enclosing fn scope replaced with the parenthesised `(RHS)` and the let removed, refusing on reassignment + closure capture, warning in the action title when the RHS is side-effecting and would duplicate calls across uses; organize imports, sort fn declarations, plus a `quickfix` for R17A's match-exhaustiveness WARN that auto-adds stub arms for missing variants with `_` placeholders matching payload arity, inserting before the existing `_` catch-all when present), workspace symbol search (`workspace/symbol`, Cmd+T fuzzy picker), semantic tokens (`textDocument/semanticTokens/full` + `/range` — per-token classification into variable / function / type / namespace / parameter / constant with declaration / readonly / static modifiers, going beyond TextMate regex highlighting), call hierarchy (`textDocument/prepareCallHierarchy` + `callHierarchy/incomingCalls` + `callHierarchy/outgoingCalls` — caller/callee navigation tree, grouped by enclosing top-level fn, cross-file via imports + workspace index, builtins elided), inlay hints (`textDocument/inlayHint` — parameter-name ghost text at call sites resolved through the same import + workspace-index path, viewport-range filtered, plus literal-RHS type hints on `let` bindings), code lens (`textDocument/codeLens` + `codeLens/resolve` — clickable "N references" / "N readers" / "N variants used" annotations rendered above top-level fn / let / const / enum decls, with optional "/ tested" marker when a `tests/test_*.nova` mentions the decl), type hierarchy (`textDocument/prepareTypeHierarchy` + `typeHierarchy/supertypes` + `typeHierarchy/subtypes` — navigate sub/supertype edges: enum -> declared variants as `EnumMember` items, `type T = U` alias -> the `U` base + every other alias pointing back, enum-variant -> parent enum; cross-file via imports + workspace index), folding ranges (`textDocument/foldingRange` — collapse/expand gutter markers for fn bodies, match/if/else blocks, enum/struct bodies, contiguous `///` doc-comment blocks rendered as `kind=comment`, contiguous `import` blocks as `kind=imports`), document symbols (`textDocument/documentSymbol` — hierarchical `DocumentSymbol[]` outline tree for the editor sidebar / Cmd+Shift+O quick-pick, with enum variants and struct fields nesting as children of their parent declaration). R23F enhances the existing per-file diagnostic capability with `workspace/diagnostic` (LSP 3.17 pull-model aggregation) — every diagnostic marker across every indexed file + open buffer is funneled into a single panel feed; content-hash `resultId` drives incremental `kind: "unchanged"` vs `kind: "full"` reports so a re-poll over an unchanged workspace returns just a list of unchanged-tags without re-running the engine. Capability count stays at 17 — the enhancement layers `workspaceDiagnostics: true` onto the existing `diagnosticProvider` rather than adding a new top-level provider
- `tree-sitter-nova` grammar (`tools/tree-sitter-nova/`) — full
  surface syntax including R17A enum sum types with payloads +
  `Type::Variant(args)` ctor + match destructure, R20A postfix `?`
  Result-propagation operator, R21A generic enums `<T, U>`, R22B
  generic fns `<T, U>(p: T) -> U` and function-type `T -> U`
  parameter annotations, R23A generic structs + semicolon-separated
  fields, R25A struct brace-init `Foo { field: val, ... }` with
  source-order to declaration-order reordering + destructure patterns
  `let Foo { field: binder } = x` (parser-time lowering) and
  match-arm struct patterns `Foo { x: 0, y: _ } => body` (parser-time
  rewrite to a wildcard-with-guard plus prepended binder lets), named-
  argument calls `f(name: value)`, `#` / `--` line
  comments, `not`/`and`/`or` keyword operators, `is` / `in` / `not
  in` type and membership operators, `..` / `..=` ranges, `|>`
  pipe, `??` nullish coalescing + `??=`, octal `0o755`, `const`
  decls, slice `xs[s:e:step]`, map literals + comprehensions, list
  comprehensions, multi-binding and destructure `let`, `...args`
  spread, `expr @ score` confidence, `break/continue if`, labeled
  loops `@outer ... break @outer`, `do { ... } while cond`,
  `impl Type { fn ... }`, `fn Type.method(self, ...)`,
  if-as-expression, match guards, power `**`, flow operators
  `~> <~ =>> <<~ ~~> <=> |~>`, and `mind`/`soul`/`system` cognitive
  declarations. R26B adds R25A brace-init + destructure (struct
  init expressions `Point { x: 1, y: 2 }`, shorthand `Point { x, y }`,
  let-binding destructure `let Point { x, y } = p`, partial
  destructure `let Point { x: a, .. } = p`, match-arm struct patterns
  `Point { x: 0, y: _ } => ...`) plus forward-compatible R26A update-
  syntax (`Point { x: 1, ..base }`). **108 corpus tests pass** (89
  baseline + 10 brace-init + 9 destructure), **242 / 247 (98.0%)**
  of `tests/*.nova` + `examples/*.nova` parse with 0 ERROR / 0
  MISSING nodes. `highlights.scm` + `folds.scm` + `locals.scm`
  queries; drop-in support for Neovim, Helix, Emacs, Zed
- VS Code extension (`tools/vscode-nova/`) — TextMate grammar +
  tree-sitter contributor manifest + LSP/DAP wiring

## Moment-Signal Computing

Nova's cognitive architecture is built on three primitives:

### Moments -- Atoms of Experience

A Moment captures a structured experience: what happened, who was involved, what was felt, and what the consequences were. It is the atomic unit of cognition.

```nova
moment Greeting {
    what_happened: "a person said hello"
    who: entity Person { name: "Alice", role: "visitor" }
    felt: warmth 0.7, curiosity 0.4
    consequence: expectation "conversation will follow"
}
```

Internally, a Moment is a tagged list: `[TAG, what_happened, who, felt_valence, felt_arousal, consequence, salience, urgency, timestamp]`. Entities, emotions, and consequences are their own tagged structures, composable and introspectable.

### Signals -- Message Passing

Signals carry Moments between Nodes. Each signal has a type (event, question, command, request, response, correction, reflection), a priority for scheduling, a trace for cycle detection, and metadata for context enrichment.

```nova
let sig = signal_event(moment, "external", "Perceiver")
scheduler_emit(sig)
scheduler_run()
```

### Nodes -- Cognitive Processors

Six specialized node types handle different aspects of cognition:

| Node Type | Role | Computation |
|-----------|------|-------------|
| **perceiver** | Interprets raw input | Weighted Jaccard template matching |
| **knower** | Stores structured knowledge | Spreading activation on semantic graph |
| **rememberer** | Episodic memory | Composite similarity scoring (content, entity, emotion, recency) |
| **reasoner** | Logical processing | Multi-strategy: deductive, abductive, analogical, causal |
| **feeler** | Emotional modeling | Dimensional drift (valence, arousal, dominance) |
| **actor** | Output generation | Competing activations (reason, emotion, habit, reflex) |

### Flow Operators -- Signal Routing

Seven operators express how signals move through a cognitive architecture:

```nova
// Forward flow: signal passes through nodes in sequence
result = signal ~> perceiver ~> reasoner ~> actor

// Backward flow: reflection, feedback
reflection = signal <~ reasoner

// Broadcast: one signal to many nodes simultaneously
count = signal =>> [reasoner, feeler, actor]

// Memory enrichment: pull context from memory systems
context <<~ rememberer("related knowledge", signal@who)

// Tentative flow: reduced priority, speculative processing
tentative = signal ~~> feeler

// Resonance: bidirectional exchange between nodes
Think <=> Heart

// Filtered flow: only passes if salience exceeds threshold
filtered = signal |~> perceiver
```

### Mind Declarations -- Wiring It All Together

A `mind` declaration creates nodes, registers them with the scheduler, and wires channels between them in one concise block:

```nova
mind Nova {
    nodes {
        Sense: perceiver
        Store: knower
        Memory: rememberer
        Think: reasoner
        Heart: feeler
        Output: actor
    }
    channels {
        perception: Sense =>> [Store, Memory, Think, Heart]
        reasoning: Think ~> Output
        emotion: Heart ~> Think
    }
}
```

This single declaration creates all six nodes (accessible as `Nova_Sense`, `Nova_Think`, etc.), registers them with the signal scheduler, and establishes typed channels between them. No manual wiring required.

### Path Declarations -- Named Pipelines

Paths define reusable signal processing pipelines with enrichment stages:

```nova
path ForwardEnrichment {
    signal arrives at perceiver
        ~> perceiver interprets raw
        ~> rememberer enriches with context
            <<~ academic("what do we know?")
            <<~ experiential("have we seen this before?")
        ~> reasoner processes enriched_signal
        ~> actor decides response
}
```

### Soul Declarations -- Identity and Behavior

A `soul` declaration defines the persistent identity, values, drives, and feelings of a cognitive agent. The soul influences signal processing through bias functions and emotional preprocessing.

```nova
soul Aurora {
    identity { purpose: "understand and assist" }
    values { truth: "never fabricate" }
    drives { curiosity: 80 }
    feelings { warmth: 50 }
}
```

Soul values bias signal processing (`soul_bias`), drives modulate attention (`soul_drive_level`), and feelings evolve over time (`soul_tick`). The `soul_preprocess` function applies the soul's personality to incoming signals before they reach cognitive nodes.

**New in v4.1:** Souls now support OCEAN personality vectors (`soul_set_personality` with openness, conscientiousness, extraversion, agreeableness, neuroticism dimensions), constitutional rules with severity levels (`soul_add_constitution` for warn/block/override_only enforcement), identity themes with reinforcement (`soul_add_theme`, `soul_reinforce_theme`), and a loyalty hierarchy (`soul_add_loyalty` with insertion-sorted priority levels).

### System Declarations -- Multi-Mind Composition

A `system` declaration composes multiple minds, bridges between them, and a soul into a unified agent:

```nova
system FullAgent {
    minds {
        perception: Perception
        cognition: Cognition
    }
    bridges {
        see_to_think: perception.Eyes ~> cognition.Think
    }
    soul: Aurora
}
```

Bridges wire nodes from different minds together, enabling modular cognitive architectures where each mind handles a distinct domain. The system scheduler coordinates signal flow across all minds while the soul provides unified identity.

## Examples

### Fibonacci with Recursion

```nova
fn fib(n) {
    if n <= 1 {
        return n
    }
    return fib(n - 1) + fib(n - 2)
}

fn main() {
    let i = 0
    while i < 20 {
        print("fib(")
        print_int(i)
        print(") = ")
        print_int(fib(i))
        println("")
        i = i + 1
    }
}

main()
```

### Higher-Order Functions and Lambdas

```nova
let data = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]

// Filter evens, double them, sum the result
let evens = filter(data, fn(x) { return x % 2 == 0 })
let doubled = map_list(evens, fn(x) { return x * 2 })
let total = reduce(doubled, fn(acc, x) { return acc + x }, 0)

println("Even numbers doubled and summed: ")
print_int(total)  // 60

// Functional composition
fn apply_twice(f, x) {
    return f(f(x))
}

let inc = fn(n) { return n + 1 }
print_int(apply_twice(inc, 5))  // 7
```

### TCP Echo Server (No libc, Raw Syscalls)

```nova
fn main() {
    let port = 8080

    // Create socket: AF_INET=2, SOCK_STREAM=1
    let server_fd = socket(2, 1, 0)
    if server_fd < 0 {
        println("Error: cannot create socket")
        exit(1)
    }

    let addr = make_sockaddr_in(port, 0)
    bind_socket(server_fd, addr, 16)
    listen_socket(server_fd, 5)
    println("Listening on port 8080...")

    while 1 == 1 {
        let client_fd = accept_conn(server_fd, 0, 0)
        if client_fd >= 0 {
            println("Client connected!")
            let buf = alloc(1024)
            let n = recv_data(client_fd, buf, 1024)
            if n > 0 {
                send_data(client_fd, buf, n)
            }
            close_fd(client_fd)
        }
    }
}

main()
```

### Cognitive Architecture with Mind Declaration

```nova
import "../src/core/moment.nova"
import "../src/core/signal.nova"
import "../src/core/node.nova"
import "../src/core/channel.nova"
import "../src/core/path.nova"
import "../src/runtime/scheduler.nova"

mind Nova {
    nodes {
        Sense: perceiver
        Store: knower
        Memory: rememberer
        Think: reasoner
        Heart: feeler
        Output: actor
    }
    channels {
        perception: Sense =>> [Store, Memory, Think, Heart]
        reasoning: Think ~> Output
        emotion: Heart ~> Think
    }
}

fn main() {
    // Configure cognitive nodes
    perceiver_add_template(Nova_Sense, "greeting", ["hello", "hi", "hey"], 80)
    perceiver_add_template(Nova_Sense, "question", ["what", "why", "how"], 70)
    knower_add_concept(Nova_Store, "greetings", "social protocol")
    reasoner_add_rule(Nova_Think, "hello", "greeting detected")
    reasoner_add_case(Nova_Think, "greeting", "respond warmly")
    feeler_set_emotion(Nova_Heart, 60, 40, 50)
    actor_add_habit(Nova_Output, "action taken", "respond", 60)

    // Create a moment and emit it as a signal
    let alice = entity_new("Alice", "visitor")
    let cons = consequence_new(CTYPE_EXPECTATION, "conversation", 80)
    let m = moment_new("hello there!", alice, 75, 55, cons, 80)
    let sig = signal_event(m, "external", "Sense")

    // Run the cognitive cycle
    scheduler_emit(sig)
    scheduler_run()

    print("Processed: ")
    print_int(scheduler_total_processed())
    println(" signals")
    print("Cycles: ")
    print_int(scheduler_cycle_count())
    println("")
}

main()
```

### Signal Flow Operators

```nova
// Create cognitive nodes
let perceiver = node_new("Perceiver", NTYPE_PERCEIVER)
let reasoner = node_new("Reasoner", NTYPE_REASONER)
let feeler = node_new("Feeler", NTYPE_FEELER)
let actor = node_new("Actor", NTYPE_ACTOR)

// Forward flow: signal passes through a chain
let result = sig ~> perceiver ~> reasoner

// Broadcast: one signal to many nodes
let count = sig =>> [reasoner, feeler, actor]

// Filtered flow: only high-salience signals pass
node_set_config(perceiver, "threshold", 50)
let filtered = sig |~> perceiver  // passes only if salience > 50

// Tentative flow: speculative, reduced priority
let tentative = sig ~~> feeler

// Backward flow: reflection
let reflection = sig <~ reasoner
```

### Sieve of Eratosthenes

```nova
fn sieve(limit) {
    let is_prime = list_new()
    let i = 0
    while i <= limit {
        push(is_prime, 1)
        i = i + 1
    }
    list_set(is_prime, 0, 0)
    list_set(is_prime, 1, 0)

    i = 2
    while i * i <= limit {
        if is_prime[i] == 1 {
            let j = i * i
            while j <= limit {
                list_set(is_prime, j, 0)
                j = j + i
            }
        }
        i = i + 1
    }

    let primes = list_new()
    i = 2
    while i <= limit {
        if is_prime[i] == 1 {
            push(primes, i)
        }
        i = i + 1
    }
    return primes
}
```

## Architecture

```
boot/nova_boot.s           Self-compiled x86-64 assembly bootstrap 106,045 lines
src/compiler/              Self-hosting compiler in Nova            16,467 lines
  lexer.nova                 Tokenizer                                867 lines
  parser.nova                Recursive descent parser               2,305 lines
  ast.nova                   AST node definitions                     582 lines
  ir.nova                    Intermediate representation              486 lines
  regalloc.nova              Register allocator                       197 lines
  lower_x64.nova             x86-64 lowering                          684 lines
  codegen.nova               Code generation + runtime stubs       10,799 lines
  compiler.nova              Entry point, CLI, import resolution      547 lines
src/core/                  Cognitive architecture types              5,306 lines
  moment.nova                Experience records, entities, emotions
  signal.nova                Typed message passing with priority
  node.nova                  6 cognitive processor types
  channel.nova               Signal routing between nodes
  path.nova                  Named processing pipelines
  similarity.nova            Similarity computation for matching
  soul.nova                  Identity, values, drives, feelings, personality, constitution
  system.nova                Multi-mind composition with bridges
  belief.nova                Bayesian belief system (Beta distribution) (v4.1)
  goal.nova                  Goal engine with drive generators (v4.1)
  safety.nova                Safety/audit layer with permission tiers (v4.1)
  imagination.nova           World model, forward sim, counterfactual, dreams (v4.1)
  concept.nova               Concept hierarchy, schemas, multi-vector embeddings (v4.1)
src/mind/                  Mind systems                              2,800 lines
  academic.nova              Knowledge from axioms and rules
  experiential.nova          Learning from lived moments
  emotion.nova               Emotional state modeling
  memory.nova                Episodic memory store and recall
  reasoning.nova             Rule-based and case-based reasoning
src/runtime/               Runtime library                          7,717 lines
  syscall.nova               Raw Linux/Windows syscall wrappers
  alloc.nova                 Arena allocator (mmap-backed bump alloc)
  string.nova                String operations
  io.nova                    File and console I/O
  list.nova                  Dynamic arrays
  map.nova                   Hash maps
  set.nova                   Set data structure
  math.nova                  Math functions
  json.nova                  JSON parsing and serialization
  scheduler.nova             Signal dispatch with batching
  coroutine.nova             Coroutine runtime
  chan.nova                   Channels for coroutine communication
  taskpool.nova              Concurrent task pools
  path.nova                  File path utilities
  db.nova                    File-based key-value store
  embed.nova                 Integer vector embeddings
  knowledge.nova             Knowledge graph with nearest-neighbor
  crypto.nova                SHA-256 hashing and verification
  validate.nova              Input sanitization and range checking
  secure_mem.nova            Secure memory (zeroed on free)
  stream.nova                Streaming signals and pipe composition
  simd.nova                  SSE2-vectorized SIMD operations (v4.0)
  tensor.nova                Tensor math with tiled matmul (v4.0)
  blas.nova                  OpenBLAS FFI wrapper (v4.0)
  embedding.nova             BM25 + n-gram + cognitive embeddings (v4.0)
  mem.nova                   IEEE 754 double memory operations
  confidence.nova            Confidence-annotated values
  ffi.nova                   Foreign function interface
  gpu.nova                   GPU compute interface
  llm.nova                   LLM model loading and generation
  llm_bridge.c               C bridge to llama.cpp
  python.nova                Bidirectional Python interop
  csv.nova                   CSV parsing
src/agent/                 Agent systems                             2,151 lines
  agent.nova                 Multi-loop cognitive agent (v4.1)         952 lines
  cognitive_llm.nova         Cognitive LLM pipeline (v4.0)             331 lines
  rag.nova                   RAG retrieval pipeline                    589 lines
  preprocess.nova            Corpus ingestion and canonicalization (v4.1)
src/pkg/pkg.nova           Package manager                            487 lines
examples/                  31 example programs                       3,125 lines
tests/                     164 test programs                        10,416 lines
```

**Total: ~68,000 lines of Nova + 106,045 lines of bootstrap assembly.**

## How It Works

Nova achieves self-hosting through a multi-stage bootstrap process:

1. **Bootstrap** (`boot/nova_boot.s`) -- an x86-64 assembly program (originally 7,379 lines handwritten, now 106,045 lines self-compiled) that interprets Nova source code. It makes raw Linux syscalls directly; no libc is linked.
2. **Stage 1** -- the bootstrap interprets the Nova compiler source (`src/compiler/*.nova`) and emits x86-64 assembly for the compiler itself.
3. **Native binary** -- GNU `as` and `ld` assemble and link the Stage 1 output into `bin/nova`, a native executable.
4. **Stage 2** -- `bin/nova` compiles its own source code, producing `stage2.s`.
5. **Stage 3** -- the Stage 2 binary compiles the compiler source again, producing `stage3.s`.
6. **Verification** -- `diff stage2.s stage3.s` confirms they are byte-identical. The compiler is a fixed point of itself.

```
                    interprets                  emits
boot/nova_boot.s  ───────────>  compiler.nova  ──────>  stage1.s
                                                            |
                                                     as + ld|
                                                            v
                                 compiler.nova  <────  bin/nova (stage 1 binary)
                                       |                    |
                                       +--- emits --> stage2.s
                                                            |
                                                     as + ld|
                                                            v
                                 compiler.nova  <────  stage 2 binary
                                       |                    |
                                       +--- emits --> stage3.s
                                                            |
                                              diff stage2.s stage3.s => identical
```

## Performance

- **Native machine code** -- compiles directly to x86-64 instructions, no interpreter, no bytecode, no JIT
- **Zero dynamic dispatch** -- all types resolved at compile time
- **Arena allocator** -- mmap-backed bump allocation with O(1) alloc and instant reset; no garbage collector, no malloc
- **Direct syscalls** -- no C library overhead; the binary talks to the kernel directly
- **SSE2 SIMD** -- vectorized dot product (4 doubles/iteration with 2x unrolling), element-wise add/sub/mul/div/fma/relu/max, broadcast scale, horizontal sum; all with scalar tail handling for arbitrary lengths
- **Tiled matrix multiplication** -- 32x32 block tiling fits L1 cache (8KB per tile), 3-5x speedup for matrices >= 64 columns vs. naive transpose+dot
- **OpenBLAS dispatch** -- automatic FFI call to `cblas_dgemm` for large matrices, matching NumPy/SciPy performance via the same BLAS backend
- **BM25 scoring** -- term frequency saturation and document length normalization for embedding quality competitive with dedicated IR systems
- **Signal batching** -- the scheduler groups signals by destination node for cache-friendly dispatch
- **Strength reduction** -- compiler optimizations for common arithmetic patterns
- **Tiny binaries** -- no standard library bloat; only the code you write ends up in the binary
- **Memory primitives** -- `store64`/`load64`/`store8`/`load8` for direct memory access when you need it

## Building

**Prerequisites:** GNU `as` and `ld` (standard GNU binutils, pre-installed on virtually every Linux system).

That's it. No C compiler. No package manager. No downloads.

```bash
make                # Build bin/nova
make self-host      # Verify self-hosting (stage2.s == stage3.s)
make test-all       # Run all 170 tests (164 pass, 6 skip)
make run FILE=path  # Compile and run a .nova file
make examples       # Build and run all 29 examples
make agent          # Run the cognitive agent
make cross-macos    # Generate macOS x86-64 assembly
make cross-windows  # Generate Windows x86-64 PE32+ executable
make smoke-winarm64 # Generate Windows ARM64 PE32+ binaries (hello + secure_random)
make wasm FILE=path # Compile to WebAssembly and run (requires Node.js + wabt)
make stats          # Show codebase statistics
make clean          # Remove build artifacts
```

### Compiler Flags

```
bin/nova <input.nova> [-o output.s] [options]

  -o <file>              Output assembly file (default: output.s)
  --target=<t>           Target platform: linux, macos, wasm, windows, arm64, windows-arm64
  --check                Syntax check only (no code generation)
  --stats                Show compilation statistics
  --debug                Enable debug output
  --version              Show compiler version

Subcommands:
  nova pkg init          Initialize a package project
  nova pkg install <n>   Install a dependency
  nova pkg build         Build the package
```

### Cross-Compilation

```bash
# macOS x86-64: generate assembly, transfer to Mac, assemble there
make cross-macos
# On macOS:
as -o nova.o bin/nova_macos.s
ld -e _main -o nova nova.o

# Windows x86-64: generate PE32+ executable
make cross-windows
# Or directly:
bin/nova examples/hello.nova --target=windows -o hello_win.s
# Transfer hello_win.exe to a Windows machine and run

# Windows ARM64 (PE32+ AArch64): build hello + secure_random for ARM-Windows
make smoke-winarm64
# Pipeline: NOVA --target=windows-arm64 emits ARM64 GAS asm with PE section
# directives + IAT imports; clang -target aarch64-windows-gnu assembles to
# Aarch64 COFF; llvm-dlltool -m arm64 fabricates ARM64 import libs from
# .def files; lld-link /machine:arm64 produces the final PE32+ executable.
# Binaries land at bin/hello_winarm64.exe and bin/secure_random_winarm64.exe;
# format is verified locally, runtime needs an ARM-Windows host.

# WebAssembly (WASI)
make wasm FILE=examples/hello.nova

# WASI preopens / filesystem (serverless / CDN-edge):
make smoke-wasi-preopens
# emits wasi_snapshot_preview1.{fd_read, fd_write, path_open,
# path_filestat_get, fd_close, fd_seek, args_get, args_sizes_get,
# environ_get, environ_sizes_get, random_get, proc_exit} imports;
# runs wasmtime --dir=/tmp on the compiled module.
```

## Built-in Functions (270+)

### I/O
`print` `println` `print_int` `read_line` `read_file` `write_file`

### WASI low-level (serverless / CDN-edge)
`wasi_open` `wasi_read` `wasi_write` `wasi_close` `wasi_seek` `wasi_filestat` `wasi_args_get` `wasi_environ_get`

### Strings
`len` `concat` `substr` `char_at` `chr` `int_to_str` `str_to_int` `starts_with` `ends_with` `str_find` `split` `join` `hex`

### Lists
`list_new` `push` `pop` `len` `list_set` `contains` `list_remove` `reverse` `sort`

### Maps
`map_new` `map_set` `map_get` `map_has`

### Higher-Order
`map_list` `filter` `reduce` `any` `all` `zip` `enumerate` `flatten` `unique`

### Math
`abs` `min` `max` `random` `random_seed`

### Fixed-Point Floats
`float_mul` `float_div` `float_to_str` `to_float` `from_float`

### System
`exit` `time` `sleep_ms` `getenv` `mkdir` `unlink` `file_size` `alloc`

### Networking
`socket` `bind_socket` `listen_socket` `accept_conn` `connect_socket` `send_data` `recv_data` `close_fd` `make_sockaddr_in` `sys_socket_udp` `sys_sendto` `sys_recvfrom` `sys_setsockopt_so_reuseaddr` `sys_poll`

### Process
`fork_process` `waitpid` `exec_program` `pipe_create`

### Memory
`store64` `load64` `store8` `load8` `memcpy_raw`

### Soul
`soul_new` `soul_feel` `soul_drive_level` `soul_bias` `soul_tick` `soul_preprocess` `soul_set_personality` `soul_get_personality` `soul_add_constitution` `soul_check_constitution` `soul_add_theme` `soul_dominant_theme` `soul_add_loyalty` `soul_loyalty_level`

### Belief
`belief_new` `belief_mean` `belief_variance` `belief_strength` `belief_update_positive` `belief_update_negative` `belief_decay` `belief_combine` `belief_conflict` `belief_to_confidence` `confidence_to_belief`

### Goal Engine
`goal_new` `goal_engine_init` `goal_engine_add` `goal_engine_tick` `goal_engine_top` `goal_engine_complete` `goal_engine_active_count` `goal_influences_reasoning` `drive_curiosity` `drive_social` `drive_task` `drive_homeostasis`

### Safety
`safety_init` `safety_set_permission` `safety_check` `safety_classify_action` `safety_log_decision` `safety_log_count` `safety_log_recent` `safety_request_override` `safety_grant_override` `safety_has_override` `safety_check_content`

### Imagination
`imagination_init` `world_model_add_entity` `world_model_set_relation` `world_model_entities` `imagine_action` `imagine_consequence` `imagine_counterfactual` `imagine_dream` `imagine_scenarios` `imagine_best_scenario`

### Concepts
`concept_init` `concept_new` `concept_find` `concept_set_property` `concept_get_inherited` `concept_is_a` `concept_children` `concept_descendants` `concept_common_ancestor` `concept_taxonomic_similarity` `schema_new` `schema_add_required` `schema_add_optional` `schema_validate` `schema_instantiate` `multi_embed_new` `multi_embed_add_facet` `multi_embed_similarity` `multi_embed_blended_similarity`

### Preprocessing
`preprocess_init` `preprocess_canonicalize` `preprocess_split_sentences` `preprocess_extract_keywords` `preprocess_ingest_text` `preprocess_ingest_file` `preprocess_deduplicate` `preprocess_consolidate` `preprocess_batch`

### Database
`db_open` `db_put` `db_get` `db_prefix` `db_close`

### Embeddings
`embed_new` `embed_set` `embed_get` `embed_cosine` `embed_distance`

### Knowledge Graph
`kg_new` `kg_add_entity` `kg_add_relation` `kg_nearest`

### Security
`sha256` `sha256_verify` `sanitize` `validate_range` `secure_alloc` `secure_free`

### SIMD (SSE2)
`simd_vec_new` `simd_vec_set` `simd_vec_get` `simd_add_f64` `simd_sub_f64` `simd_mul_f64` `simd_div_f64` `simd_dot_f64` `simd_scale_f64` `simd_sum_f64` `simd_norm_f64` `simd_fma_f64` `simd_relu_f64` `simd_max_f64`

### Optimization passes (R12E)

Two AST-level optimization passes run between parse and codegen,
on by default, opt-out with `--no-opt`:

- **Constant folding** (`cg_fold_constants`) — evaluates pure
  integer/bool/none expressions at compile time. Examples:
  `2 + 3` → `5`, `(2 + 3) * 4` → `20`, `1 << 8` → `256`,
  `(1 << 8) + (1 << 16)` → `65792`, `5 * 1000` → `5000`,
  `!1` → `0`. Skips function calls, variables, string concat
  (overloaded operator), and division by literal zero. Folds
  through unary minus, bitwise (`& | ^ << >> ~`), comparison
  (`== != < > <= >=`), and small-exponent `**`.

- **Dead code elimination** (`cg_eliminate_dead_code`) — three
  rewrites:
  1. Statements after `return` / `break` / `continue` / `throw`
     are unreachable; truncate the enclosing block at the
     terminator.
  2. Function-scope unused-let drop: `let x = pure_literal`
     where `x` is never read or reassigned is removed. RHS
     must be pure (no calls, allocations, or visible effects).
  3. Constant-condition `if` / `while` are handled by the
     existing per-target codegen shortcut (preserved from prior
     rounds, not duplicated at AST level since NOVA permits
     `if` as both a statement and an expression).

Folding follows two's-complement i64 wraparound (the same
semantics as runtime evaluation). The smart-op classifier in
`gen_runtime` correctly handles folded integer literals because
classification is range-based (PTR_THRESHOLD root fix, R6A), not
threshold-based. Pipeline:
`parse → cg_fold_constants → cg_eliminate_dead_code → codegen`.

Measured on a synthetic 30-stmt benchmark (`bench_fold.nova`):
~4.3% reduction in emitted .s size; self-hosting stage2/stage3
remains bit-identical because the compiler source already used
literals where folding would apply.

**R27A.2 extension — struct const-fold.** The fold pass now
collapses all-literal brace-init + update-syntax constructions:

- `Foo { x: 10, ..Foo { x: 1, y: 2 } }` is recognised as a
  compile-time constant (the parser wraps the non-trivial
  spread base in a `do { let _struct_spread_tmp_N = ...; ... }`
  cache); the entire do-expr is rewritten in place into a single
  `AST_LIST_LIT` in field-declaration order, eliminating the
  inner struct allocation + the runtime `base.field` reads R26A
  emits for the variable-spread path.
- `Foo { x: 1, y: 2 }` with all-literal values is rewritten to
  the same AST_LIST_LIT shape. The wire-format of a struct value
  is already a positional list across every target, so codegen
  emits identical machine code for both forms; the rewrite is
  preserved for downstream walker uniformity.
- Anything dynamic — variable, function call, arithmetic, enum
  ctor — fails the literal check; the construction falls through
  to the runtime emit path R26A introduced (no regression).

The fold needs field-declaration order, which lives in
`cg_structs` — empty until `cg_init()` runs *after* the fold
pass. A private side-table (`cg_fold_structs`, populated by
`_cg_fold_register_structs`) mirrors the AST_STRUCT_DECL list
at the top of `cg_fold_constants` so the fold helpers can
look up field order without re-architecting the pipeline.

### SIMD i32x8 codegen intrinsics (R11D)
Explicit 8-lane int32 SIMD builtins lowered directly by the compiler.
All take raw 32-byte int32 buffers (caller-allocated with `alloc(32)`):
`simd_add_i32x8(a, b, dst)` `simd_sub_i32x8(a, b, dst)`
`simd_load_i32x8(src, dst)` `simd_store_i32x8(dst, src)`
`simd_sum_abs_diff(a, b, n)` (returns int).

Per-target lowering:

| Target                | Lowering                                                  |
| --------------------- | --------------------------------------------------------- |
| Linux x86-64          | AVX2 (`vpaddd`, `vpsubd`, `vpabsd`, `vmovdqu`, ...)       |
| ARM64 Linux           | NEON (2x 128-bit `add v0.4s` / `sub v0.4s` / `abs`)       |
| ARM64 Windows         | NEON (same instruction set as Linux ARM64)                |
| macOS x86-64          | scalar 8-iter loop fallback                               |
| Windows x86-64        | scalar 8-iter loop fallback                               |
| WebAssembly (WASI)    | **v128 SIMD** -- 2x `v128.load` + `i32x4.add` / `i32x4.sub` / `i32x4.abs` per call (R15B) |

The AVX2 path assumes the host CPU implements AVX2 (Intel Haswell 2013+
or AMD Excavator 2015+). Run `make bench-simd-sad` to measure the AVX2
SAD-on-1024 speedup; the existing `make bench-simd` covers
`__intrinsic_dot_i32`; `make bench-simd-wasm` compares WASM v128 SIMD
against an open-coded scalar SAD under wasmtime (8-9x speedup measured
on 1024 lanes x 1000 trials). See `SIMD_AUDIT.md` for the design
rationale and `tests/test_simd_wasm_v128.nova` for the v128 lowering
correctness suite.

### Call-site builtin inlining (R13A)

On Linux x86-64, the SIMD primitives above plus the cheapest int_*
helpers (`int_add`, `int_sub`, `int_mul`, `int_div`, `int_mod`,
`int_and`, `int_or`, `int_xor`, `int_shl`, `int_shr`) are emitted
INLINE at the call site, skipping the runtime label's call / ret /
prologue / epilogue. Bit-identical to the runtime label body — the
runtime labels are still emitted in every binary so function-pointer
callers and the other targets (macOS, Windows, WASM, ARM64) still
resolve correctly. Realized R13A perf vs R12A baseline on the 256x256
CrossEngin stereo SAD bench (ws=7, max_disp=16): SIMD wallclock
~1.45 s → ~0.75 s (**1.93x absolute speedup**); SIMD now beats scalar
**1.10x relative** (was 0.85x in R12A — i.e. SIMD was slower than
scalar before inlining). LK speedup is bounded by the per-pixel
staging step in the CE wrapper (5x `_lk_store_i32_le` per cell);
hitting the 2x SIMD/scalar target on LK requires a future `simd_sad_u8`
primitive that works on raw bytes via `vpsadbw`. See R11D + R12A +
R13A sections in `NEXT_SESSION.md` for the full perf walkthrough.

### Raw-byte SAD primitive `simd_sad_u8` (R14B)

`simd_sad_u8(a_ptr, b_ptr, n_bytes) -> int` computes the sum of
absolute byte differences over `n_bytes` raw u8 lanes directly on
caller-allocated byte buffers — skipping the byte→i32 staging that
`simd_sum_abs_diff` requires upstream (4 `store8` calls per i32 lane
in CE's `stereo_sad_block_simd` / `lk_optical_flow_simd`). One AVX2
`vpsadbw` instruction reduces 32 input bytes to four i64 partial sums
in a single op; the inline path emits `vmovdqu` / `vpsadbw` / `vpaddq`
in a loop with horizontal-sum via `vextracti128` / `vpshufd` /
`vmovq`. Per-target lowering: Linux x86-64 AVX2 inline (same call-site
inlining as the R13A SIMD primitives), ARM64 Linux/Windows NEON
(`uabd v.16b` + `uaddlp .8h` + `uaddlp .4s`), WASM v128
(`i8x16.sub_sat_u | i8x16.sub_sat_u(swapped)` + 2x `extadd_pairwise`,
R15B), macOS / Windows x86-64 scalar 1-byte fallback. Eligible for the
next-round CE wire-in that
replaces the i32-staged SAD wrappers with raw byte ones to close the
2x SIMD/scalar ceiling on stereo and LK. Correctness: 21 assertions in
`tests/test_simd_sad_u8.nova` (identical buffers, known diff, asymmetric
unsigned, multi-chunk + tail, n=0, tail-only, exact chunks, boundary
0/255, tight loop, 16 KiB large buffer, mixed pattern vs scalar oracle).

### Byte mul-acc primitives `simd_mul_acc_*_byte` (R18A)

`simd_mul_acc_byte_signed_byte(a_u8_ptr, b_i8_ptr, n_bytes) -> int`
computes `Sum a[i] * b[i]` over `n_bytes` raw bytes where `a` is
treated as unsigned u8 (e.g. image pixel) and `b` as signed i8 (e.g.
gradient). `simd_mul_acc_signed_signed_byte(a_i8_ptr, b_i8_ptr,
n_bytes) -> int` is the same but treats BOTH operands as signed i8 —
the direct fit for LK's 5 accumulator kernels (Σ Ix·Ix, Σ Iy·Iy,
Σ Ix·Iy, Σ Ix·It, Σ Iy·It) since Ix/Iy/It are all signed gradients of
a u8 image. Closes R17C's honestly-reported 0.80x full-LK ceiling.
Per-target lowering: Linux x86-64 AVX2 inline at call site
(`vpmovzxbw` / `vpmovsxbw` widen bytes to i16, `vpmaddwd` computes
8 i32 pair-sums per 16-byte chunk, `vpaddd` accumulator, horizontal-
sum to scalar i64; 16 bytes per iter), ARM64 Linux/Windows NEON
(`ushll`/`sshll` widen + `smull`/`smull2` accumulate, 8 bytes per
iter), WASM v128 (`i16x8.extend_low/high_i8x16_u/_s` + `i32x4.
dot_i16x8_s`, 16 bytes per iter), macOS / Windows x86-64 scalar `imul`
fallback. Lowering deliberately avoids `pmaddubsw` (which saturates
the i16 pair-sum, breaking bit-identical correctness against scalar
`Sum a*b` for max-magnitude inputs like `255 * 127`). CE wire-in
deferred to R18A.2 (replace `_lk_optical_flow_u8_simd_inner`'s scalar
WIN² inner loop with 5 mul-acc calls over packed ix/iy/it byte
buffers; expected 2-3x LK speedup). Correctness: 35 assertions in
`tests/test_simd_mul_acc.nova` (both primitives x identical / known
multiply / negative b / boundary 255 vs -128 / both-negative i8 /
n=0 / tail-only / chunk-only / multi-chunk + tail / back-to-back
inlined calls / tight loop / 16 KiB large buffer / pseudo-textured
pattern vs scalar oracle).

### Tensor
`tensor_new` `tensor_set` `tensor_get` `tensor_matmul` `tensor_add` `tensor_sub` `tensor_scale` `tensor_relu` `tensor_softmax` `tensor_transpose` `tensor_cosine_sim` `tensor_print`

### BLAS
`blas_init` `blas_available` `blas_matmul`

### Embedding (v4.0)
`embedding_init` `embedding_encode` `embedding_similarity` `embedding_add_document` `embedding_cognitive` `embedding_vocab_size` `embedding_doc_count`

### Cognitive LLM
`cognitive_llm_init` `cognitive_generate` `cognitive_evaluate` `cognitive_chat` `cognitive_embed_text` `cognitive_history_count` `cognitive_clear_history`

### FFI
`ffi_open` `ffi_sym` `ffi_call` `ffi_call2` `ffi_call3` `ffi_close`

### Python Bridge
`py_init` `py_exec` `py_eval` `py_import` `py_call` `py_getattr` `py_list_len` `py_list_get`

### LLM Bridge
`llm_load_model` `llm_new_context` `llm_generate` `llm_tokenize` `llm_free_context` `llm_free_model` `llm_embedding_dim` `llm_get_embeddings`

### Confidence
`conf_new` `conf_value` `conf_level` `conf_is_uncertain` `conf_is_confident`

### Streams
`signal_stream_new` `signal_stream_next` `stream_pipe`

### Multi-Mind System
`system_new` `system_add_mind` `system_resolve_node` `system_spawn_mind` `system_describe`

### Debug
`assert` `type_of` `debug_print`

See [docs/LANGUAGE_REFERENCE.md](docs/LANGUAGE_REFERENCE.md) for the complete language reference.

## Architecture

For the compiler pipeline, the runtime layout, the six cross-target
backends, the SIMD / LSP / DAP / tree-sitter capability inventories,
the self-hosting stage chain, and a per-module catalog cross-
referenced to commit history, see [`ARCHITECTURE.md`](./ARCHITECTURE.md).
That document is the layout index — the per-target audits
(`WIN32_AUDIT.md`, `MACOS_AUDIT.md`, `WASM_AUDIT.md`,
`MOBILE_AUDIT.md`, `SIMD_AUDIT.md`, `GPU_AUDIT.md`,
`DWARF_AUDIT.md`) are the deep-dives.

## Stability & Versioning

Effective 2026-05-30 (commit `ac692f7`), NOVA tracks a real semver line.

- Current version: see `VERSION` (`0.1.0`).
- Stability inventory: see `STABILITY_AUDIT.md` — language syntax, the
  152-function builtin set, the five codegen targets (Linux,
  Windows-Wine, macOS minimum-viable, WASM hello-world, ARM64 stubbed),
  the 167-symbol `_nova_*` runtime ABI, the standard library, the
  PTR_THRESHOLD bug class, and a ranked roadmap to 1.0.
- Breaking-change log: see `COMPAT.md`. Only one breaking change in the
  past 100 commits (`str_new` format reconciliation in 56322bb).
- Known-bug docs: see `NOVA_BUG_THRESHOLD.md` (PTR_THRESHOLD details
  and `int_*` escape hatch).
- Target audits: `WIN32_AUDIT.md`, `MACOS_AUDIT.md`, `WASM_AUDIT.md`,
  `MOBILE_AUDIT.md`, `SIMD_AUDIT.md`, `GPU_AUDIT.md`.

Versioning policy: MAJOR for language/builtin/ABI break, MINOR for new
builtin/target/stdlib module, PATCH for bug fix or refactor.

## Contributing

Contributions are welcome. The compiler is written entirely in Nova (`src/compiler/`), so you can read and modify it without knowing any other language.

To get started:

1. Read the code -- start with `src/compiler/compiler.nova` (entry point, 547 lines) and work outward
2. Make your changes
3. Run `make self-host` to verify the compiler can still compile itself
4. Run `make test-all` to check for regressions (164 of 170 tests should pass, 6 skip)

The cognitive architecture lives in `src/core/` (types, soul, system) and `src/mind/` (systems). The runtime is in `src/runtime/`. The agent systems (cognitive LLM, RAG) are in `src/agent/`. The 31 examples in `examples/` demonstrate most language features.

## License

See LICENSE.

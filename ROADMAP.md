# Nova Language Roadmap

## Phase 0: Bootstrap Interpreter (Python) — CURRENT
**Status: In Progress**

- [x] Formal EBNF grammar
- [x] Lexer (tokenizer)
- [x] Recursive-descent parser
- [x] Tree-walking interpreter
- [x] Core types: int, float, str, bool, none, list, map
- [x] Tensor type with basic operations
- [x] Frame type (tabular data)
- [x] Model declarations with forward pass
- [x] Agent declarations with tools/memory/plan/act
- [x] Arena memory management blocks
- [x] Sandbox and constrain safety blocks
- [x] Distribute blocks (auto-parallel stubs)
- [x] Result[T, E] + panic error model
- [x] Pattern matching with destructuring
- [x] Pipeline operator (|>)
- [x] Lambda expressions and closures
- [x] Numerical autograd via grad()
- [x] Ownership tracking (soft ownership)
- [x] Runtime borrow checking
- [ ] CLI tooling (nova test, nova fmt, nova lint)
- [ ] Expanded standard library
- [ ] Expression-based string interpolation
- [ ] Module system / imports
- [ ] DataLoader and dataset streaming

## Phase 1: LLVM Backend
**Status: Not Started**

- [ ] Nova IR (intermediate representation) design
- [ ] AST → Nova IR lowering
- [ ] Nova IR → LLVM IR code generation
- [ ] Native binary output for x86-64
- [ ] Basic optimizations (constant folding, dead code elimination)
- [ ] Runtime library (memory management, tensor ops) in C
- [ ] Tensor operation fusion at IR level
- [ ] SIMD auto-vectorization hints
- [ ] Static shape checking in the compiler
- [ ] Arena compile-time budget verification

## Phase 2: Custom Nova IR
**Status: Not Started**

- [ ] Design Nova-specific IR with tensor operations as first-class
- [ ] SSA form with tensor-aware type information
- [ ] Shape propagation pass
- [ ] Autograd transformation pass (source-to-source AD)
- [ ] Auto-parallelism analysis pass
- [ ] Memory planning pass (arena budget analysis)
- [ ] Device placement pass (CPU/GPU split decisions)

## Phase 3: Custom Backend
**Status: Not Started**

- [ ] x86-64 code generation (replace LLVM)
- [ ] RISC-V code generation
- [ ] CUDA/NVVM IR generation for GPU kernels
- [ ] Tensor operation fusion in the backend
- [ ] SIMD auto-vectorization (AVX-512, NEON)
- [ ] Custom register allocator optimized for tensor workloads
- [ ] GPU kernel launch and synchronization code generation
- [ ] Multi-device code generation (split computation across GPUs)

## Phase 4: Production Runtime
**Status: Not Started**

- [ ] Native tensor engine (BLAS-level matmul, conv, etc.)
- [ ] Native autograd engine (tape-based reverse-mode AD)
- [ ] GPU runtime with CUDA/ROCm support
- [ ] Distributed runtime (NCCL/Gloo backends)
- [ ] Green thread scheduler
- [ ] Channel-based message passing runtime
- [ ] Arena allocator implementation (bump allocator + device memory)
- [ ] Sandbox enforcement (seccomp/landlock on Linux)

## Phase 5: Tooling & Ecosystem
**Status: Not Started**

- [ ] `nova pkg` — package manager with dependency resolution
- [ ] `nova fmt` — AST-based code formatter
- [ ] `nova lint` — static analysis and linting
- [ ] `nova test` — test runner with coverage
- [ ] `nova shell` — feature-rich REPL with completion
- [ ] `nova watch` — hot reload for development
- [ ] LSP server for IDE integration (VS Code, Neovim)
- [ ] Documentation generator
- [ ] Profiler (CPU + GPU timeline)
- [ ] Debugger with tensor inspection

# Nova

**A self-hosting compiled language for AGI through Moment-Signal Computing.**

Compiles to native x86-64 machine code. Zero dependencies. No libc. Direct Linux/Windows syscalls.

---

| | |
|---|---|
| **Status** | Self-hosting verified (`stage2.s == stage3.s`) |
| **Version** | 4.0.0 |
| **Bootstrap** | 106,045 lines of x86-64 assembly (self-compiled) |
| **Compiler** | 16,467 lines of Nova (lexer, parser, AST, IR, register allocator, x86-64 lowering, codegen) |
| **Core Types** | 3,559 lines (moment, signal, node, channel, path, similarity, soul, system) |
| **Mind Systems** | 2,690 lines (academic, experiential, emotion, memory, reasoning) |
| **Runtime** | 7,717 lines (syscall, alloc, string, io, scheduler, SIMD, tensor, BLAS, embedding, LLM, FFI, Python bridge, etc.) |
| **Agent** | 1,693 lines (cognitive agent, cognitive LLM pipeline, RAG) |
| **Total Nova** | ~65,000 lines across compiler, runtime, core, mind, agent, and package manager |
| **Tests** | 130 tests (124 pass, 6 skip) |
| **Targets** | Linux x86-64, macOS x86-64, WebAssembly (WASI), Windows x86-64 |

---

## What is Nova?

Nova is a compiled programming language designed for building AGI systems through **Moment-Signal Computing** -- a paradigm where cognition emerges from signals flowing through specialized processing nodes. Nova compiles to native x86-64 machine code via direct Linux and Windows syscalls with no C library, no garbage collector, and no runtime interpreter.

The language is **fully self-hosting**: the Nova compiler is written in Nova, bootstrapped from handwritten x86-64 assembly. The resulting native binary compiles its own source code to produce byte-identical output -- a verified fixed point.

**New in v4.0:** SSE2-vectorized SIMD operations, tiled matrix multiplication for cache efficiency, OpenBLAS FFI for large matrices, a cognitive LLM pipeline with confidence annotation and episodic memory, BM25-scored n-gram embeddings for competitive RAG, and a unified embedding interface with cognitive dimensions.

At its foundation, Nova is a practical systems language with structs, enums, lambdas, coroutines, pattern matching, try/catch/finally, 100+ built-in functions, and an arena allocator for deterministic memory management. You can write a TCP server with raw syscalls, parse JSON, manage processes, or do bitwise manipulation -- all without any external dependency.

What sets Nova apart is its first-class support for cognitive computing. Where other languages treat AI as a library concern, Nova builds it into the language itself:

- **Moments** capture structured experiences -- what happened, who was involved, what was felt, and what the consequences were.
- **Signals** carry moments between processing nodes with typed routing, priority, and trace metadata. Streaming signals enable lazy, incremental processing via `signal_stream_new()` and `stream_pipe()`.
- **Nodes** are specialized cognitive processors -- perceivers, knowers, rememberers, reasoners, feelers, and actors -- each with domain-specific computation. Dynamic node scaling via `node_pool_new()` auto-scales when queue depth exceeds thresholds.
- **Flow operators** (`~>`, `<~`, `=>>`, `<<~`, `~~>`, `<=>`, `|~>`) express signal routing as concisely as arithmetic.
- **Mind declarations** wire an entire cognitive architecture in a single declarative block.
- **Soul declarations** define first-class identity and behavior constructs -- purpose, values, drives, and feelings -- giving each agent a persistent personality.
- **System declarations** compose multiple minds, bridges between them, and a soul into a unified multi-mind agent.
- **Knowledge persistence** provides file-based key-value stores and knowledge graphs for long-term memory across sessions.
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

# Run all 130 tests
make test-all

# Verify self-hosting (stage2.s == stage3.s)
make self-host

# Syntax check without compiling
bin/nova myprogram.nova --check

# See codebase statistics
make stats
```

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

### Foreign Function Interface

- **Dynamic library loading** -- `ffi_open`, `ffi_sym`, `ffi_call` for calling C functions from Nova
- **Python bridge** -- `py_init`, `py_exec`, `py_eval`, `py_import`, `py_call` for bidirectional Python interop
- **LLM bridge** -- C bridge to llama.cpp for model loading, text generation, tokenization, and embedding extraction

### Knowledge & Persistence

- **File-based key-value store** -- `db_open`, `db_put`, `db_get`, `db_prefix`, `db_close` for persistent storage across sessions
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
- `match` expressions with literal patterns, guards, and wildcard default
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
- Structs with dot-notation field access and method-style calls
- Enums with variant access (`Op.Add`)
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
- Process management: `fork`, `exec`, `waitpid`, `pipe`
- File I/O, directory operations, environment variables
- JSON parsing and serialization
- `import` for source-level file inclusion
- Extern function declarations

### Compiler and Tooling

- Cross-compilation: `--target=linux` (default), `--target=macos`, `--target=wasm`, `--target=windows`
- `--check` for syntax validation without code generation
- `--stats` for compilation statistics
- `--version` and `--debug` flags
- Rich error messages with line numbers and source context
- List comprehensions: `[x * x for x in range(10)]`
- Map comprehensions
- Default parameters and named arguments
- Trailing commas, multiline strings
- Package manager: `nova pkg init`, `nova pkg install <name>`, `nova pkg build`

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
src/core/                  Cognitive architecture types              3,559 lines
  moment.nova                Experience records, entities, emotions
  signal.nova                Typed message passing with priority
  node.nova                  6 cognitive processor types
  channel.nova               Signal routing between nodes
  path.nova                  Named processing pipelines
  similarity.nova            Similarity computation for matching
  soul.nova                  Identity, values, drives, feelings
  system.nova                Multi-mind composition with bridges
src/mind/                  Mind systems                              2,690 lines
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
src/agent/                 Agent systems                             1,693 lines
  agent.nova                 Cognitive agent                           773 lines
  cognitive_llm.nova         Cognitive LLM pipeline (v4.0)             331 lines
  rag.nova                   RAG retrieval pipeline                    589 lines
src/pkg/pkg.nova           Package manager                            487 lines
examples/                  31 example programs                       3,125 lines
tests/                     130 test programs                         9,854 lines
```

**Total: ~65,000 lines of Nova + 106,045 lines of bootstrap assembly.**

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
make test-all       # Run all 130 tests
make run FILE=path  # Compile and run a .nova file
make examples       # Build and run all 29 examples
make agent          # Run the cognitive agent
make cross-macos    # Generate macOS x86-64 assembly
make cross-windows  # Generate Windows x86-64 PE32+ executable
make wasm FILE=path # Compile to WebAssembly and run (requires Node.js + wabt)
make stats          # Show codebase statistics
make clean          # Remove build artifacts
```

### Compiler Flags

```
bin/nova <input.nova> [-o output.s] [options]

  -o <file>              Output assembly file (default: output.s)
  --target=<t>           Target platform: linux, macos, wasm, windows
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

# WebAssembly (WASI)
make wasm FILE=examples/hello.nova
```

## Built-in Functions (180+)

### I/O
`print` `println` `print_int` `read_line` `read_file` `write_file`

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
`socket` `bind_socket` `listen_socket` `accept_conn` `connect_socket` `send_data` `recv_data` `close_fd` `make_sockaddr_in`

### Process
`fork_process` `waitpid` `exec_program` `pipe_create`

### Memory
`store64` `load64` `store8` `load8` `memcpy_raw`

### Soul
`soul_new` `soul_feel` `soul_drive_level` `soul_bias` `soul_tick` `soul_preprocess`

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

## Contributing

Contributions are welcome. The compiler is written entirely in Nova (`src/compiler/`), so you can read and modify it without knowing any other language.

To get started:

1. Read the code -- start with `src/compiler/compiler.nova` (entry point, 547 lines) and work outward
2. Make your changes
3. Run `make self-host` to verify the compiler can still compile itself
4. Run `make test-all` to check for regressions (124 of 130 tests should pass)

The cognitive architecture lives in `src/core/` (types, soul, system) and `src/mind/` (systems). The runtime is in `src/runtime/`. The agent systems (cognitive LLM, RAG) are in `src/agent/`. The 31 examples in `examples/` demonstrate most language features.

## License

See LICENSE.

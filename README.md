# Nova

**A self-hosting compiled language for AGI through Moment-Signal Computing.**

Compiles to native x86-64 machine code. Zero dependencies. No libc. Direct Linux syscalls.

---

| | |
|---|---|
| **Status** | Self-hosting verified (`stage2.s == stage3.s`) |
| **Version** | 0.2.0 |
| **Bootstrap** | 7,379 lines of handwritten x86-64 assembly |
| **Compiler** | 14,491 lines of Nova (lexer, parser, AST, IR, register allocator, x86-64 lowering, codegen) |
| **Core Types** | 2,052 lines (moment, signal, node, channel, path, similarity) |
| **Mind Systems** | 2,455 lines (academic, experiential, emotion, memory, reasoning) |
| **Runtime** | 2,694 lines (syscall, alloc, string, io, scheduler, json, coroutine, etc.) |
| **Total Nova** | ~22,000 lines across compiler, runtime, core, mind, and package manager |
| **Tests** | 114 tests (110 pass, 4 skip) |
| **Targets** | Linux x86-64, macOS x86-64, WebAssembly (WASI) |

---

## What is Nova?

Nova is a compiled programming language designed for building AGI systems through **Moment-Signal Computing** -- a paradigm where cognition emerges from signals flowing through specialized processing nodes. Nova compiles to native x86-64 machine code via direct Linux syscalls with no C library, no garbage collector, and no runtime interpreter.

The language is **fully self-hosting**: the Nova compiler is written in Nova, bootstrapped from 7,379 lines of handwritten x86-64 assembly. The resulting native binary compiles its own source code to produce byte-identical output -- a verified fixed point.

At its foundation, Nova is a practical systems language with structs, enums, lambdas, coroutines, pattern matching, try/catch/finally, 100+ built-in functions, and an arena allocator for deterministic memory management. You can write a TCP server with raw syscalls, parse JSON, manage processes, or do bitwise manipulation -- all without any external dependency.

What sets Nova apart is its first-class support for cognitive computing. Where other languages treat AI as a library concern, Nova builds it into the language itself:

- **Moments** capture structured experiences -- what happened, who was involved, what was felt, and what the consequences were.
- **Signals** carry moments between processing nodes with typed routing, priority, and trace metadata.
- **Nodes** are specialized cognitive processors -- perceivers, knowers, rememberers, reasoners, feelers, and actors -- each with domain-specific computation.
- **Flow operators** (`~>`, `<~`, `=>>`, `<<~`, `~~>`, `<=>`, `|~>`) express signal routing as concisely as arithmetic.
- **Mind declarations** wire an entire cognitive architecture in a single declarative block.

The result is a language where you can write a TCP server with raw syscalls on one line and declare a reasoning pipeline with memory enrichment on the next -- all compiling to the same native binary.

## Quick Start

```bash
# Clone and build
git clone https://github.com/amoufaq5/nova.git
cd nova

# Build the compiler (requires only GNU as + ld)
make

# Compile and run a program
make run FILE=examples/hello.nova

# Run all 114 tests
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
- **6 cognitive node types** -- perceiver, knower, rememberer, reasoner, feeler, actor
- **7 flow operators** -- `~>` forward, `<~` backward, `=>>` broadcast, `<<~` memory enrichment, `~~>` tentative, `<=>` resonance, `|~>` filtered
- **Mind declarations** -- `mind Nova { nodes { ... } channels { ... } }` for declarative cognitive architecture
- **Signal scheduler** -- priority-based dispatch with batching for cache-friendly processing
- **Path declarations** -- named signal processing pipelines with enrichment stages
- **5 mind systems** -- academic learning, experiential learning, emotion modeling, memory, reasoning

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

- Cross-compilation: `--target=linux` (default), `--target=macos`, `--target=wasm`
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
boot/nova_boot.s           Handwritten x86-64 assembly bootstrap    7,379 lines
src/compiler/              Self-hosting compiler in Nova            14,491 lines
  lexer.nova                 Tokenizer                                883 lines
  parser.nova                Recursive descent parser               1,858 lines
  ast.nova                   AST node definitions                     540 lines
  ir.nova                    Intermediate representation              486 lines
  regalloc.nova              Register allocator                       197 lines
  lower_x64.nova             x86-64 lowering                          676 lines
  codegen.nova               Code generation + runtime stubs        9,437 lines
  compiler.nova              Entry point, CLI, import resolution      414 lines
src/core/                  Cognitive architecture types              2,052 lines
  moment.nova                Experience records, entities, emotions
  signal.nova                Typed message passing with priority
  node.nova                  6 cognitive processor types
  channel.nova               Signal routing between nodes
  path.nova                  Named processing pipelines
  similarity.nova            Similarity computation for matching
src/mind/                  Mind systems                              2,455 lines
  academic.nova              Knowledge from axioms and rules
  experiential.nova          Learning from lived moments
  emotion.nova               Emotional state modeling
  memory.nova                Episodic memory store and recall
  reasoning.nova             Rule-based and case-based reasoning
src/runtime/               Runtime library                          2,694 lines
  syscall.nova               Raw Linux syscall wrappers
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
src/pkg/pkg.nova           Package manager                            466 lines
src/agent/agent.nova       Cognitive agent                             773 lines
examples/                  23 example programs                       1,975 lines
tests/                     114 test programs                         7,720 lines
```

**Total: ~22,000 lines of Nova + 7,379 lines of bootstrap assembly.**

## How It Works

Nova achieves self-hosting through a multi-stage bootstrap process:

1. **Handwritten bootstrap** (`boot/nova_boot.s`) -- a 7,379-line x86-64 assembly program that interprets Nova source code. It makes raw Linux syscalls directly; no libc is linked.
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
make test-all       # Run all 114 tests
make run FILE=path  # Compile and run a .nova file
make examples       # Build and run all 23 examples
make agent          # Run the cognitive agent
make cross-macos    # Generate macOS x86-64 assembly
make wasm FILE=path # Compile to WebAssembly and run (requires Node.js + wabt)
make stats          # Show codebase statistics
make clean          # Remove build artifacts
```

### Compiler Flags

```
bin/nova <input.nova> [-o output.s] [options]

  -o <file>              Output assembly file (default: output.s)
  --target=<t>           Target platform: linux, macos, wasm
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

# WebAssembly (WASI)
make wasm FILE=examples/hello.nova
```

## Built-in Functions (100+)

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

### Debug
`assert` `type_of` `debug_print`

See [docs/LANGUAGE_REFERENCE.md](docs/LANGUAGE_REFERENCE.md) for the complete language reference.

## Contributing

Contributions are welcome. The compiler is written entirely in Nova (`src/compiler/`), so you can read and modify it without knowing any other language.

To get started:

1. Read the code -- start with `src/compiler/compiler.nova` (entry point, 414 lines) and work outward
2. Make your changes
3. Run `make self-host` to verify the compiler can still compile itself
4. Run `make test-all` to check for regressions (110 of 114 tests should pass)

The cognitive architecture lives in `src/core/` (types) and `src/mind/` (systems). The runtime is in `src/runtime/`. The 23 examples in `examples/` demonstrate most language features.

## License

See LICENSE.

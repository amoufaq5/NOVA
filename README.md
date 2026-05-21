# Nova

**A self-hosting compiled language for Moment-Signal Computing and AGI.**

Self-hosting verified | x86-64 native | Zero dependencies | No libc | 22,000 lines

---

## What is Nova?

Nova is a compiled programming language designed for building AGI systems through **Moment-Signal Computing** — a paradigm where cognition emerges from signals flowing through specialized processing nodes. Nova compiles to native x86-64 machine code via direct Linux syscalls with no C library, no garbage collector, and no runtime interpreter.

The language is **fully self-hosting**: the Nova compiler is written in Nova, bootstrapped from 7,379 lines of handwritten x86-64 assembly. The resulting native binary compiles its own source code to produce an identical output — a verified fixed point.

Nova includes first-class primitives for cognitive architectures: six specialized node types (perceiver, knower, rememberer, reasoner, feeler, actor), signal routing operators, declarative mind definitions, and a signal scheduler with cache-friendly batching. These aren't libraries — they're part of the language syntax and compiler.

## Quick Start

```bash
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
```

## Hello World

```nova
fn main() {
    println("Hello, World!")
}

main()
```

No implicit entry point. Execution starts at the first top-level statement.

## Language Features

### Core Language
- **Functions** — first-class, recursive, closures, default parameters, named arguments
- **Variables** — `let` mutable, `const` constants, destructuring (`let [a, b] = list`)
- **Structs** — named records with dot access, method-style calls
- **Enums** — variant types (`enum Color { Red, Green, Blue }`)
- **Match expressions** — pattern matching with guards and wildcard
- **Error handling** — `try`/`catch`/`finally`, `throw`, `defer`
- **Coroutines** — `yield`, `coro_new`, `coro_resume`
- **Lambda expressions** — `|x| x * 2`
- **List/map comprehensions** — `[x * x for x in range(10)]`
- **Pipe operator** — `data |> transform |> output`
- **Inline assembly** — `asm { "mov rax, 1" "syscall" }`

### Control Flow
- `if`/`else if`/`else`, `unless`, ternary (`? :`), nullish coalescing (`??`)
- `while`, `until`, `do..while`, `loop`
- `for item in list`, `for i, item in list`, `for i in 0..10`, `for i in 0..=10`
- `match` with literal, wildcard, and guard patterns
- `break`, `continue`, labeled loops (`@outer`)

### Operators
- Arithmetic: `+` `-` `*` `/` `%` `**`
- Comparison: `==` `!=` `<` `>` `<=` `>=` (chainable: `1 < x < 10`)
- Logical: `&&` `||` `!` (short-circuit)
- Bitwise: `&` `|` `^` `~` `<<` `>>`
- Compound: `+=` `-=` `*=` `/=` `%=` `&=` `|=` `^=` `<<=` `>>=`
- Membership: `in`, `not in`, `is`

### Types and Literals
- 64-bit signed integers, hex (`0xFF`), octal (`0o777`), binary (`0b1010`)
- Numeric separators: `1_000_000`
- Strings with interpolation: `"Hello, ${name}!"`
- Multiline strings: `"""..."""`
- Dynamic lists: `[1, 2, 3]` with negative indexing
- Hash maps: `map_new()`, `map_set()`, `map_get()`
- Fixed-point floats: `3.14` (scaled by 1000)
- Booleans: `true` / `false`, None: `none`

### 100+ Built-in Functions
I/O, strings, lists, maps, math, networking, processes, memory, coroutines, higher-order functions (map, filter, reduce, any, all, zip, enumerate, flatten, unique, ...).

See [docs/LANGUAGE_REFERENCE.md](docs/LANGUAGE_REFERENCE.md) for the complete list.

## Moment-Signal Computing

Nova's cognitive architecture is built on three concepts:

### Moments
A **Moment** captures a unit of experience: what happened, who was involved, what was felt, what the consequences were, and what knowledge is relevant.

### Signals
**Signals** carry Moments between nodes. They have types (event, question, order, command, request), priorities, and trace their path through the system for cycle detection.

### Nodes
Six specialized **node types** process signals differently:

| Node | Role | Computation |
|------|------|-------------|
| **Perceiver** | Input classification | Weighted Jaccard template matching |
| **Knower** | Knowledge storage | Spreading activation on semantic graph |
| **Rememberer** | Episodic memory | Composite similarity scoring (content, entity, emotion, recency) |
| **Reasoner** | Inference | Multi-strategy: deductive, abductive, analogical, causal |
| **Feeler** | Emotion | Dimensional drift (valence, arousal, dominance) |
| **Actor** | Output/action | Competing activations (reason, emotion, habit, reflex) |

### Flow Operators

```nova
signal ~> node           // forward flow
node <~ signal           // backward/reflection
signal =>> [a, b, c]    // broadcast (one-to-many)
memory <<~ query         // memory enrichment
signal ~~> node          // tentative/weighted
node1 <=> node2          // bidirectional resonance
signal |~> node          // filtered flow
```

### Mind Declarations

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

This single declaration creates all nodes, registers them with the scheduler, and wires signal channels between them. Access nodes as `Nova_Sense`, `Nova_Think`, etc.

## Examples

**Cognitive agent processing input:**
```nova
import "src/core/moment.nova"
import "src/core/signal.nova"
import "src/core/node.nova"
import "src/core/channel.nova"
import "src/runtime/scheduler.nova"

mind Agent {
    nodes {
        Eye: perceiver
        Brain: reasoner
        Hand: actor
    }
    channels {
        see: Eye ~> Brain
        act: Brain ~> Hand
    }
}

fn main() {
    perceiver_add_template(Agent_Eye, "greeting", ["hello", "hi"], 80)
    reasoner_add_rule(Agent_Brain, "hello", "greeting detected")
    actor_add_habit(Agent_Hand, "respond", "wave", 60)

    let alice = entity_new("Alice", "user")
    let cons = consequence_new(CTYPE_EXPECTATION, "reply", 70)
    let m = moment_new("hello there!", alice, 75, 55, cons, 80)
    let sig = signal_event(m, "external", "Eye")

    scheduler_emit(sig)
    scheduler_run()

    print("Processed: ")
    print_int(scheduler_total_processed())
    println(" signals")
}

main()
```

**TCP echo server (raw syscalls, no libc):**
```nova
fn main() {
    let fd = socket(2, 1, 0)
    let addr = make_sockaddr_in(8080, 0)
    bind_socket(fd, addr, 16)
    listen_socket(fd, 5)
    println("Listening on :8080")

    while true {
        let client = accept_conn(fd, 0, 0)
        if client >= 0 {
            let buf = alloc(1024)
            let n = recv_data(client, buf, 1024)
            if n > 0 { send_data(client, buf, n) }
            close_fd(client)
        }
    }
}

main()
```

**Fibonacci with match:**
```nova
fn fib(n) {
    return match n {
        0 => 0
        1 => 1
        _ => fib(n - 1) + fib(n - 2)
    }
}

fn main() {
    for i in 0..20 {
        print_int(fib(i))
        print(" ")
    }
    println("")
}

main()
```

**Sieve of Eratosthenes:**
```nova
fn sieve(limit) {
    let is_prime = list_new()
    for i in 0..=limit { push(is_prime, 1) }
    list_set(is_prime, 0, 0)
    list_set(is_prime, 1, 0)

    let i = 2
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
    for i in 2..=limit {
        if is_prime[i] == 1 { push(primes, i) }
    }
    return primes
}
```

## Architecture

```
boot/nova_boot.s         Handwritten x86-64 assembly bootstrap    7,379 lines
src/compiler/            Self-hosting compiler (Nova)             14,491 lines
  lexer.nova               Tokenizer (883 tokens)
  parser.nova              Recursive descent parser (1,858 rules)
  ast.nova                 AST node definitions (540 types)
  ir.nova                  Intermediate representation (486)
  regalloc.nova            Register allocator (197)
  lower_x64.nova           x86-64 lowering (676)
  codegen.nova             Code generator + runtime stubs (9,437)
  compiler.nova            Main entry + import resolution (414)
src/core/                Cognitive types (Nova)                    2,052 lines
  moment.nova, signal.nova, node.nova, channel.nova, path.nova, similarity.nova
src/mind/                Learning & reasoning (Nova)               2,455 lines
  academic.nova, experiential.nova, emotion.nova, memory.nova, reasoning.nova
src/runtime/             Runtime library (Nova)                    2,694 lines
  syscall.nova, alloc.nova, string.nova, io.nova, scheduler.nova, json.nova, ...
src/pkg/                 Package manager (Nova)                      466 lines
examples/                23 example programs                       1,975 lines
tests/                   114 tests (110 pass, 4 skip)              7,720 lines
```

**Total: ~29,500 lines of Nova + 7,379 lines of assembly.**

## How It Works

```
                    interprets                  emits
boot/nova_boot.s  ───────────>  compiler.nova  ──────>  stage1.s
                                                            │
                                                     as + ld│
                                                            v
                                 compiler.nova  <────  bin/nova
                                       │                    │
                                       └── emits ──> stage2.s
                                                            │
                                              diff stage1.s stage2.s => identical
```

1. **Bootstrap** (`boot/nova_boot.s`) — 7,379 lines of x86-64 assembly that interpret Nova source
2. **Stage 1** — bootstrap interprets the compiler, emits x86-64 assembly
3. **Native binary** — GNU `as` + `ld` produce `bin/nova`
4. **Self-hosting** — `bin/nova` compiles its own source; output matches stage 1

## Performance

- **Native machine code** — no interpreter, no bytecode, no JIT
- **Zero dynamic dispatch** — all types resolved at compile time
- **Arena allocator** — mmap-backed bump allocation, instant reset (no GC pauses)
- **Signal batching** — groups signals by destination for cache-friendly dispatch
- **Direct syscalls** — no libc overhead, no dynamic linking
- **Memory primitives** — `store64`/`load64`/`store8`/`load8` for direct memory access

## Building

**Prerequisites:** GNU `as` and `ld` (standard GNU binutils). Nothing else.

```bash
make              # Build bin/nova
make self-host    # Verify self-hosting
make test-all     # Run 114 tests
make examples     # Build and run all examples
make clean        # Remove build artifacts
```

### Compiler Flags
```
bin/nova <input.nova> [-o output.s] [options]

  -o <file>              Output assembly file (default: output.s)
  --target=<t>           Target: linux, macos, wasm
  --check                Syntax check only (no codegen)
  --stats                Show compilation statistics
  --debug / --no-debug   Enable/disable debug info
  --version              Show compiler version

Subcommands:
  nova pkg init          Initialize package project
  nova pkg add <name>    Add dependency
  nova pkg list          List installed packages
```

## Documentation

- [Language Reference](docs/LANGUAGE_REFERENCE.md) — complete syntax, all built-in functions
- [Use Cases](docs/USE_CASES.md) — what you can build with Nova, with examples
- [Comparison with C and Python](docs/COMPARISON.md) — side-by-side code, performance, trade-offs
- [Examples](examples/) — 23 runnable programs

## Contributing

Contributions welcome. The compiler is written entirely in Nova (`src/compiler/`), so you can read and modify it without knowing any other language. Run `make self-host` after changes to verify the compiler still compiles itself.

## License

See LICENSE.

# NOVA: A 5-Minute Tour

> The short on-ramp. For exhaustive per-round detail see
> [`NEXT_SESSION.md`](../NEXT_SESSION.md) (it is large — you do not need
> to read it to get started).

## What is NOVA?

NOVA is a self-hosting, compiled programming language for building AGI
systems through **Moment-Signal Computing** — a paradigm where cognition
emerges from signals flowing through specialized processing nodes. It
compiles straight to native x86-64 machine code via direct Linux/Windows
syscalls: no libc, no garbage collector, no runtime interpreter. The
compiler is written in NOVA and bootstrapped from a handwritten x86-64
assembly seed; the native binary recompiles its own source to
byte-identical output — a verified self-hosting fixed point.

## Build in 60 seconds

```bash
git clone https://github.com/amoufaq5/nova.git
cd nova

make                              # build bin/nova (stage1 from the asm seed)
make run FILE=examples/hello.nova # compile + run a program
make self-host                    # verify self-hosting
```

`make self-host` is the integrity check that defines NOVA: it compiles
the compiler with itself across stages and asserts **`stage2.s ==
stage3.s`** byte-for-byte. If the binary the compiler produces can
produce an identical copy of itself, the compiler is a true fixed point.

Other handy targets: `make test-all` (full suite), `bin/nova prog.nova
--check` (syntax-only), `make stats` (codebase metrics).

## Your first cognitive program

Where most languages treat AI as a library, NOVA makes it syntax. A
`mind {}` block declares a cognitive architecture; flow operators route
signals between nodes. From [`examples/basic_mind.nova`](../examples/basic_mind.nova):

```nova
mind Agent {
    memory working(capacity: 7)         -- short-term store
    memory episodic(max_episodes: 10000) -- experiences over time
    memory semantic(structure: "graph")  -- knowledge graph

    perceive(input) {                   -- intake handler
        store(input, in: "working")
        return input
    }

    think(goal) {                       -- recall + predict
        let knowledge = recall(goal, from: "semantic", limit: 10)
        return knowledge
    }

    learn(event) {
        store(event, in: "episodic")
    }
}
```

Signals flow between nodes with **flow operators** (see
[`examples/flow_operators.nova`](../examples/flow_operators.nova)):

```nova
let result = sig ~> perceiver ~> reasoner   -- ~>  forward flow
let count  = sig =>> [reasoner, feeler]     -- =>> broadcast
let weak   = sig ~~> feeler                 -- ~~> tentative (low priority)
let passed = sig |~> perceiver              -- |~> filtered by salience
let back   = sig <~ reasoner                -- <~  backward (reflection)
```

The seven operators are `~>` (forward), `<~` (backward), `=>>`
(broadcast), `<<~` (memory enrichment), `~~>` (tentative), `<=>`
(resonance), `|~>` (filtered).

## Where things live

| Path           | What it holds                                                        |
|----------------|---------------------------------------------------------------------|
| `src/compiler` | The NOVA compiler in NOVA: lexer, parser, AST, IR, regalloc, x86-64 lowering, codegen. |
| `src/runtime`  | Runtime: syscalls, arena allocator, strings, I/O, scheduler, SIMD, tensor/BLAS, FFI.   |
| `src/core`     | Core cognitive types: moment, signal, node, channel, path, soul, system, belief, goal. |
| `src/mind`     | Mind systems: academic/experiential learning, emotion, memory, reasoning.              |
| `src/agent`    | Cognitive agent, multi-loop pipeline, RAG, LLM preprocessing.                          |
| `src/stdlib`   | Standard library: list combinators, float utilities, strings.                          |

(See also `src/cognitive`, `src/pkg` (package manager), and
`src/tooling`.)

## Next steps

- [`docs/GETTING_STARTED.md`](GETTING_STARTED.md) — full setup for Linux / Windows WSL2 / macOS.
- [`docs/IDE_SETUP.md`](IDE_SETUP.md) — VS Code: tree-sitter syntax + `nova-lsp` + `nova-dap`.
- [`docs/adr/`](adr/) — architecture decisions (ADR 0002 covers the self-hosting bootstrap).
- [`NEXT_SESSION.md`](../NEXT_SESSION.md) — deep per-round development log.
- [`INSTALL.md`](../INSTALL.md) — the seven install paths once a release is published.

# Nova

**A self-hosting compiled language for x86-64 Linux. Zero dependencies. No libc.**

Self-hosting verified | x86-64 Linux | v0.2.0

---

## What is Nova?

Nova is a compiled programming language that bootstraps itself from handwritten x86-64 assembly. The Nova compiler is written in Nova, compiled by a ~7,200-line assembly bootstrap, and the resulting native binary can compile its own source code -- completing the self-hosting loop. Nova targets x86-64 Linux directly via raw syscalls with no C library, no runtime interpreter, and no garbage collector. Beyond general-purpose programming, Nova includes domain primitives for cognitive architectures: memory systems, signal processing, and reasoning pipelines designed for building autonomous agents.

## Quick Start

```bash
# Build the compiler
make

# Compile and run a program
make run FILE=examples/hello.nova

# Run all tests
make test-all

# Verify self-hosting (stage 2 output == stage 1 output)
make self-host

# See codebase stats
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

## Language Features

### Core
- **Functions** -- first-class declarations, recursion, forward references
- **First-class function references** -- assign to variables, pass as arguments, store in lists
- **Variables** -- `let` for mutable, `const` for named constants
- **Structs** -- named record types with dot-notation field access and assignment

### Control Flow
- `if` / `else if` / `else`
- `while` loops with `break` and `continue`
- `for item in list` iteration
- `for i in range(n)` and `for i in range(a, b)` range loops
- `match` expressions with literal patterns and wildcard default arm

### Types and Literals
- **Integers** -- 64-bit signed
- **Strings** -- null-terminated, immutable, with escape sequences (`\n`, `\t`, `\\`, `\"`, `\0`)
- **Booleans** -- `true` / `false`
- **Lists** -- dynamic arrays with bracket indexing and negative indices
- **Maps** -- hash maps via built-in functions
- **Fixed-point floats** -- `3.14` stored as `3140` (scale factor 1000), with `float_mul`, `float_div`, `float_to_str`
- **Hex** (`0xFF`), **octal** (`0o777`), **binary** (`0b1010`) integer literals

### Operators
- Arithmetic: `+` `-` `*` `/` `%`
- String concatenation via `+`
- Comparison: `==` `!=` `<` `>` `<=` `>=`
- Logical: `&&` `||` `!` (short-circuit)
- Bitwise: `&` `|` `^` `<<` `>>`

### Other
- `import "path/to/file.nova"` -- source-level file inclusion
- Comments with `//`

## Built-in Functions

### I/O
`print(s)` `println(s)` `print_int(n)` `read_line()` `read_file(path)` `write_file(path, data)`

### Strings
`len(s)` `concat(a, b)` `substr(s, start, len)` `char_at(s, idx)` `chr(code)` `int_to_str(n)` `str_to_int(s)` `starts_with(s, prefix)` `ends_with(s, suffix)` `str_find(haystack, needle)` `split(s, delim)` `join(list, sep)` `hex(n)`

### Lists
`list_new()` `push(list, val)` `pop(list)` `len(list)` `list_set(list, idx, val)` `contains(list, val)` `list_remove(list, idx)` `reverse(list)` `sort(list)`

### Maps
`map_new()` `map_set(m, key, val)` `map_get(m, key)` `map_has(m, key)`

### Math
`abs(n)` `min(a, b)` `max(a, b)` `random(max)` `random_seed(n)`

### Fixed-Point Float
`float_mul(a, b)` `float_div(a, b)` `float_to_str(f)` `to_float(n)` `from_float(f)`

### System
`exit(code)` `time()` `sleep_ms(ms)` `getenv(name)` `mkdir(path)` `unlink(path)` `file_size(path)` `alloc(size)`

### Network
`socket(domain, type, proto)` `bind_socket(fd, addr, len)` `listen_socket(fd, backlog)` `accept_conn(fd, addr, len)` `connect_socket(fd, addr, len)` `send_data(fd, buf, len)` `recv_data(fd, buf, len)` `close_fd(fd)` `make_sockaddr_in(port, ip)`

### Process
`fork_process()` `waitpid(pid)` `exec_program(path, argv)` `pipe_create()`

### Debug
`assert(cond, msg)` `type_of(val)` `debug_print(label, val)`

See [docs/LANGUAGE_REFERENCE.md](docs/LANGUAGE_REFERENCE.md) for full details.

## Architecture

```
boot/nova_boot.s       Handwritten x86-64 assembly bootstrap (7,183 lines)
src/compiler/          Self-hosting compiler written in Nova (4,653 lines)
  lexer.nova             Tokenizer
  parser.nova            Recursive descent parser
  ast.nova               AST node definitions
  codegen.nova           x86-64 code generator + runtime library
  compiler.nova          Main entry point
src/core/              Cognitive architecture types (545 lines)
src/mind/              Learning, memory, emotion, reasoning (437 lines)
src/runtime/           Runtime library: allocator, I/O, strings, scheduler (1,347 lines)
examples/              27 example programs (1,389 lines)
tests/                 24 test programs + test runner (1,272 lines)
docs/                  Language reference
```

**Total Nova source: ~71,000 lines.**

## How It Works

Nova achieves self-hosting through a four-stage process:

1. **Handwritten bootstrap** (`boot/nova_boot.s`) -- a ~7,200-line x86-64 assembly program that can interpret Nova source code. It makes raw Linux syscalls directly; no libc is linked.
2. **Stage 1 compilation** -- the bootstrap interprets the Nova compiler source (`src/compiler/*.nova`) and uses it to emit x86-64 assembly for the compiler itself.
3. **Native binary** -- GNU `as` and `ld` assemble and link the stage 1 output into `bin/nova`, a native executable.
4. **Self-hosting verification** -- `bin/nova` compiles its own source to produce stage 2 assembly. `make self-host` confirms the stage 2 output is byte-identical to stage 1.

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
                                              diff stage1.s stage2.s  =>  identical
```

## Performance

- Compiles to native x86-64 machine code -- no interpreter, no bytecode
- All types resolved at compile time -- zero dynamic dispatch
- Bump allocator with arena reset -- no garbage collector, no `malloc`
- Direct Linux syscalls -- no C library overhead
- The compiler itself runs as a native binary after the first bootstrap

## Examples

**Fibonacci with recursion:**
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
        print_int(fib(i))
        println("")
        i = i + 1
    }
}

main()
```

**Sieve of Eratosthenes:**
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

**TCP echo server (no libc, raw syscalls):**
```nova
fn main() {
    let server_fd = socket(2, 1, 0)
    let addr = make_sockaddr_in(8080, 0)
    bind_socket(server_fd, addr, 16)
    listen_socket(server_fd, 5)
    println("Listening on port 8080...")

    while 1 == 1 {
        let client_fd = accept_conn(server_fd, 0, 0)
        if client_fd >= 0 {
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

## Building

**Prerequisites:** GNU `as` and `ld` (standard GNU binutils, pre-installed on virtually every Linux system).

That's it. No other dependencies. No C compiler. No package manager.

```bash
make            # Build bin/nova
make self-host  # Verify self-hosting
make test-all   # Run full test suite
make examples   # Build and run all examples
make clean      # Remove build artifacts
```

## Contributing

Contributions are welcome. The compiler is written entirely in Nova (`src/compiler/`), so you can read and modify it without knowing any other language. Run `make self-host` after changes to verify the compiler can still compile itself.

## License

See LICENSE.

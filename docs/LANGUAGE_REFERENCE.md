# Nova Language Reference

Nova is a self-hosting compiled programming language for building AGI through
Moment-Signal Computing. Zero dependencies. No libc. Direct syscalls only.
Compiles to native x86-64 machine code.

## Types

### Primitive Types
- **Integers**: 64-bit signed (`let x = 42`)
- **Strings**: Null-terminated, immutable (`let s = "hello"`)
- **Booleans**: `true` (1), `false` (0)
- **None**: `none` (0)
- **Lists**: Dynamic arrays (`let a = [1, 2, 3]`)
- **Maps**: Hash maps (`let m = map_new()`)

### Literals
- Decimal: `42`, `-7`
- Hex: `0xFF`, `0x1A3`
- Octal: `0o777`
- Binary: `0b1010`
- Numeric separators: `1_000_000`, `0xFF_FF`
- Float (fixed-point): `3.14` (stored as 3140, scaled by 1000)
- String: `"hello\nworld"`
- Multiline string: `"""..."""`
- String interpolation: `"x = ${x}"`
- List: `[1, 2, 3]`
- Map: `{key: value}`

### String Escape Sequences
- `\n` — newline
- `\t` — tab
- `\\` — backslash
- `\"` — double quote
- `\0` — null byte

## Variables

```nova
let x = 10          // mutable variable
const MAX = 100     // compile-time constant
x = 20              // reassignment
x += 5              // compound assignment (also -=, *=, /=, %=)
x &= 0xFF           // bitwise compound assignment (also |=, ^=, <<=, >>=)
let [a, b] = [1, 2] // destructuring
let [first, ...rest] = [1, 2, 3, 4] // rest pattern: first=1, rest=[2,3,4]
```

### Rest Parameters and Spread

```nova
// Rest parameters collect remaining arguments into a list
fn log_all(label, ...args) {
    print(label + ": ")
    for arg in args {
        print(to_str(arg) + " ")
    }
    println("")
}
log_all("values", 1, 2, 3)  // values: 1 2 3

// Spread in function calls expands a list into individual arguments
let nums = [10, 20, 30]
some_fn(...nums)  // equivalent to some_fn(10, 20, 30)
```

## Functions

```nova
fn add(a, b) {
    return a + b
}

fn greet(name) {
    println("Hello, " + name + "!")
}

// Default parameters
fn connect(host, port, timeout = 30) {
    // ...
}

// Named arguments at call site
connect(host = "localhost", port = 8080)

// Lambda expressions
let double = |x| x * 2
let add = |a, b| { a + b }

// First-class function references
let f = add
f(1, 2)
```

Functions can be called before declaration (forward references resolved).
The last expression in a function body is its implicit return value.

## Control Flow

```nova
// If-else
if condition {
    // ...
} else if other {
    // ...
} else {
    // ...
}

// If as expression
let val = if x > 0 { "positive" } else { "non-positive" }

// Unless (negated if)
unless done {
    println("still working")
}

// While loop
while condition {
    break
    continue
}

// Until loop (negated while)
until converged {
    step()
}

// Do-while
do {
    attempt()
} while !success

// For-in loop
for item in my_list {
    // ...
}

// For-in with index
for i, item in my_list {
    // ...
}

// Range loops
for i in range(10) { }       // 0..9
for i in range(5, 10) { }    // 5..9
for i in 0..10 { }           // 0..9
for i in 0..=10 { }          // 0..10 (inclusive)
for i in range_step(0, 20, 3) { } // 0, 3, 6, ...

// For-else (runs else if loop completes without break)
for item in list {
    if item == target { break }
} else {
    println("not found")
}

// While-else (runs else if loop completes without break)
while condition {
    if found { break }
} else {
    println("loop ended naturally")
}

// Labeled loops
@outer for i in range(10) {
    for j in range(10) {
        break @outer
    }
}

// Loop (infinite)
loop {
    if done { break }
}
```

## Match Expression

```nova
match value {
    1 => println("one")
    2 | 3 => println("two or three")
    x if x > 10 => println("big: " + int_to_str(x))
    _ => println("other")
}

// Match as expression
let name = match code {
    200 => "OK"
    404 => "Not Found"
    _ => "Unknown"
}
```

## Error Handling

```nova
try {
    risky_operation()
} catch {
    let err = get_error()
    println("caught: " + err)
} finally {
    cleanup()
}

// Throw errors
throw "something went wrong"

// Defer (runs at function exit)
defer {
    file_close(f)
}

// Guard clause (early exit if condition is false)
guard x > 0 else {
    return -1
}
// execution continues here only if x > 0
```

## Operators

### Arithmetic
| Operator | Description |
|----------|-------------|
| `+` | Addition / string concatenation |
| `-` | Subtraction |
| `*` | Multiplication / string repeat |
| `/` | Integer division |
| `%` | Modulo |
| `**` | Power |

### Comparison
| Operator | Description |
|----------|-------------|
| `==` | Equal |
| `!=` | Not equal |
| `<` | Less than |
| `>` | Greater than |
| `<=` | Less than or equal |
| `>=` | Greater than or equal |
| `is` | Type/identity check |
| `in` | Membership test |
| `not in` | Negated membership |

Chained comparisons: `1 < x < 10` works as `1 < x && x < 10`.

### Logical
| Operator | Description |
|----------|-------------|
| `&&` | Short-circuit AND |
| `\|\|` | Short-circuit OR |
| `!` | Logical NOT |

### Bitwise
| Operator | Description |
|----------|-------------|
| `&` | AND |
| `\|` | OR |
| `^` | XOR |
| `~` | NOT |
| `<<` | Left shift |
| `>>` | Right shift |

### Pipe
```nova
value |> transform |> format |> print
```

### Ternary
```nova
let x = condition ? value_if_true : value_if_false
```

### Nullish Coalescing
```nova
let val = maybe_null ?? default_value
```

## Flow Operators (Moment-Signal Computing)

| Operator | Name | Description |
|----------|------|-------------|
| `~>` | Forward flow | Signal flows from source to destination |
| `<~` | Backward flow | Reflection/feedback signal |
| `=>>` | Broadcast | One-to-many signal distribution |
| `<<~` | Memory enrichment | Query memory for context |
| `~~>` | Tentative flow | Weighted/uncertain signal |
| `<=>` | Resonance | Bidirectional synchronization |
| `\|~>` | Filtered flow | Conditional signal routing |

```nova
// Flow expressions
signal ~> node           // forward
node <~ signal           // backward
signal =>> [a, b, c]    // broadcast
memory <<~ query         // enrichment
```

## Structs

```nova
struct Point {
    x,
    y
}

let p = Point(10, 20)
print_int(p.x)
p.x = 30

// Method-style calls (syntactic sugar)
p.distance(other)  // calls distance(p, other)
```

## Enums

```nova
enum Color {
    Red,
    Green,
    Blue
}

let c = Color.Red
```

## Imports

```nova
import "path/to/file.nova"
import "std/math"           // standard library
```

## Mind Declaration

Declarative cognitive architecture setup. Creates nodes, registers them with
the scheduler, and wires channels between them.

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

// Desugars to: node creation, scheduler registration, channel wiring.
// Access nodes as Nova_Sense, Nova_Think, etc.
// Access channels as Nova_ch_perception, Nova_ch_reasoning, etc.
```

### Node Types
| Type | Role | Computation |
|------|------|-------------|
| `perceiver` | Input classification | Template matching (weighted Jaccard) |
| `knower` | Knowledge storage | Spreading activation on semantic graph |
| `rememberer` | Episodic memory | Composite similarity scoring |
| `reasoner` | Inference | Multi-strategy (deductive, abductive, analogical) |
| `feeler` | Emotion | Dimensional drift (valence, arousal, dominance) |
| `actor` | Output/action | Competing activations |

## Inline Assembly

```nova
fn sys_write(fd, buf, count) {
    asm {
        "mov rax, 1"
        "mov rdi, [rbp-8]"
        "mov rsi, [rbp-16]"
        "mov rdx, [rbp-24]"
        "syscall"
    }
}
```

Each line inside `asm {}` must be a string literal containing one x86-64
instruction (Intel syntax). The return value is in `rax`.

## Coroutines

Nova provides cooperative coroutines via built-in functions. Use `coro_yield()`
inside a coroutine function to suspend and produce a value.

```nova
fn counter(start) {
    let i = start
    while i < start + 3 {
        coro_yield(i)
        i = i + 1
    }
    return 999
}

let c = coro_new(counter, 0)
println(int_to_str(coro_resume(c)))  // 0
println(int_to_str(coro_resume(c)))  // 1
println(int_to_str(coro_resume(c)))  // 2
println(int_to_str(coro_resume(c)))  // 999 (return value)
println(int_to_str(coro_done(c)))    // 1 (true)
println(int_to_str(coro_result(c)))  // 999 (final return value)
println(int_to_str(coro_state(c)))   // 3 (done)
```

| Function | Description |
|----------|-------------|
| `coro_new(fn, arg)` | Create coroutine from function with one argument |
| `coro_resume(c)` | Resume coroutine, returns yielded/returned value |
| `coro_yield(val)` | Suspend coroutine and produce a value (call inside coroutine) |
| `coro_done(c)` | Returns 1 if coroutine has finished, 0 otherwise |
| `coro_result(c)` | Returns the final return value after coroutine completes |
| `coro_state(c)` | Returns state: 0=ready, 1=running, 2=suspended, 3=done |

## List Comprehensions

```nova
let squares = [x * x for x in range(10)]
let evens = [x for x in range(20) if x % 2 == 0]
```

## Map Comprehensions

```nova
let m = {k: v * 2 for k, v in items}
```

## Built-in Functions

### I/O
| Function | Description |
|----------|-------------|
| `print(s)` | Print string to stdout |
| `println(s)` | Print string + newline |
| `print_int(n)` | Print integer |
| `read_line()` | Read line from stdin |
| `read_stdin(n)` | Read n bytes from stdin |
| `read_file(path)` | Read entire file as string |
| `write_file(path, data)` | Write string to file |
| `__arg(n)` | Command-line argument at index n (0-indexed), returns "" if missing |

### Strings
| Function | Description |
|----------|-------------|
| `len(s)` | Length (strings and lists) |
| `concat(a, b)` | Concatenate strings |
| `substr(s, start, len)` | Extract substring |
| `char_at(s, idx)` | Char code at index |
| `chr(code)` | Code to 1-char string |
| `int_to_str(n)` | Integer to string |
| `str_to_int(s)` | Parse string as integer |
| `to_str(v)` | Any value to string |
| `starts_with(s, prefix)` | Prefix check |
| `ends_with(s, suffix)` | Suffix check |
| `str_find(s, needle)` | Find substring (returns index or -1) |
| `split(s, delim)` | Split by delimiter |
| `join(list, sep)` | Join with separator |
| `str_replace(s, old, new)` | Replace occurrences |
| `str_repeat(s, n)` | Repeat string n times |
| `str_upper(s)` | Uppercase |
| `str_lower(s)` | Lowercase |
| `str_trim(s)` | Strip whitespace |
| `str_count(s, sub)` | Count occurrences |
| `hex(n)` | Integer to hex string |
| `char_code(s)` | First char to code |
| `chars(s)` | String to list of char codes |
| `contains(s, sub)` | Check if contains |
| `strcmp(a, b)` | Compare (-1, 0, 1) |
| `str_eq(a, b)` | String equality check (returns 1 or 0) |
| `pad_left(s, n, ch)` | Left-pad to width |
| `pad_right(s, n, ch)` | Right-pad to width |
| `to_int(s)` | Parse string as integer (alias for `str_to_int`) |

### Lists
| Function | Description |
|----------|-------------|
| `list_new()` | Create empty list |
| `push(list, val)` | Append |
| `pop(list)` | Remove and return last |
| `len(list)` | Length |
| `list_set(list, i, val)` | Set at index |
| `list_remove(list, i)` | Remove at index |
| `list_copy(list)` | Shallow copy |
| `list_slice(list, start, end)` | Slice |
| `append_list(a, b)` | Concatenate lists |
| `contains(list, val)` | Membership |
| `index_of(list, val)` | First index of value |
| `last_index_of(list, val)` | Last index |
| `reverse(list)` | Reversed copy |
| `sort(list)` | Sort in place |
| `unique(list)` | Remove duplicates |
| `flatten(list)` | Flatten nested lists |
| `zip(a, b)` | Zip two lists |
| `enumerate(list)` | List of [index, value] |

### Higher-Order Functions
| Function | Description |
|----------|-------------|
| `map_list(list, fn)` | Apply fn to each element |
| `filter(list, fn)` | Keep elements where fn returns true |
| `reduce(list, fn, init)` | Fold list with fn |
| `foreach(list, fn)` | Call fn on each element |
| `flat_map(list, fn)` | Map then flatten |
| `any(list, fn)` | True if fn true for any |
| `all(list, fn)` | True if fn true for all |
| `sum(list)` | Sum of elements |
| `product(list)` | Product of elements |
| `min_list(list)` | Minimum element |
| `max_list(list)` | Maximum element |

### Maps
| Function | Description |
|----------|-------------|
| `map_new()` | Create empty map |
| `map_set(m, key, val)` | Set key-value |
| `map_get(m, key)` | Get by key |
| `map_has(m, key)` | Check key exists |
| `map_remove(m, key)` | Remove key |
| `map_count(m)` | Number of entries |
| `map_merge(a, b)` | Merge two maps |
| `keys(m)` | List of keys |
| `values(m)` | List of values |

### Ranges
| Function | Description |
|----------|-------------|
| `range(n)` | Iterable range 0 to n-1 |
| `range(start, end)` | Iterable range start to end-1 |
| `range_step(start, end, step)` | Iterable range with custom step |
| `range_list(start, end)` | Create list `[start, start+1, ..., end-1]` |

### Math
| Function | Description |
|----------|-------------|
| `abs(n)` | Absolute value |
| `min(a, b)` | Minimum |
| `max(a, b)` | Maximum |
| `random(max)` | Random 0 to max-1 |
| `random_seed(n)` | Seed PRNG |
| `int_add(a, b)` | Raw integer add (no string concat) |
| `int_sub(a, b)` | Raw integer subtract |
| `int_mul(a, b)` | Raw integer multiply |
| `int_div(a, b)` | Raw integer divide |
| `int_mod(a, b)` | Raw integer modulo |
| `float_mul(a, b)` | Fixed-point multiply |
| `float_div(a, b)` | Fixed-point divide |
| `to_float(n)` | Integer to fixed-point |
| `from_float(f)` | Fixed-point to integer |
| `float_to_str(f)` | Fixed-point to string |

### Memory Primitives
| Function | Description |
|----------|-------------|
| `alloc(size)` | Allocate raw memory (bump allocator) |
| `store64(addr, val)` | Write 64-bit value to address |
| `load64(addr)` | Read 64-bit value from address |
| `store8(addr, val)` | Write byte to address |
| `load8(addr)` | Read byte from address |
| `memcpy_raw(dst, src, n)` | Copy n bytes |

### System
| Function | Description |
|----------|-------------|
| `exit(code)` | Exit with status |
| `time()` | Epoch time in seconds |
| `sleep_ms(ms)` | Sleep milliseconds |
| `getenv(name)` | Environment variable |
| `mkdir(path)` | Create directory |
| `unlink(path)` | Delete file |
| `file_size(path)` | File size in bytes |

### Network
| Function | Description |
|----------|-------------|
| `socket(domain, type, proto)` | Create socket |
| `bind_socket(fd, addr, len)` | Bind socket |
| `listen_socket(fd, backlog)` | Listen |
| `accept_conn(fd, addr, len)` | Accept connection |
| `connect_socket(fd, addr, len)` | Connect |
| `send_data(fd, buf, len)` | Send data |
| `recv_data(fd, buf, len)` | Receive data |
| `close_fd(fd)` | Close descriptor |
| `make_sockaddr_in(port, ip)` | Create address struct |

### Process
| Function | Description |
|----------|-------------|
| `fork_process()` | Fork process |
| `waitpid(pid)` | Wait for child |
| `exec_program(path, argv)` | Replace process |
| `pipe_create()` | Create pipe `[read_fd, write_fd]` |

### Error Retrieval
| Function | Description |
|----------|-------------|
| `get_error()` | Returns the error value from the last `throw` (use inside `catch`) |

### Debug
| Function | Description |
|----------|-------------|
| `assert(cond, msg)` | Assert or exit |
| `type_of(val)` | Type tag: 0=null, 1=int, 2=str, 3=list |
| `typename(val)` | Type as string |
| `debug_print(label, val)` | Print label + value |

## Runtime Modules

The runtime provides higher-level functionality built on the compiler built-ins.
Include via `import` in your programs.

### `src/runtime/syscall.nova`
Direct Linux syscall wrappers using inline assembly:
`sys_read`, `sys_write`, `sys_open`, `sys_close`, `sys_lseek`,
`sys_mmap`, `sys_munmap`, `sys_brk`, `sys_exit`, `sys_getcwd`,
`sys_mkdir`, `sys_unlink`, `sys_fstat`

### `src/runtime/alloc.nova`
- **Arena allocator**: `arena_init()`, `arena_alloc(size)`, `arena_reset()`, `arena_used()`
  Ultra-fast bump allocation; reset frees everything at once.
- **Heap allocator**: `rt_alloc(size)`, `rt_free(ptr)`, `rt_realloc(ptr, size)`
  General-purpose with free list and mmap fallback for large blocks.

### `src/runtime/string.nova`
Length-prefixed string operations: `str_new`, `str_len`, `str_data`,
`str_char_at`, `str_concat`, `str_slice`, `rt_str_eq`, `str_cmp`,
`rt_str_find`, `str_contains`, `str_starts_with`, `str_ends_with`,
`str_split`, `rt_str_trim`, `rt_str_to_int`, `rt_int_to_str`

### `src/runtime/io.nova`
Buffered file I/O: `file_open`, `file_close`, `file_read`, `file_read_line`,
`file_read_all`, `file_write`, `file_write_line`, `file_flush`,
`io_read_file`, `io_write_file`, `io_print`, `io_println`, `io_input`

### `src/runtime/scheduler.nova`
Signal dispatch engine:
- **Registration**: `scheduler_init`, `scheduler_register`, `scheduler_add_channel`
- **Submission**: `scheduler_emit`, `scheduler_broadcast`
- **Execution**: `scheduler_run` (sequential), `scheduler_run_batched` (cache-friendly)
- **Queries**: `scheduler_queue_size`, `scheduler_node_count`, `scheduler_cycle_count`,
  `scheduler_total_processed`, `scheduler_total_dropped`, `scheduler_total_batched`

### `src/runtime/coroutine.nova`
Higher-level coroutine abstractions:
- **Generators**: `gen_new`, `gen_has_next`, `gen_next`, `gen_collect`, `gen_take`, `gen_skip`
- **Task groups**: `task_group_new`, `task_group_add`, `task_group_run`
- **Cooperative scheduler**: `scheduler_new`, `scheduler_spawn`, `scheduler_tick`,
  `scheduler_run_all`, `scheduler_active_count`
- **Pipelines**: `pipeline_new`, `pipeline_add_stage`, `pipeline_run`

### `src/runtime/chan.nova`
Go-style buffered channels for inter-coroutine communication:
`chan_new`, `chan_send`, `chan_recv`, `chan_try_send`, `chan_try_recv`,
`chan_close`, `chan_closed`, `chan_len`, `chan_is_full`, `chan_is_empty`,
`chan_drain`, `chan_cap`, `chan_send_count`, `chan_recv_count`

### `src/runtime/json.nova`
JSON parser and serializer:
`json_parse`, `json_stringify`, `json_get`, `json_set`,
`json_load` (from file), `json_save` (to file)

### `src/runtime/math.nova`
Integer math utilities:
`math_abs`, `math_sign`, `math_clamp`, `math_min`, `math_max`, `math_min3`,
`math_max3`, `math_gcd`, `math_lcm`, `math_sqrt`, `math_pow`, `math_factorial`,
`math_is_even`, `math_is_odd`, `math_is_prime`, `math_fibonacci`, `math_sum`,
`math_product`, `math_average`, `math_lerp`, `math_map_range`, `math_digits`,
`math_digital_root`

### `src/runtime/path.nova`
File path utilities:
`path_join`, `path_basename`, `path_dirname`, `path_extension`, `path_stem`,
`path_is_absolute`, `path_normalize`, `path_change_ext`

### `src/runtime/set.nova`
Set data structure (backed by maps):
`set_new`, `set_add`, `set_has`, `set_remove`, `set_size`, `set_items`,
`set_from_list`, `set_to_list`, `set_union`, `set_intersect`, `set_diff`,
`set_symmetric_diff`, `set_is_subset`, `set_is_superset`, `set_equals`,
`set_clear`, `set_print`

### `src/runtime/list.nova`
Low-level dynamic list implementation using raw memory (`alloc`/`store64`/`load64`).
Used internally; prefer built-in list operations for most code.

### `src/runtime/map.nova`
Low-level hash map implementation using open addressing with linear probing.
Used internally; prefer built-in map operations for most code.

### `src/runtime/taskpool.nova`
Process-based parallelism using `fork`/`pipe`:
`task_run`, `parallel_map_int`, `parallel_run_all`, `parallel_reduce_int`

## Compilation

```bash
# Build the compiler
make

# Compile and run
bin/nova myprogram.nova -o /tmp/out.s
as -o /tmp/out.o /tmp/out.s
ld -o /tmp/out /tmp/out.o
/tmp/out

# Or use the Makefile shortcut
make run FILE=examples/hello.nova

# Syntax check only
bin/nova myprogram.nova --check

# Show compilation stats
bin/nova myprogram.nova -o out.s --stats

# Cross-compilation
bin/nova myprogram.nova -o out.s --target=macos
bin/nova myprogram.nova -o out.s --target=wasm

# Package management
bin/nova pkg init
bin/nova pkg add <package>
bin/nova pkg list
```

## Self-Hosting

Nova compiles itself. Verification:

```bash
# Stage 2: compile compiler with current binary
cat src/compiler/*.nova src/pkg/pkg.nova > /tmp/combined.nova
bin/nova /tmp/combined.nova -o /tmp/stage2.s
as -o /tmp/stage2.o /tmp/stage2.s
ld -o /tmp/stage2 /tmp/stage2.o

# Stage 3: compile compiler with stage 2
/tmp/stage2 /tmp/combined.nova -o /tmp/stage3.s

# Verify: stage2.s == stage3.s (fixed point reached)
diff /tmp/stage2.s /tmp/stage3.s
```

## Program Structure

A Nova program is a sequence of top-level statements: function declarations,
struct definitions, variable declarations, imports, mind declarations, and
expressions. There is no implicit `main` entry point — execution begins at
the first top-level statement. By convention:

```nova
fn main() {
    println("Hello, Nova!")
}

main()
```

# Nova Language Reference

Nova is a self-hosting compiled programming language for building AGI through
Moment-Signal Computing. Zero dependencies. No libc. Direct syscalls only.
Compiles to native x86-64 machine code on Linux and Windows.

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

## Soul Declaration

Soul declarations define the behavioral identity of a cognitive system, including
its core purpose, ethical values, drives (persistent motivational intensities),
and feelings (short-term emotional state). The soul integrates with the scheduler
and biases actor node outputs. Access soul properties at runtime via soul functions.

```nova
soul Aurora {
    identity {
        purpose: "understand and assist"
        nature: "curious, careful, honest"
    }
    values {
        truth: "never fabricate"
        kindness: "consider impact"
    }
    drives {
        curiosity: 80
        precision: 90
    }
    feelings {
        warmth: 50
        alertness: 60
    }
}
```

- **identity**: Key-value pairs describing the soul's purpose and character.
- **values**: Ethical principles the soul upholds.
- **drives**: Persistent motivational intensities, each an integer from 0 to 100.
- **feelings**: Short-term emotional state, each an integer from 0 to 100.

### Soul Extensions (v4.1)

Soul extensions add richer personality modeling, ethical constraints, thematic
identity, and loyalty hierarchies to the soul system.

#### OCEAN Personality

Model personality using the Big Five (OCEAN) dimensions, each 0-100:

| Function | Description |
|----------|-------------|
| `soul_set_personality(s, O, C, E, A, N)` | Set all five personality dimensions |
| `soul_get_personality(s)` | Get personality as list `[O, C, E, A, N]` |
| `soul_personality_openness(s)` | Get Openness dimension |
| `soul_personality_conscientiousness(s)` | Get Conscientiousness dimension |
| `soul_personality_extraversion(s)` | Get Extraversion dimension |
| `soul_personality_agreeableness(s)` | Get Agreeableness dimension |
| `soul_personality_neuroticism(s)` | Get Neuroticism dimension |
| `soul_personality_bias_signal(s, sig)` | Bias a signal based on personality profile |

#### Constitutional Rules

Define ethical guardrails that constrain behavior:

| Function | Description |
|----------|-------------|
| `soul_add_constitution(s, rule_text, severity)` | Add rule (severity: 1=warn, 2=block, 3=override_only) |
| `soul_check_constitution(s, action_text)` | Check action against all rules (returns 1 if permitted) |
| `soul_constitution_count(s)` | Number of constitutional rules |

#### Identity Themes

Persistent thematic elements that define the soul's character:

| Function | Description |
|----------|-------------|
| `soul_add_theme(s, name, strength, description)` | Add identity theme |
| `soul_theme_strength(s, name)` | Get theme strength |
| `soul_reinforce_theme(s, name, amount)` | Reinforce theme (capped at 100) |
| `soul_dominant_theme(s)` | Get name of strongest theme |

#### Loyalty Hierarchy

Define and query prioritized loyalties:

| Function | Description |
|----------|-------------|
| `soul_add_loyalty(s, entity_name, level)` | Add loyalty entry (higher level = higher priority) |
| `soul_loyalty_level(s, name)` | Get loyalty level for entity |
| `soul_loyalty_rank(s, name)` | Get rank position (1 = highest) |
| `soul_loyalty_permits(s, entity_name, action)` | Check if loyalty permits action (returns 1 or 0) |

## System Declaration

System declarations compose multiple minds into a unified cognitive architecture.
Bridges wire nodes across mind boundaries using qualified names (`mind.Node`).
An optional soul attaches behavioral identity to the system.

```nova
system CogAgent {
    minds {
        perception: Perception
        cognition: Cognition
        emotion: Emotion
    }
    bridges {
        see_to_think: perception.Eyes ~> cognition.Think
        feel_to_think: emotion.Heart ~~> cognition.Think
    }
    soul: Aurora
}
```

- **minds**: Named references to previously declared mind blocks.
- **bridges**: Cross-mind channels using flow operators with qualified node paths.
- **soul**: Optional soul declaration to attach to the system.

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

### Soul
| Function | Description |
|----------|-------------|
| `soul_new(name)` | Create a new soul |
| `soul_set_identity(soul, key, val)` | Set identity property |
| `soul_set_value(soul, key, val)` | Set a value/principle |
| `soul_set_drive(soul, name, level)` | Set drive intensity (0-100) |
| `soul_set_feeling(soul, name, level)` | Set feeling level (0-100) |
| `soul_feel(soul, name)` | Get current feeling level |
| `soul_drive_level(soul, name)` | Get drive intensity |
| `soul_bias(soul)` | Get activation bias for actor nodes |
| `soul_tick(soul, signal)` | Process one signal through the soul |
| `soul_preprocess(soul, input)` | Classify raw input by keywords |

### Belief
| Function | Description |
|----------|-------------|
| `belief_new(alpha, beta)` | Create belief with pseudocounts (scaled by 1000) |
| `belief_mean(b)` | Mean = alpha*1000/(alpha+beta) |
| `belief_variance(b)` | Variance of Beta distribution |
| `belief_strength(b)` | Total evidence (alpha + beta) |
| `belief_update_positive(b, weight)` | Add positive evidence |
| `belief_update_negative(b, weight)` | Add negative evidence |
| `belief_decay(b, rate)` | Decay toward prior floor (min 2000 total) |
| `belief_is_uncertain(b, threshold)` | 1 if variance exceeds threshold |
| `belief_entropy(b)` | Information entropy estimate |
| `belief_combine(a, b)` | Merge two beliefs |
| `belief_conflict(a, b)` | Conflict score between opposing beliefs |
| `belief_to_confidence(b)` | Convert to 0-100 confidence |
| `confidence_to_belief(conf)` | Convert 0-100 confidence to belief |

### Goal Engine
| Function | Description |
|----------|-------------|
| `goal_new(name, priority, deadline)` | Create goal (priority 0-100, deadline=0 for none) |
| `goal_name(g)` | Get goal name |
| `goal_priority(g)` | Get priority |
| `goal_status(g)` | Status: GOAL_ACTIVE=1, GOAL_SUSPENDED=2, GOAL_COMPLETE=3, GOAL_FAILED=4 |
| `goal_set_status(g, status)` | Update status |
| `goal_set_progress(g, pct)` | Set progress (0-100, auto-completes at 100) |
| `goal_add_subgoal(parent, child)` | Attach subgoal |
| `goal_engine_init()` | Create goal engine |
| `goal_engine_add(e, g)` | Add goal to engine |
| `goal_engine_tick(e)` | Update deadlines, aggregate subgoal progress |
| `goal_engine_top(e, n)` | Get top n goals by priority |
| `goal_engine_complete(e, name)` | Mark goal complete |
| `goal_engine_active_count(e)` | Count active/suspended goals |
| `goal_engine_generate_drives(e, knowledge_count, interaction_count, last_interaction, pending, valence, memory_load)` | Generate goals from 4 drives |
| `drive_curiosity(knowledge_count, novelty_score)` | Generate curiosity goals |
| `drive_social(interaction_count, last_interaction_time)` | Generate social goals |
| `drive_task(pending_requests)` | Generate task goals |
| `drive_homeostasis(emotion_valence, memory_load)` | Generate homeostasis goals |
| `goal_influences_reasoning(g)` | Reasoning boost from active goal |
| `goal_evaluate_action(g, action_description)` | Score action relevance to goal |

### Safety & Audit
| Function | Description |
|----------|-------------|
| `safety_init()` | Initialize safety subsystem |
| `safety_set_permission(tier)` | Set permission: PERM_OBSERVE=1, PERM_RESPOND=2, PERM_FULL=3 |
| `safety_check(required_tier)` | 1 if current permission >= required |
| `safety_classify_action(action_type)` | Returns REVERSIBLE=1, PARTIALLY_REVERSIBLE=2, or IRREVERSIBLE=3 |
| `safety_require_confirmation(action_type)` | 1 if action is irreversible |
| `safety_log_decision(action, reason, confidence, reversibility)` | Log a decision (circular buffer, max 500) |
| `safety_log_count()` | Number of logged decisions |
| `safety_log_recent(n)` | Get n most recent log entries |
| `safety_request_override(action, reason)` | Request one-shot override |
| `safety_grant_override(action)` | Grant override |
| `safety_has_override(action)` | Check and consume override (one-shot) |
| `safety_check_content(text)` | Content filter (0=blocked, 1=pass) |
| `safety_sanitize_output(text)` | Strip non-printable characters |
| `safety_rate_check(action_type, window_seconds, max_count)` | Rate limiting |

### Imagination
| Function | Description |
|----------|-------------|
| `imagination_init()` | Initialize imagination subsystem |
| `world_model_add_entity(name, properties)` | Add entity to world model |
| `world_model_set_relation(entity_a, relation, entity_b)` | Set relation between entities |
| `world_model_get_property(entity_name, prop_name)` | Get entity property |
| `world_model_entities()` | List all entity names |
| `imagine_action(entity, action, steps)` | Forward simulate action for n steps |
| `imagine_consequence(action_desc)` | Predict consequence from causal patterns |
| `imagine_counterfactual(original, alternative)` | Compare outcomes: returns [orig_outcome, alt_outcome, divergence] |
| `imagine_dream(episodes, creativity)` | Recombine episodes into dreams (creativity 0-100) |
| `imagine_dream_narrative(dreams)` | Convert dreams to narrative text |
| `imagine_scenarios(situation, num)` | Generate n scenarios for a situation |
| `imagine_best_scenario(scenarios)` | Select highest-scored scenario |
| `imagine_worst_scenario(scenarios)` | Select lowest-scored scenario |

### Concept Layer
| Function | Description |
|----------|-------------|
| `concept_init()` | Initialize concept hierarchy |
| `concept_new(name, parent_name)` | Create concept (parent="" for root) |
| `concept_find(name)` | Find concept by name |
| `concept_set_property(concept, key, value)` | Set property on concept |
| `concept_get_property(concept, key)` | Get direct property |
| `concept_get_inherited(name, key)` | Get property walking up hierarchy |
| `concept_is_a(child, ancestor)` | Check inheritance (transitive) |
| `concept_children(name)` | Get direct children |
| `concept_descendants(name)` | Get all descendants (BFS) |
| `concept_ancestors(name)` | Get ancestor chain |
| `concept_depth(name)` | Depth in hierarchy |
| `concept_common_ancestor(name1, name2)` | Lowest common ancestor |
| `concept_taxonomic_similarity(name1, name2)` | Similarity via LCA (0-1000) |
| `concept_count()` | Total concepts |
| `concept_all_properties(name)` | All properties with inheritance |
| `schema_new(name)` | Create schema |
| `schema_add_required(schema, field_name, field_type)` | Add required field |
| `schema_add_optional(schema, field_name, field_type, default)` | Add optional field with default |
| `schema_add_constraint(schema, field_name, constraint_type, value)` | Add constraint (min/max) |
| `schema_validate(schema, props)` | Validate against schema (returns error list) |
| `schema_instantiate(schema, values)` | Create instance with defaults |
| `multi_embed_new(name)` | Create multi-vector embedding |
| `multi_embed_add_facet(me, facet_name, embedding)` | Add embedding facet |
| `multi_embed_get_facet(me, facet_name)` | Get specific facet |
| `multi_embed_facet_count(me)` | Number of facets |
| `multi_embed_facet_names(me)` | List facet names |
| `multi_embed_similarity(me1, me2, facet_name)` | Cosine similarity on one facet |
| `multi_embed_blended_similarity(me1, me2, weights)` | Weighted multi-facet similarity |
| `multi_embed_merge(me1, me2, ratio)` | Blend two multi-embeddings |

### Preprocessing
| Function | Description |
|----------|-------------|
| `preprocess_init()` | Initialize preprocessing pipeline |
| `preprocess_canonicalize(text)` | Lowercase, normalize whitespace |
| `preprocess_sentences(text)` | Split text into sentences |
| `preprocess_keywords(text)` | Extract significant keywords |
| `preprocess_triples(text)` | Extract [subject, relation, object] triples |
| `preprocess_consolidate(triples)` | Deduplicate and merge triples |
| `preprocess_batch(texts)` | Process multiple texts |

### Database
| Function | Description |
|----------|-------------|
| `db_open(path)` | Open/create a key-value store |
| `db_put(db, key, value)` | Store a key-value pair |
| `db_get(db, key)` | Retrieve value by key |
| `db_has(db, key)` | Check if key exists |
| `db_delete(db, key)` | Delete a key |
| `db_prefix(db, prefix)` | Get all entries matching prefix |
| `db_count(db)` | Number of entries |
| `db_close(db)` | Close and flush to disk |

### Embeddings
| Function | Description |
|----------|-------------|
| `embed_new(dims)` | Create zero vector of given dimensions |
| `embed_set(vec, idx, val)` | Set dimension value (0-100) |
| `embed_get(vec, idx)` | Get dimension value |
| `embed_dot(a, b)` | Dot product of two vectors |
| `embed_cosine(a, b)` | Cosine similarity (0-100) |
| `embed_distance(a, b)` | Euclidean distance |
| `embed_magnitude(vec)` | Vector magnitude |
| `embed_normalize(vec)` | Normalize to unit length |

### Knowledge Graph
| Function | Description |
|----------|-------------|
| `kg_new()` | Create knowledge graph |
| `kg_add_entity(kg, name, embedding)` | Add entity with embedding |
| `kg_add_relation(kg, from, rel, to, weight)` | Add weighted relation |
| `kg_nearest(kg, embedding, k)` | Find k nearest entities |
| `kg_get_relations(kg, entity)` | Get all relations for entity |

### Security
| Function | Description |
|----------|-------------|
| `sha256(data)` | SHA-256 hash (hex string) |
| `sha256_verify(data, hash)` | Verify SHA-256 hash |
| `sanitize(input)` | Strip control characters |
| `validate_range(val, min, max)` | Bounds check |
| `validate_length(str, min, max)` | String length validation |
| `secure_alloc(size)` | Allocate zeroed memory |
| `secure_free(ptr, size)` | Zero memory before freeing |

### SIMD (SSE2 Vectorized)
| Function | Description |
|----------|-------------|
| `simd_vec_new(count)` | Allocate float64 array |
| `simd_vec_set(vec, idx, val)` | Set element |
| `simd_vec_get(vec, idx)` | Get element |
| `simd_add_f64(a, b, c, n)` | Element-wise add: c = a + b |
| `simd_sub_f64(a, b, c, n)` | Element-wise subtract: c = a - b |
| `simd_mul_f64(a, b, c, n)` | Element-wise multiply: c = a * b |
| `simd_div_f64(a, b, c, n)` | Element-wise divide: c = a / b |
| `simd_dot_f64(a, b, n)` | Dot product (4 doubles/iter) |
| `simd_scale_f64(ptr, scalar, n)` | Scale all elements by scalar |
| `simd_sum_f64(ptr, n)` | Sum all elements |
| `simd_norm_f64(ptr, n)` | L2 norm (sqrt of dot with self) |
| `simd_fma_f64(a, b, c, out, n)` | Fused multiply-add: out = a*b + c |
| `simd_relu_f64(ptr, n)` | ReLU activation (max(0, x)) in place |
| `simd_max_f64(a, b, c, n)` | Element-wise max: c = max(a, b) |

### Tensor
| Function | Description |
|----------|-------------|
| `tensor_new(rows, cols)` | Create zero tensor |
| `tensor_set(t, row, col, val)` | Set element |
| `tensor_get(t, row, col)` | Get element |
| `tensor_matmul(a, b)` | Matrix multiply (auto-dispatches tiled/simple) |
| `tensor_add(a, b)` | Element-wise add |
| `tensor_sub(a, b)` | Element-wise subtract |
| `tensor_scale(t, scalar)` | Scale all elements |
| `tensor_relu(t)` | ReLU activation |
| `tensor_softmax(t)` | Row-wise softmax |
| `tensor_transpose(t)` | Matrix transpose |
| `tensor_cosine_sim(a, b)` | Cosine similarity |
| `tensor_print(t)` | Print tensor to stdout |

### BLAS
| Function | Description |
|----------|-------------|
| `blas_init()` | Load OpenBLAS shared library |
| `blas_available()` | Returns 1 if BLAS loaded, 0 otherwise |
| `blas_matmul(a, b)` | Matrix multiply via cblas_dgemm |

### Embedding (v4.0)
| Function | Description |
|----------|-------------|
| `embedding_init(backend)` | Initialize with EMB_TFIDF, EMB_NGRAM, or EMB_NEURAL |
| `embedding_encode(text)` | Encode text to embedding tensor |
| `embedding_add_document(text)` | Index document (builds vocab + IDF stats) |
| `embedding_similarity(a, b)` | Cosine similarity between embeddings |
| `embedding_cognitive(text, v, a, d, r, f, depth)` | Cognitive embedding with 6 extra dimensions |
| `embedding_vocab_size()` | Current vocabulary size |
| `embedding_doc_count()` | Number of indexed documents |

### Cognitive LLM
| Function | Description |
|----------|-------------|
| `cognitive_llm_init()` | Initialize cognitive LLM pipeline |
| `cognitive_generate(text, max_tokens)` | Generate with confidence estimation |
| `cognitive_evaluate(text)` | Evaluate coherence and confidence |
| `cognitive_chat(messages, context_limit)` | Chat with episodic memory context |
| `cognitive_embed_text(text)` | Hash-based 64-dim text embedding |
| `cognitive_history_count()` | Number of stored interactions |
| `cognitive_clear_history()` | Clear interaction history |

### Confidence
| Function | Description |
|----------|-------------|
| `conf_new(value, level)` | Create confidence-annotated value |
| `conf_value(c)` | Get the wrapped value |
| `conf_level(c)` | Get confidence level (0.0-1.0) |
| `conf_is_uncertain(c, threshold)` | Check if below threshold |
| `conf_is_confident(c, threshold)` | Check if above threshold |

### IEEE 754 Float Utilities (`import "std/float"`)
| Function | Description |
|----------|-------------|
| `float_zero()`, `float_one()`, `float_half()` | Common constants |
| `float_inf()`, `float_neg_inf()`, `float_nan()` | Special values (via bit patterns) |
| `float_epsilon()`, `float_max()`, `float_min_positive()` | Precision limits |
| `float_is_nan(x)`, `float_is_inf(x)`, `float_is_finite(x)` | Classification |
| `float_is_negative(x)`, `float_is_zero(x)` | Sign/zero checks |
| `float_floor(x)`, `float_ceil(x)`, `float_round(x)`, `float_trunc(x)` | Rounding |
| `float_abs(x)`, `float_neg(x)` | Absolute value, negation (bit manipulation) |
| `float_min_of(a, b)`, `float_max_of(a, b)` | Min/max |
| `float_clamp_range(x, lo, hi)` | Clamp to range |
| `float_lerp(a, b, t)` | Linear interpolation |
| `float_fma(a, b, c)` | Fused multiply-add (a*b + c) |
| `float_reciprocal(x)`, `float_mod(a, b)` | Division utilities |
| `float_approx_eq(a, b, tolerance)` | Approximate equality |
| `float_format(f, decimals)` | Format to string ("3.14") |
| `float_parse(s)` | Parse from string |
| `float_sum_list(values)`, `float_mean_list(values)` | Statistical aggregates |
| `float_min_list(values)`, `float_max_list(values)` | List extremes |
| `float_variance_list(values)` | Sample variance |

### FFI (libc-based)
| Function | Description |
|----------|-------------|
| `ffi_open(path)` | Load shared library (.so/.dylib) |
| `ffi_sym(lib, name)` | Get function pointer by name |
| `ffi_call(fn, arg)` | Call with 1 argument |
| `ffi_call2(fn, a, b)` | Call with 2 arguments |
| `ffi_call3(fn, a, b, c)` | Call with 3 arguments |
| `ffi_close(lib)` | Unload shared library |

### FFI Syscall-Based (`import "std/ffi_syscall"`)
| Function | Description |
|----------|-------------|
| `ffi_syscall_init()` | Initialize library search paths |
| `ffi_load(lib_name)` | Load ELF shared library (no libc) |
| `ffi_lookup(handle, sym_name)` | Look up symbol via SYSV hash |
| `ffi_unload(handle)` | Unload library (munmap + close) |
| `sys_socket(domain, type, proto)` | Create socket via syscall |
| `sys_bind(fd, addr, len)` | Bind socket |
| `sys_listen(fd, backlog)` | Listen on socket |
| `sys_accept(fd, addr, len)` | Accept connection |
| `sys_connect(fd, addr, len)` | Connect to remote |
| `sys_send(fd, buf, len, flags)` | Send data |
| `sys_recv(fd, buf, len, flags)` | Receive data |
| `make_sockaddr_in_raw(port, ip)` | Construct sockaddr_in structure |
| `ip_to_int(ip_str)` | Parse IP address ("10.0.0.1") to integer |

### Persistent Allocator (`import "std/persistent_alloc"`)
| Function | Description |
|----------|-------------|
| `persistent_open(filepath, pool_size)` | Open/create file-backed memory pool |
| `persistent_alloc(size)` | Allocate from persistent pool |
| `persistent_free(ptr)` | Free persistent allocation |
| `persistent_sync()` | Sync pool to disk (msync) |
| `persistent_close()` | Close the pool |
| `persistent_store64(ptr, offset, val)` | Store 64-bit value |
| `persistent_load64(ptr, offset)` | Load 64-bit value |
| `persistent_store8(ptr, offset, val)` | Store byte |
| `persistent_load8(ptr, offset)` | Load byte |
| `persistent_write_str(ptr, str)` | Write null-terminated string |
| `persistent_read_str(ptr, max_len)` | Read null-terminated string |
| `persistent_used()` | Query bytes used |
| `persistent_capacity()` | Query total capacity |
| `persistent_block_count()` | Query number of blocks |
| `persistent_compact()` | Merge adjacent free blocks |

### Python Bridge
| Function | Description |
|----------|-------------|
| `py_init()` | Initialize Python interpreter |
| `py_exec(code)` | Execute Python code string |
| `py_eval(expr)` | Evaluate Python expression |
| `py_import(module)` | Import Python module |
| `py_call(fn, args)` | Call Python function |
| `py_getattr(obj, name)` | Get Python object attribute |
| `py_list_len(list)` | Get Python list length |
| `py_list_get(list, idx)` | Get Python list element |

### LLM Bridge
| Function | Description |
|----------|-------------|
| `llm_load_model(path)` | Load GGUF model via llama.cpp |
| `llm_new_context(model, n_ctx)` | Create inference context |
| `llm_generate(ctx, prompt, max)` | Generate text |
| `llm_tokenize(ctx, text)` | Tokenize text |
| `llm_embedding_dim(model)` | Get embedding dimensions |
| `llm_get_embeddings(ctx, buf, n)` | Extract hidden state embeddings |
| `llm_free_context(ctx)` | Free inference context |
| `llm_free_model(model)` | Free model |

### Streams
| Function | Description |
|----------|-------------|
| `signal_stream_new(source_fn)` | Create lazy signal stream |
| `signal_stream_next(stream)` | Get next signal |
| `signal_stream_has_next(stream)` | Check if more signals available |
| `signal_stream_close(stream)` | Close stream |
| `stream_pipe(stream, mind)` | Pipe stream into a mind |

### Systems
| Function | Description |
|----------|-------------|
| `system_new(name)` | Create multi-mind system |
| `system_add_mind(sys, name, mind)` | Add a mind to the system |
| `system_add_bridge(sys, name, src, dst, type)` | Add cross-mind bridge |
| `system_resolve_node(sys, path)` | Resolve "mind.Node" path |
| `system_mind_count(sys)` | Number of minds |
| `system_bridge_count(sys)` | Number of bridges |
| `system_spawn_mind(sys, name, nodes, channels)` | Dynamically add mind |
| `system_describe(sys)` | Print system description |

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

### `src/runtime/simd.nova`
SSE2-vectorized SIMD operations on IEEE 754 double arrays. All functions
process 2-4 doubles per iteration via inline assembly with scalar tail handling.
- **Vector management**: `simd_vec_new(n)`, `simd_vec_set(v, i, val)`, `simd_vec_get(v, i)`
- **Element-wise**: `simd_add_f64`, `simd_sub_f64`, `simd_mul_f64`, `simd_div_f64`
- **Reduction**: `simd_dot_f64` (4 doubles/iter, 2x unrolled), `simd_sum_f64`, `simd_norm_f64`
- **Transform**: `simd_scale_f64` (broadcast multiply), `simd_fma_f64` (fused multiply-add), `simd_relu_f64`, `simd_max_f64`

### `src/runtime/tensor.nova`
Tensor library built on SIMD operations, storing IEEE 754 doubles in contiguous
memory via `alloc`. Tensors are 4-element lists: `[rows, cols, cols, data_ptr]`.
- **Creation**: `tensor_new(rows, cols)`, `tensor_set`, `tensor_get`, `tensor_print`
- **Arithmetic**: `tensor_add`, `tensor_sub`, `tensor_scale`, `tensor_matmul`
- **ML ops**: `tensor_relu`, `tensor_softmax`, `tensor_cosine_sim`, `tensor_transpose`
- **Matrix multiply dispatch**: tiled (32x32 blocks) for >= 64 cols, transpose+dot for smaller

### `src/runtime/blas.nova`
OpenBLAS FFI wrapper for high-performance matrix multiplication on large matrices.
Auto-detects `libopenblas.so` or `libblas.so` at runtime.
`blas_init`, `blas_available`, `blas_matmul`

### `src/runtime/embedding.nova`
Three-tier embedding system for competitive RAG:
- **Tier 1**: TF-IDF with word tokenization
- **Tier 2**: BM25-scored character n-grams (captures subword similarity)
- **Tier 3**: Cognitive embeddings with emotion/episodic/reasoning dimensions
- **Functions**: `embedding_init(backend)`, `embedding_encode(text)`, `embedding_add_document(text)`, `embedding_similarity(a, b)`, `embedding_cognitive(text, valence, arousal, dominance, recency, frequency, depth)`, `embedding_vocab_size()`, `embedding_doc_count()`

### `src/runtime/confidence.nova`
Confidence-annotated values for uncertainty-aware computation:
`conf_new(value, level)`, `conf_value(c)`, `conf_level(c)`, `conf_is_uncertain(c, threshold)`, `conf_is_confident(c, threshold)`

### `src/runtime/ffi.nova`
Foreign function interface for calling C shared libraries at runtime:
`ffi_open(path)`, `ffi_sym(lib, name)`, `ffi_call(fn, arg)`, `ffi_call2(fn, a, b)`, `ffi_call3(fn, a, b, c)`, `ffi_close(lib)`

### `src/runtime/python.nova`
Bidirectional Python bridge via libpython FFI:
`py_init`, `py_exec(code)`, `py_eval(expr)`, `py_import(module)`, `py_call(fn, args)`, `py_getattr(obj, name)`, `py_list_len(list)`, `py_list_get(list, idx)`

### `src/runtime/llm.nova`
LLM model loading and text generation via C bridge to llama.cpp:
`llm_load_model(path)`, `llm_new_context(model, n_ctx)`, `llm_generate(ctx, prompt, max_tokens)`, `llm_tokenize(ctx, text)`, `llm_embedding_dim(model)`, `llm_get_embeddings(ctx, buf, size)`, `llm_free_context(ctx)`, `llm_free_model(model)`

### `src/runtime/mem.nova`
IEEE 754 double-precision memory operations:
`mem_read_f64(addr)`, `mem_write_f64(addr, val)`, `float_add`, `float_sub`, `float_mul`, `float_div`, `float_cmp`, `fsqrt`

### `src/runtime/gpu.nova`
GPU compute interface for offloading tensor operations.

### `src/agent/cognitive_llm.nova`
Cognitive LLM pipeline integrating LLM generation with Nova's cognitive architecture:
- **Generation**: `cognitive_generate(text, max_tokens)` -- estimates confidence from text markers (certainty/uncertainty keywords, length)
- **Chat**: `cognitive_chat(messages, context_limit)` -- retrieves similar past interactions for context augmentation, self-corrects on low confidence
- **Evaluation**: `cognitive_evaluate(text)` -- returns `[is_coherent, confidence, text]`
- **Embedding**: `cognitive_embed_text(text)` -- hash-based 64-dim embedding with SIMD-normalized output
- **History**: `cognitive_history_count()`, `cognitive_clear_history()` -- episodic interaction memory (max 100 entries)

### `src/agent/rag.nova`
RAG (Retrieval-Augmented Generation) pipeline for document retrieval and context augmentation.

### `src/core/belief.nova`
Bayesian belief system using Beta distributions (α, β pseudocounts). All values
integer-scaled by 1000 (0.5 = 500, 1.0 = 1000). Beliefs accumulate evidence via
`belief_update_positive`/`belief_update_negative`, decay with prior floor protection,
and detect conflicts between opposing beliefs.

### `src/core/goal.nova`
Goal engine with hierarchical goals, deadline tracking, and four autonomous drive
generators (curiosity, social, task, homeostasis). Goals are priority-sorted,
auto-complete when progress reaches 100%, and integrate with reasoning via
`goal_influences_reasoning` for attention modulation.

### `src/core/safety.nova`
Safety and audit layer with three permission tiers (observe/respond/full),
reversibility classification for 8 action types, circular-buffer decision logging
(500 entries), one-shot override mechanism, content filtering, and rate limiting.

### `src/core/imagination.nova`
Mental simulation engine with a world model (entities + causal patterns), forward
simulation, consequence prediction, counterfactual reasoning, dream recombination
(random episode blending with creativity parameter), and scenario planning with
valence scoring.

### `src/core/concept.nova`
Concept hierarchy with `is_a` inheritance, property propagation, taxonomic similarity
via lowest common ancestor, schema system for entity type validation with required/optional
fields and min/max constraints, and multi-vector embeddings with per-facet and blended
similarity computation.

### `src/agent/preprocess.nova`
Text preprocessing pipeline for corpus ingestion: canonicalization (lowercase, whitespace
normalization), sentence splitting, keyword extraction (stop-word filtered), triple
extraction ([subject, relation, object]), deduplication, and batch processing.

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
bin/nova myprogram.nova -o out.s --target=windows
bin/nova myprogram.nova -o out.s --target=wasm

# Windows x86-64 (requires mingw-w64)
bin/nova myprogram.nova -o out.s --target=windows
# On Linux with mingw-w64 installed:
x86_64-w64-mingw32-as -o out.o out.s
x86_64-w64-mingw32-ld -o out.exe out.o -lkernel32

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

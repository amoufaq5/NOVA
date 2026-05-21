# Nova Use Cases

Nova is a self-hosting compiled language targeting x86-64 with no libc dependency,
direct Linux syscalls (and Windows via Win32 API), and built-in cognitive computing
primitives. This document covers the major use cases with short, runnable code
examples for each.

---

## 1. Systems Programming

Nova compiles to native x86-64 machine code with zero runtime dependencies.
Direct syscall access, raw memory primitives, and TCP networking make it
suitable for OS-level utilities and servers.

### File Manipulation and Process Management

```nova
fn main() {
    // Write a config file
    write_file("/tmp/nova_app.conf", "workers=4\nport=8080\n")

    // Read it back and parse
    let contents = read_file("/tmp/nova_app.conf")
    let lines = split(contents, "\n")
    for line in lines {
        if len(line) > 0 {
            let parts = split(line, "=")
            println("  " + parts[0] + " => " + parts[1])
        }
    }

    // File metadata and cleanup
    let size = file_size("/tmp/nova_app.conf")
    println("Config size: " + int_to_str(size) + " bytes")
    unlink("/tmp/nova_app.conf")

    // Fork a child process
    let pid = fork_process()
    if pid == 0 {
        println("Child process running")
        exit(0)
    } else {
        waitpid(pid)
        println("Parent: child finished")
    }
}

main()
```

### Arena Allocator for Batch Processing

```nova
import "src/runtime/alloc.nova"

fn process_batch(items) {
    arena_init()
    for item in items {
        // Allocate scratch space per item (freed all at once)
        let buf = arena_alloc(256)
        store64(buf, item * item)
        let result = load64(buf)
        println("Processed: " + int_to_str(result))
    }
    println("Arena used: " + int_to_str(arena_used()) + " bytes")
    arena_reset()  // Free everything in one shot
}

process_batch([10, 20, 30, 40, 50])
```

### TCP Echo Server

```nova
fn main() {
    let port = 8080
    let server_fd = socket(2, 1, 0)       // AF_INET, SOCK_STREAM
    let addr = make_sockaddr_in(port, 0)   // INADDR_ANY
    bind_socket(server_fd, addr, 16)
    listen_socket(server_fd, 5)
    println("Listening on port " + int_to_str(port))

    while true {
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

### IPC via Pipes and Fork/Exec

```nova
fn main() {
    let fds = pipe_create()   // [read_fd, write_fd]
    let pid = fork_process()

    if pid == 0 {
        // Child: close read end, write message
        close_fd(fds[0])
        let msg = "hello from child"
        send_data(fds[1], msg, len(msg))
        close_fd(fds[1])
        exit(0)
    } else {
        // Parent: close write end, read message
        close_fd(fds[1])
        let buf = alloc(256)
        let n = recv_data(fds[0], buf, 256)
        close_fd(fds[0])
        waitpid(pid)
        println("Parent received " + int_to_str(n) + " bytes via pipe")
    }
}

main()
```

---

## 2. Compiler and Language Tools

Nova is self-hosting: its compiler (lexer, parser, IR, codegen) is written in
Nova itself. This makes it a proven platform for building language tools.

### Simple Tokenizer

```nova
let TOK_NUM = 1
let TOK_OP = 2
let TOK_EOF = 0

fn tokenize(input) {
    let tokens = list_new()
    let i = 0
    while i < len(input) {
        let c = char_at(input, i)
        if c >= 48 && c <= 57 {
            // Digit: accumulate number
            let num = 0
            while i < len(input) && char_at(input, i) >= 48 && char_at(input, i) <= 57 {
                num = num * 10 + (char_at(input, i) - 48)
                i = i + 1
            }
            push(tokens, [TOK_NUM, num])
        } else if c == 43 || c == 45 || c == 42 || c == 47 {
            push(tokens, [TOK_OP, c])
            i = i + 1
        } else {
            i = i + 1  // skip whitespace
        }
    }
    push(tokens, [TOK_EOF, 0])
    return tokens
}

let tokens = tokenize("12 + 34 * 5")
for tok in tokens {
    match tok[0] {
        1 => println("NUM: " + int_to_str(tok[1]))
        2 => println("OP:  " + chr(tok[1]))
        0 => println("EOF")
    }
}
```

### Recursive Descent Expression Parser

```nova
let tokens = []
let pos = 0

fn peek() { return tokens[pos] }
fn advance() { pos = pos + 1 }

fn parse_primary() {
    let tok = peek()
    if tok[0] == 1 {   // TOK_NUM
        advance()
        return tok[1]
    }
    throw "expected number"
}

fn parse_expr() {
    let left = parse_primary()
    while pos < len(tokens) - 1 {
        let tok = peek()
        if tok[0] != 2 { break }
        let op = tok[1]
        if op != 43 && op != 45 { break }
        advance()
        let right = parse_primary()
        if op == 43 { left = left + right }
        else { left = left - right }
    }
    return left
}

// Reuse the tokenizer from above, then:
tokens = [[1,10], [2,43], [2,45], [1,3], [0,0]]
// Represents: 10 + - 3  -- but let's do a simpler one:
tokens = [[1,10], [2,43], [1,25], [2,45], [1,3], [0,0]]
pos = 0
let result = parse_expr()
println("10 + 25 - 3 = " + int_to_str(result))
```

---

## 3. Data Structures and Algorithms

Nova's lists, maps, closures, and raw memory primitives support implementing
classic data structures and algorithms.

### Linked List

```nova
fn node_new(value) {
    let n = list_new()
    push(n, value)
    push(n, 0)   // next pointer
    return n
}

fn list_append(head, value) {
    let n = node_new(value)
    if head == 0 { return n }
    let curr = head
    while curr[1] != 0 { curr = curr[1] }
    list_set(curr, 1, n)
    return head
}

fn list_print(head) {
    let curr = head
    while curr != 0 {
        print(int_to_str(curr[0]))
        if curr[1] != 0 { print(" -> ") }
        curr = curr[1]
    }
    println("")
}

let head = 0
for val in [10, 20, 30, 40] {
    head = list_append(head, val)
}
list_print(head)  // 10 -> 20 -> 30 -> 40
```

### Quicksort

```nova
fn quicksort(arr, lo, hi) {
    if lo >= hi { return 0 }
    let pivot = arr[hi]
    let i = lo
    let j = lo
    while j < hi {
        if arr[j] < pivot {
            let tmp = arr[i]
            list_set(arr, i, arr[j])
            list_set(arr, j, tmp)
            i = i + 1
        }
        j = j + 1
    }
    let tmp = arr[i]
    list_set(arr, i, arr[hi])
    list_set(arr, hi, tmp)
    quicksort(arr, lo, i - 1)
    quicksort(arr, i + 1, hi)
}

let data = [38, 27, 43, 3, 9, 82, 10]
quicksort(data, 0, len(data) - 1)
print("Sorted: ")
for i, v in data {
    if i > 0 { print(", ") }
    print(int_to_str(v))
}
println("")
```

### Binary Search

```nova
fn binary_search(arr, target) {
    let low = 0
    let high = len(arr) - 1
    while low <= high {
        let mid = (low + high) / 2
        if arr[mid] == target { return mid }
        if arr[mid] < target { low = mid + 1 }
        else { high = mid - 1 }
    }
    return -1
}

let sorted = [2, 5, 8, 12, 16, 23, 38, 56, 72, 91]
let idx = binary_search(sorted, 23)
println("Found 23 at index " + int_to_str(idx))
```

### Hash Map (Built-in)

```nova
let db = map_new()
map_set(db, "alice", 95)
map_set(db, "bob", 87)
map_set(db, "charlie", 92)

for name in keys(db) {
    let score = map_get(db, name)
    let grade = match true {
        _ if score >= 90 => "A"
        _ if score >= 80 => "B"
        _ => "C"
    }
    println(name + ": " + int_to_str(score) + " (" + grade + ")")
}
```

---

## 4. Cognitive and AI Systems

Nova's defining feature is Moment-Signal Computing: a declarative cognitive
architecture with six node types, flow operators, and a signal scheduler.

### Perception Pipeline

```nova
fn make_signal(content, salience) {
    return [content, salience, time()]
}

fn perceive(sig) {
    let content = sig[0]
    let category = if contains(content, "hello") { "greeting" }
        else if contains(content, "?") { "question" }
        else { "statement" }
    println("[Perceiver] " + content + " => " + category)
    return [category, sig[1]]
}

fn reason(classified) {
    let response = match classified[0] {
        "greeting" => "reciprocate warmly"
        "question" => "search knowledge"
        _ => "acknowledge"
    }
    println("[Reasoner] Action: " + response)
    return response
}

// Pipeline: signal ~> perceiver ~> reasoner
let sig = make_signal("hello there!", 80)
let classified = perceive(sig)
reason(classified)
```

### Memory System with Decay

```nova
let episodes = list_new()
let current_time = 0

fn memory_store(what, importance) {
    push(episodes, [what, importance, current_time])
}

fn memory_recall(query, now) {
    let best = ""
    let best_score = 0
    for ep in episodes {
        let age = now - ep[2]
        let decay = max(0, ep[1] - age * 2)   // Linear decay
        if contains(ep[0], query) && decay > best_score {
            best = ep[0]
            best_score = decay
        }
    }
    return best
}

// Store memories at different times
current_time = 0
memory_store("learned about dogs", 90)
current_time = 5
memory_store("saw a cat", 40)
current_time = 10
memory_store("read about dogs in science", 80)

// Recall at t=12: recent + important wins
let result = memory_recall("dogs", 12)
println("Best match: " + result)
```

### Emotion Model (Valence-Arousal-Dominance)

```nova
struct Emotion { valence, arousal, dominance }

fn emotion_new() { return Emotion(500, 500, 500) }  // Neutral (scaled 0-1000)

fn emotion_update(e, v_shift, a_shift, d_shift) {
    let decay = 80  // 80% retention per step
    e.valence = (e.valence * decay + v_shift * (100 - decay)) / 100
    e.arousal = (e.arousal * decay + a_shift * (100 - decay)) / 100
    e.dominance = (e.dominance * decay + d_shift * (100 - decay)) / 100
}

fn emotion_label(e) {
    if e.valence > 600 && e.arousal > 600 { return "excited" }
    if e.valence > 600 && e.arousal < 400 { return "content" }
    if e.valence < 400 && e.arousal > 600 { return "anxious" }
    if e.valence < 400 && e.arousal < 400 { return "sad" }
    return "neutral"
}

let mood = emotion_new()
emotion_update(mood, 800, 700, 600)   // Positive stimulus
println("After good news: " + emotion_label(mood))
emotion_update(mood, 200, 800, 300)   // Threat stimulus
println("After threat:    " + emotion_label(mood))
```

### Full Cognitive Agent (Mind Declaration)

```nova
import "src/core/moment.nova"
import "src/core/signal.nova"
import "src/core/node.nova"
import "src/core/channel.nova"
import "src/runtime/scheduler.nova"

mind Agent {
    nodes {
        Sense: perceiver
        Know: knower
        Mem: rememberer
        Think: reasoner
        Feel: feeler
        Act: actor
    }
    channels {
        intake: Sense =>> [Know, Mem, Think, Feel]
        decide: Think ~> Act
        color: Feel ~~> Think
        recall: Mem <<~ Think
    }
}

fn main() {
    // Configure nodes
    perceiver_add_template(Agent_Sense, "greeting", ["hello", "hi"], 80)
    reasoner_add_rule(Agent_Think, "hello", "respond with greeting")
    feeler_set_emotion(Agent_Feel, 70, 40, 50)

    // Create and process a moment
    let user = entity_new("User", "human")
    let cons = consequence_new(CTYPE_EXPECTATION, "reply", 80)
    let m = moment_new("hello!", user, 75, 50, cons, 85)
    let sig = signal_event(m, "external", "Sense")

    scheduler_emit(sig)
    scheduler_run()
    println("Processed: " + int_to_str(scheduler_total_processed()) + " signals")
}

main()
```

### Soul-Driven Agent
```nova
import "src/core/soul.nova"
import "src/core/node.nova"
import "src/runtime/scheduler.nova"

soul Aria {
    identity {
        purpose: "assist and learn"
        nature: "curious and kind"
    }
    values {
        truth: "verify before asserting"
        growth: "embrace complexity"
    }
    drives {
        curiosity: 85
        empathy: 70
    }
    feelings {
        warmth: 60
        focus: 75
    }
}

// The soul biases cognitive processing and
// initiates signals when drives exceed thresholds
```

### Multi-Mind System
```nova
import "src/core/system.nova"

mind Perception {
    nodes { Eyes: perceiver, Ears: perceiver }
    channels { }
}

mind Reasoning {
    nodes { Think: reasoner, Store: rememberer }
    channels { recall: Think ~> Store }
}

mind Emotion {
    nodes { Heart: feeler }
    channels { }
}

system Agent {
    minds {
        perception: Perception
        reasoning: Reasoning
        emotion: Emotion
    }
    bridges {
        see: perception.Eyes ~> reasoning.Think
        hear: perception.Ears ~> reasoning.Think
        feel: emotion.Heart ~~> reasoning.Think
    }
    soul: Aria
}
```

---

## 5. Signal Processing

Flow operators (`~>`, `=>>`, `~~>`, `|~>`, `<~`, `<=>`) provide concise syntax
for event-driven signal routing, pub/sub, and pipeline processing.

### Event-Driven Architecture

```nova
let handlers = map_new()
let event_queue = list_new()

fn on(event_name, handler) {
    map_set(handlers, event_name, handler)
}

fn emit(event_name, data) {
    push(event_queue, [event_name, data])
}

fn process_events() {
    while len(event_queue) > 0 {
        let event = event_queue[0]
        list_remove(event_queue, 0)
        if map_has(handlers, event[0]) {
            let handler = map_get(handlers, event[0])
            handler(event[1])
        }
    }
}

on("login", fn(data) { println("User logged in: " + data) })
on("purchase", fn(data) { println("Purchase made: " + data) })
on("logout", fn(data) { println("User logged out: " + data) })

emit("login", "alice")
emit("purchase", "$29.99")
emit("logout", "alice")
process_events()
```

### Pipeline Processing with Fan-Out

```nova
fn make_pipeline(stages) {
    return stages
}

fn run_pipeline(pipeline, input) {
    let data = input
    for stage in pipeline {
        data = stage(data)
    }
    return data
}

// Define stages
let normalize = fn(s) { return str_lower(str_trim(s)) }
let classify = fn(s) {
    if contains(s, "error") { return "ALERT: " + s }
    return "INFO: " + s
}
let tag = fn(s) { return "[" + int_to_str(time()) + "] " + s }

let pipeline = make_pipeline([normalize, classify, tag])

let messages = ["  ERROR disk full  ", "System OK", "  Error timeout "]
for msg in messages {
    println(run_pipeline(pipeline, msg))
}
```

---

## 6. Game Development

Nova can build terminal-based games using raw I/O, timers, and grid-based
rendering. The Game of Life ships as a bundled example.

### Conway's Game of Life (Simplified)

```nova
let W = 10
let H = 6

fn neighbors(grid, x, y) {
    let count = 0
    for dy in [-1, 0, 1] {
        for dx in [-1, 0, 1] {
            if dx == 0 && dy == 0 { continue }
            let nx = x + dx
            let ny = y + dy
            if nx >= 0 && nx < W && ny >= 0 && ny < H {
                count = count + grid[ny * W + nx]
            }
        }
    }
    return count
}

fn step(grid) {
    let next = list_new()
    for y in range(H) {
        for x in range(W) {
            let n = neighbors(grid, x, y)
            let alive = grid[y * W + x]
            let cell = if alive == 1 { n == 2 || n == 3 ? 1 : 0 }
                else { n == 3 ? 1 : 0 }
            push(next, cell)
        }
    }
    return next
}

fn display(grid) {
    for y in range(H) {
        for x in range(W) {
            print(grid[y * W + x] == 1 ? "#" : ".")
        }
        println("")
    }
}

// Initialize with a blinker
let grid = [0 for _ in range(W * H)]
list_set(grid, 1 * W + 4, 1)
list_set(grid, 1 * W + 5, 1)
list_set(grid, 1 * W + 6, 1)

for gen in range(4) {
    println("Gen " + int_to_str(gen) + ":")
    display(grid)
    grid = step(grid)
    println("")
}
```

### Entity-Component Style System

```nova
// Components are maps, entities are IDs
let positions = map_new()
let velocities = map_new()
let healths = map_new()
let next_id = 0

fn spawn(x, y, vx, vy, hp) {
    let id = next_id
    next_id = next_id + 1
    map_set(positions, int_to_str(id), [x, y])
    map_set(velocities, int_to_str(id), [vx, vy])
    map_set(healths, int_to_str(id), hp)
    return id
}

fn movement_system() {
    for id_str in keys(positions) {
        let pos = map_get(positions, id_str)
        let vel = map_get(velocities, id_str)
        map_set(positions, id_str, [pos[0] + vel[0], pos[1] + vel[1]])
    }
}

fn print_entities() {
    for id_str in keys(positions) {
        let pos = map_get(positions, id_str)
        let hp = map_get(healths, id_str)
        println("Entity " + id_str + ": (" + int_to_str(pos[0]) +
                ", " + int_to_str(pos[1]) + ") HP=" + int_to_str(hp))
    }
}

spawn(0, 0, 1, 2, 100)
spawn(10, 5, -1, 0, 80)

println("=== Tick 0 ===")
print_entities()
movement_system()
println("=== Tick 1 ===")
print_entities()
```

---

## 7. Text Processing

Nova has comprehensive string built-ins plus the ability to build interpreters
and parsers. A complete Brainfuck interpreter ships as a bundled example.

### Brainfuck Interpreter

```nova
fn bf_run(program) {
    let tape = [0 for _ in range(30000)]
    let ptr = 0
    let pc = 0
    while pc < len(program) {
        let cmd = char_at(program, pc)
        if cmd == 62 { ptr = ptr + 1 }         // >
        if cmd == 60 { ptr = ptr - 1 }         // <
        if cmd == 43 { list_set(tape, ptr, tape[ptr] + 1) }  // +
        if cmd == 45 { list_set(tape, ptr, tape[ptr] - 1) }  // -
        if cmd == 46 { print(chr(tape[ptr])) }  // .
        if cmd == 91 && tape[ptr] == 0 {        // [
            let depth = 1
            while depth > 0 {
                pc = pc + 1
                if char_at(program, pc) == 91 { depth = depth + 1 }
                if char_at(program, pc) == 93 { depth = depth - 1 }
            }
        }
        if cmd == 93 && tape[ptr] != 0 {        // ]
            let depth = 1
            while depth > 0 {
                pc = pc - 1
                if char_at(program, pc) == 93 { depth = depth + 1 }
                if char_at(program, pc) == 91 { depth = depth - 1 }
            }
        }
        pc = pc + 1
    }
}

print("BF Hello: ")
bf_run("++++++++[>++++[>++>+++>+++>+<<<<-]>+>+>->>+[<]<-]>>.>---.+++++++..+++.>>.<-.<.+++.------.--------.>>+.>++.")
println("")
```

### String Manipulation Utilities

```nova
fn word_count(text) {
    let words = split(str_trim(text), " ")
    return len(filter(words, fn(w) { return len(w) > 0 }))
}

fn title_case(text) {
    let words = split(text, " ")
    let result = map_list(words, fn(w) {
        if len(w) == 0 { return w }
        return str_upper(substr(w, 0, 1)) + str_lower(substr(w, 1, len(w) - 1))
    })
    return join(result, " ")
}

fn csv_parse(line) {
    return split(line, ",")
}

let text = "  hello world from nova  "
println("Words: " + int_to_str(word_count(text)))
println("Title: " + title_case(str_trim(text)))

let csv = "alice,95,A"
let fields = csv_parse(csv)
println("Name: " + fields[0] + ", Score: " + fields[1])
```

### Expression Evaluator

```nova
fn eval_simple(expr) {
    let parts = split(expr, " ")
    let a = str_to_int(parts[0])
    let op = parts[1]
    let b = str_to_int(parts[2])
    match op {
        "+" => return a + b
        "-" => return a - b
        "*" => return a * b
        "/" => return a / b
        "**" => return a ** b
        _ => { throw "unknown operator: " + op }
    }
}

let expressions = ["10 + 25", "100 - 37", "7 * 8", "2 ** 10"]
for expr in expressions {
    try {
        println(expr + " = " + int_to_str(eval_simple(expr)))
    } catch {
        println("Error: " + get_error())
    }
}
```

---

## 8. Embedded and Bare-Metal

Nova produces minimal binaries with no libc, making it suitable for
constrained environments. Inline assembly provides direct hardware access.

### Direct Syscall Wrappers

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

fn sys_exit(code) {
    asm {
        "mov rax, 60"
        "mov rdi, [rbp-8]"
        "syscall"
    }
}

// Write directly to stdout using only syscalls
let msg = "Hello from raw syscall!\n"
sys_write(1, msg, len(msg))
sys_exit(0)
```

### Custom Memory Layout with store64/load64

```nova
// Build a simple struct in raw memory:
// offset 0: id (i64)
// offset 8: x coordinate (i64)
// offset 16: y coordinate (i64)
// offset 24: flags (i64)
const RECORD_SIZE = 32

fn record_new(id, x, y, flags) {
    let ptr = alloc(RECORD_SIZE)
    store64(ptr, id)
    store64(ptr + 8, x)
    store64(ptr + 16, y)
    store64(ptr + 24, flags)
    return ptr
}

fn record_print(ptr) {
    println("Record #" + int_to_str(load64(ptr)) +
            " at (" + int_to_str(load64(ptr + 8)) +
            ", " + int_to_str(load64(ptr + 16)) +
            ") flags=0x" + hex(load64(ptr + 24)))
}

let r1 = record_new(1, 100, 200, 0xFF)
let r2 = record_new(2, 300, 400, 0x0A)
record_print(r1)
record_print(r2)
```

### Reading the CPU Timestamp Counter

```nova
extern fn rdtsc()

asm {
    ".globl rdtsc"
    "rdtsc:"
    "    rdtsc"
    "    shl rdx, 32"
    "    or rax, rdx"
    "    ret"
}

let t1 = rdtsc()
// Tight loop
let sum = 0
for i in range(10000) { sum = sum + i }
let t2 = rdtsc()

println("10000 iterations: ~" + int_to_str(t2 - t1) + " CPU cycles")
println("Sum: " + int_to_str(sum))
```

---

## 9. Web / WebAssembly

Nova supports cross-compilation to WebAssembly, enabling browser-side
execution and serverless deployment of Nova programs.

### Compiling to WebAssembly

```bash
# Compile Nova source to WASM
bin/nova my_app.nova -o my_app.s --target=wasm

# The resulting WASM module can be loaded in the browser
# or deployed as a serverless function
```

```nova
// A Nova program that compiles to WASM
fn fibonacci(n) {
    if n <= 1 { return n }
    return fibonacci(n - 1) + fibonacci(n - 2)
}

fn factorial(n) {
    let result = 1
    for i in range(1, n + 1) {
        result = result * i
    }
    return result
}

// Exported functions callable from JavaScript
println("fib(10) = " + int_to_str(fibonacci(10)))
println("fact(12) = " + int_to_str(factorial(12)))
```

### Windows Cross-Compilation

```bash
# Cross-compile Nova to a Windows executable
bin/nova my_app.nova --target=windows -o my_app.s
x86_64-w64-mingw32-as -o my_app.o my_app.s
x86_64-w64-mingw32-ld -o my_app.exe my_app.o -lkernel32

# Or use the Makefile
make cross-windows
```

Nova produces native PE32+ executables for Windows x86-64, using Win32 API calls (kernel32.dll) instead of Linux syscalls.

### Serverless Function Pattern

```nova
// A request handler suitable for serverless compilation
fn handle_request(method, path, body) {
    match path {
        "/health" => return "OK"
        "/compute" => {
            let n = str_to_int(body)
            let result = fibonacci(n)
            return int_to_str(result)
        }
        _ => return "Not Found"
    }
}

fn fibonacci(n) {
    if n <= 1 { return n }
    return fibonacci(n - 1) + fibonacci(n - 2)
}

// Test locally
println(handle_request("GET", "/health", ""))
println(handle_request("POST", "/compute", "10"))
```

---

## 10. Educational

Nova is uniquely suited for teaching how compilers, operating systems, and
cognitive architectures work, because every layer is visible and written in
a single language.

### Learning How Compilers Work

Nova is self-hosting, meaning the compiler is written in Nova itself. Students
can trace the complete compilation pipeline:

```
Source code (.nova)
    |
    v
Lexer (src/compiler/lexer.nova)        -- characters to tokens
    |
    v
Parser (src/compiler/parser.nova)      -- tokens to AST
    |
    v
IR (src/compiler/ir.nova)             -- AST to intermediate representation
    |
    v
Codegen (src/compiler/codegen.nova)    -- IR to x86-64 assembly
    |
    v
Assembler + Linker (as, ld)           -- assembly to ELF binary
```

Verify the self-hosting property (fixed-point test):

```bash
# Stage 2: compile compiler with itself
cat src/compiler/*.nova src/pkg/pkg.nova > /tmp/combined.nova
bin/nova /tmp/combined.nova -o /tmp/stage2.s
as -o /tmp/stage2.o /tmp/stage2.s && ld -o /tmp/stage2 /tmp/stage2.o

# Stage 3: compile again with stage 2
/tmp/stage2 /tmp/combined.nova -o /tmp/stage3.s

# If the compiler is correct, stage2.s == stage3.s
diff /tmp/stage2.s /tmp/stage3.s && echo "Fixed point reached!"
```

### Understanding OS-Level Programming

```nova
// Demonstrates: no libc, no runtime -- just you and the kernel
fn sys_write(fd, buf, count) {
    asm {
        "mov rax, 1"
        "mov rdi, [rbp-8]"
        "mov rsi, [rbp-16]"
        "mov rdx, [rbp-24]"
        "syscall"
    }
}

fn sys_getpid() {
    asm {
        "mov rax, 39"
        "syscall"
    }
}

// Every Nova program starts at _start, sets up a stack,
// and talks directly to the Linux kernel via syscall.
let pid = sys_getpid()
let msg = "My PID is " + int_to_str(pid) + "\n"
sys_write(1, msg, len(msg))
```

### Studying Cognitive Architectures

```nova
// The six node types map to cognitive science concepts:
//
// perceiver   -- sensory input classification (template matching)
// knower      -- semantic memory (spreading activation)
// rememberer  -- episodic memory (similarity scoring, decay)
// reasoner    -- inference (deduction, abduction, analogy)
// feeler      -- emotion (valence/arousal/dominance drift)
// actor       -- action selection (competing activations)

// A minimal cognitive loop:
fn cognitive_cycle(input) {
    // 1. Perceive: classify the input
    let category = if contains(input, "?") { "question" } else { "statement" }

    // 2. Remember: check if we've seen this before
    let familiar = contains(input, "hello")

    // 3. Reason: decide what to do
    let action = match category {
        "question" => "search for answer"
        _ if familiar => "greet back"
        _ => "acknowledge"
    }

    // 4. Feel: emotional coloring
    let mood = familiar ? "warm" : "curious"

    // 5. Act: produce output
    println("[" + mood + "] " + input + " => " + action)
}

cognitive_cycle("hello there!")
cognitive_cycle("what is Nova?")
cognitive_cycle("the sky is blue")
```

---

## 11. Knowledge & Persistence

Nova includes built-in persistence, embeddings, and knowledge graphs for
building agents that remember across sessions.

### Key-Value Database

```nova
import "src/runtime/db.nova"

let db = db_open("knowledge.novdb")
db_put(db, "capital:France", "Paris")
db_put(db, "capital:Japan", "Tokyo")
db_put(db, "capital:Germany", "Berlin")

let city = db_get(db, "capital:France")
println("Capital of France: " + city)

let all_capitals = db_prefix(db, "capital:")
println("Known capitals: " + int_to_str(len(all_capitals)))
db_close(db)
```

### Semantic Embeddings

```nova
import "src/runtime/embed.nova"

let cat = embed_new(8)
embed_set(cat, 0, 90)   // animal
embed_set(cat, 1, 60)   // domestic
embed_set(cat, 2, 30)   // size

let dog = embed_new(8)
embed_set(dog, 0, 90)   // animal
embed_set(dog, 1, 80)   // domestic
embed_set(dog, 2, 50)   // size

let sim = embed_cosine(cat, dog)
println("Cat-Dog similarity: " + int_to_str(sim))
```

### Knowledge Graph

```nova
import "src/core/knowledge.nova"

let kg = kg_new()
kg_add_entity(kg, "cat", cat)
kg_add_entity(kg, "dog", dog)
kg_add_relation(kg, "cat", "is_a", "animal", 95)
kg_add_relation(kg, "dog", "is_a", "animal", 95)
kg_add_relation(kg, "cat", "similar_to", "dog", 75)

let near = kg_nearest(kg, cat, 3)
println("Nearest to cat: " + int_to_str(len(near)) + " entities")
```

---

## 12. Security

Nova provides built-in security primitives for hashing, validation, and
secure memory management.

### SHA-256 Hashing

```nova
import "src/runtime/crypto.nova"

let hash = sha256("password123")
println("SHA-256: " + hash)

let ok = sha256_verify("password123", hash)
println("Verified: " + int_to_str(ok))
```

### Input Validation

```nova
import "src/runtime/validate.nova"

let clean = sanitize(user_input)
let valid_age = validate_range(age, 0, 150)
let valid_name = validate_length(name, 1, 100)
```

### Secure Memory

```nova
import "src/runtime/secure_mem.nova"

let secret = secure_alloc(256)
// ... use secret ...
secure_free(secret, 256)   // zeroes memory before freeing
```

---

## Summary

| Use Case | Key Nova Features Used |
|---|---|
| Systems Programming | `fork_process`, `pipe_create`, `socket`, `alloc`, `asm{}` |
| Compiler Tools | Self-hosting compiler, `char_at`, `str_to_int`, recursive descent |
| Data Structures | Lists, maps, closures, `list_set`, `sort`, `filter` |
| Cognitive / AI | `mind` declaration, flow operators, 6 node types, scheduler |
| Signal Processing | `~>`, `=>>`, `~~>`, `\|~>`, event queues, handlers |
| Game Development | Grid rendering, `list_set`, timer loops, ECS pattern |
| Text Processing | `split`, `join`, `str_replace`, `map_list`, `filter`, `match` |
| Embedded / Bare-Metal | `asm{}`, `store64`/`load64`, `alloc`, direct syscalls |
| Web / WebAssembly | `--target=wasm`, pure computation functions |
| Educational | Self-hosting, visible pipeline, cognitive science mapping |
| Knowledge & Persistence | `db_open`, `db_put`, `embed_new`, `embed_cosine`, `kg_new`, `kg_nearest` |
| Security | `sha256`, `sanitize`, `validate_range`, `secure_alloc`, `secure_free` |
| Multi-Mind Systems | `soul` declaration, `system` declaration, bridges, `system_resolve_node` |
| Windows Deployment | `--target=windows`, `make cross-windows`, PE32+ executables |

# Nova Memory Model: Arena + Ownership Hybrid

**Version:** 0.1.0-draft
**Status:** Technical Specification
**Last Updated:** 2026-05-15

---

## Table of Contents

1. [Overview](#1-overview)
2. [Design Goals](#2-design-goals)
3. [Core Principles](#3-core-principles)
4. [Arena System](#4-arena-system)
   - 4.1 [Arena Declaration](#41-arena-declaration)
   - 4.2 [Budget Checking](#42-budget-checking)
   - 4.3 [Arena Types](#43-arena-types)
   - 4.4 [Nested Arenas](#44-nested-arenas)
   - 4.5 [Arena Internals](#45-arena-internals)
5. [Ownership Rules](#5-ownership-rules)
   - 5.1 [Single Owner Principle](#51-single-owner-principle)
   - 5.2 [Scope-Based Freeing](#52-scope-based-freeing)
   - 5.3 [The `owned` Annotation](#53-the-owned-annotation)
   - 5.4 [Scalar Copy Semantics](#54-scalar-copy-semantics)
6. [Borrowing](#6-borrowing)
   - 6.1 [Immutable Borrows](#61-immutable-borrows)
   - 6.2 [Mutable Borrows](#62-mutable-borrows)
   - 6.3 [Borrow Lifetime Rules](#63-borrow-lifetime-rules)
   - 6.4 [Runtime Enforcement](#64-runtime-enforcement)
7. [Move Semantics](#7-move-semantics)
   - 7.1 [Explicit Moves](#71-explicit-moves)
   - 7.2 [Move in Function Parameters](#72-move-in-function-parameters)
   - 7.3 [Move Detection](#73-move-detection)
8. [Shared Ownership](#8-shared-ownership)
   - 8.1 [Atomic Reference Counting](#81-atomic-reference-counting)
   - 8.2 [Thread Safety](#82-thread-safety)
   - 8.3 [Weak References](#83-weak-references)
9. [Runtime Metadata](#9-runtime-metadata)
   - 9.1 [Value Metadata](#91-value-metadata)
   - 9.2 [Arena Metadata](#92-arena-metadata)
   - 9.3 [Release Mode Stripping](#93-release-mode-stripping)
10. [Performance Characteristics](#10-performance-characteristics)
11. [Comparison with Other Languages](#11-comparison-with-other-languages)
12. [Complete Examples](#12-complete-examples)
13. [Error Catalog](#13-error-catalog)
14. [Future Considerations](#14-future-considerations)

---

## 1. Overview

Nova employs an **Arena + Ownership Hybrid** memory model designed for deterministic, high-performance memory management without a garbage collector. The model combines two complementary strategies:

- **Arenas** manage bulk allocations (GPU tensors, activation buffers, large data structures) with compile-time budget checking and O(1) bulk deallocation.
- **Ownership tracking** manages general-purpose heap values with single-owner semantics and deterministic scope-based freeing.

Together, these provide zero-GC-pause, zero-fragmentation memory management suitable for latency-sensitive ML workloads and systems programming.

---

## 2. Design Goals

| Goal | Mechanism |
|------|-----------|
| No GC pauses | No garbage collector exists in the runtime. |
| Deterministic freeing | Values freed at scope exit; arenas freed at block exit. |
| GPU-aware allocation | Arena types distinguish `gpu`, `cpu`, and `pinned` memory. |
| Compile-time safety | Static budget checking catches over-allocation before runtime. |
| Zero overhead in release mode | All runtime metadata and checks can be compiled out. |
| Ergonomic defaults | Immutable borrows by default; scalars copy automatically. |
| Compatibility with dynamic types | Borrow checking at runtime rather than requiring full static type resolution. |

---

## 3. Core Principles

1. **No garbage collector.** Nova never performs stop-the-world collection, concurrent marking, or any form of tracing GC. Memory is reclaimed through deterministic scope exits and arena resets.

2. **Deterministic memory freeing.** Every allocation has a statically-knowable point at which it will be freed: the end of its owning scope (for owned values) or the end of the arena block (for arena allocations). There is no finalization queue and no deferred cleanup.

3. **Arenas for bulk GPU/tensor allocations.** Large, uniform allocations such as model parameters, activation tensors, and gradient buffers are managed through arenas with declared memory budgets. The compiler performs static budget analysis; the runtime handles dynamic cases.

4. **Ownership tracking for general heap values.** Non-arena heap allocations follow single-owner rules with explicit move semantics, borrowed references, and optional shared ownership via reference counting.

5. **Zero runtime overhead in release mode.** All ownership metadata (moved flags, borrow counters, scope IDs) can be stripped in release builds. Arena budget checks for purely static allocations are resolved at compile time and removed from the binary.

---

## 4. Arena System

### 4.1 Arena Declaration

An arena is declared with a block scope, a device type, and a memory budget:

```nova
arena gpu(budget=24gb) {
    let weights = tensor[f16, 4096, 4096].zeros()
    let activations = tensor[f16, batch, 4096].empty()
    -- compiler statically checks: do these allocations fit in 24GB?
    -- if batch is dynamic, runtime check at arena entry
}  -- entire arena freed in one operation, no fragmentation
```

**Syntax:**

```
arena <device>( budget = <size_literal> ) { <body> }
```

Where `<size_literal>` accepts the suffixes `kb`, `mb`, `gb`, and `tb` (case-insensitive, base-2 units: 1gb = 1,073,741,824 bytes).

**Semantics:**

- On entry, the runtime reserves a contiguous region of the specified size on the specified device.
- All allocations within the block use a bump allocator against this region.
- On scope exit, the entire region is released in a single operation (pointer reset or device free).
- References to arena-allocated values must not escape the arena scope. The compiler rejects any code that would allow an arena reference to outlive its arena.

### 4.2 Budget Checking

Budget checking operates in two phases:

**Phase 1: Compile-Time (Static Allocations)**

When all dimensions of an allocation are compile-time constants, the compiler sums their sizes and verifies the total fits within the declared budget:

```nova
arena gpu(budget=1gb) {
    let a = tensor[f32, 1024, 1024].zeros()   -- 4 MB
    let b = tensor[f32, 1024, 1024].zeros()   -- 4 MB
    let c = tensor[f32, 1024, 1024].zeros()   -- 4 MB
    -- total static: 12 MB <= 1 GB  ✓  compiles
}
```

```nova
arena gpu(budget=8mb) {
    let a = tensor[f32, 1024, 1024].zeros()   -- 4 MB
    let b = tensor[f32, 1024, 1024].zeros()   -- 4 MB
    let c = tensor[f32, 1024, 1024].zeros()   -- 4 MB
    -- total static: 12 MB > 8 MB  ✗  COMPILE ERROR: arena budget exceeded
}
```

**Phase 2: Runtime (Dynamic Allocations)**

When any dimension is a runtime value, the compiler inserts a budget check at the allocation site:

```nova
arena gpu(budget=24gb) {
    let batch = get_batch_size()  -- runtime value
    let activations = tensor[f16, batch, 4096].empty()
    -- compiler inserts: assert(batch * 4096 * 2 <= arena.remaining())
    -- if this fails at runtime: ArenaOverflow error
}
```

The compiler still checks that the static allocations alone fit, and subtracts them from the remaining budget before evaluating dynamic checks.

### 4.3 Arena Types

Nova defines three arena device types:

| Arena Type | Device | Use Case |
|------------|--------|----------|
| `gpu(budget)` | GPU VRAM (specific device) | Model weights, activations, gradients |
| `cpu(budget)` | System RAM | Large CPU-side data structures, datasets |
| `pinned(budget)` | Pinned (page-locked) CPU memory | DMA transfers between CPU and GPU |

**GPU device selection:**

```nova
arena gpu(budget=24gb) {
    -- uses default GPU (device 0)
}

arena gpu(budget=24gb, device=1) {
    -- explicitly targets GPU 1
}
```

**Pinned memory for efficient transfers:**

```nova
arena pinned(budget=4gb) {
    let staging = tensor[f16, batch, seq_len, dim].empty()
    staging.copy_from(cpu_data)
    gpu_tensor.copy_from(staging)  -- DMA transfer, no page faults
}
```

**CPU arenas for large data structures:**

```nova
arena cpu(budget=16gb) {
    let dataset = load_dataset("pile", split="train")
    let index = build_token_index(dataset)
    -- both allocated from the arena, freed together
}
```

### 4.4 Nested Arenas

Arenas can be nested. Child arenas carve their budget from the parent arena's remaining capacity:

```nova
arena gpu(budget=80gb) {
    let model = load_model("llama-70b")  -- ~140GB in f16... 
    -- ERROR if model exceeds remaining budget

    arena gpu(budget=20gb) {
        -- nested arena for per-step activations
        -- this 20GB is carved from the parent's 80GB
        for batch in dataloader {
            let logits = model.forward(batch)
            let loss = cross_entropy(logits, batch.labels)
            loss.backward()
        }  -- activations freed here, every iteration
    }

    -- parent arena still alive, model still valid
}  -- parent arena freed, model freed
```

**Nesting rules:**

1. A child arena's budget must not exceed the parent's remaining capacity at the point of declaration.
2. The child arena's memory is returned to the parent on scope exit.
3. Nesting depth is unlimited but the compiler warns at depth > 4 (configurable).
4. Cross-device nesting is allowed (e.g., a `cpu` arena inside a `gpu` arena); these are independent allocations, not carved from the parent.

### 4.5 Arena Internals

Each arena is implemented as a bump allocator:

```
+------------------------------------------------------------+
| base_ptr                             current_ptr   end_ptr |
| v                                    v             v       |
| [allocated data.....................][ free space ]         |
+------------------------------------------------------------+
```

- **Allocation** advances `current_ptr` by the requested size. O(1).
- **Deallocation of individual values** is not supported. Arena memory is freed in bulk only.
- **Scope exit** resets `current_ptr` to `base_ptr` (or to the parent's watermark for nested arenas). O(1).
- **Alignment** is handled by rounding `current_ptr` up to the required alignment before each allocation. GPU arenas default to 256-byte alignment for coalesced memory access.

---

## 5. Ownership Rules

Ownership applies to all heap-allocated values that are **not** inside an arena. Arena values follow arena lifetime rules instead.

### 5.1 Single Owner Principle

Every heap-allocated value has exactly one owner: the binding that created it.

```nova
let data = [1, 2, 3, 4, 5]  -- 'data' owns this list
let config = ModelConfig(dim=512, heads=8)  -- 'config' owns this struct
```

There is never more than one owner for a given value (unless explicitly wrapped in `shared[T]`; see Section 8).

### 5.2 Scope-Based Freeing

When an owner goes out of scope, the value is freed immediately:

```nova
fn process() {
    let buffer = ByteBuffer(capacity=1024)
    -- use buffer...
}  -- buffer freed here, deterministically

fn conditional() {
    let x = allocate_large_struct()
    if some_condition {
        let y = allocate_another()
        -- y freed here
    }
    -- x freed here
}
```

Freeing is deterministic and ordered: values are freed in reverse order of declaration (LIFO), matching stack unwinding semantics.

### 5.3 The `owned` Annotation

`owned` is an explicit annotation that is semantically identical to `let` but communicates intent and enables additional lint warnings:

```nova
owned model = GPT(dim=512)  -- explicitly declares ownership intent
let model2 = GPT(dim=512)   -- identical behavior, less explicit
```

When `owned` is used, the compiler produces warnings if:
- The value is never moved or consumed (suggesting it could be a local temporary).
- The value is immediately borrowed and never used directly (suggesting `let` suffices).
- The value is shadowed before use.

```nova
owned data = load_data()
-- WARNING: 'data' declared as owned but never moved or consumed
-- consider using 'let' instead
let result = transform(data)
```

### 5.4 Scalar Copy Semantics

Scalar types are always copied, never owned:

```nova
let x: int = 42
let y = x       -- y is a copy of x, not a move
print(x)        -- perfectly valid, x was not moved
print(y)        -- also 42

let flag: bool = true
let flag2 = flag  -- copy

let pi: f64 = 3.14159
let tau = pi * 2.0  -- pi copied into the multiplication
```

**Scalar types (always copied):**
- `int`, `i8`, `i16`, `i32`, `i64`
- `float`, `f16`, `f32`, `f64`, `bf16`
- `bool`
- `byte`

**Non-scalar types (owned, subject to ownership rules):**
- `str` (heap-allocated strings)
- `list[T]`, `dict[K, V]`, `set[T]`
- `tensor[dtype, ...dims]`
- All user-defined structs and classes
- Function closures that capture owned values

---

## 6. Borrowing

Borrowing allows functions to access values without taking ownership. Nova's borrowing model prioritizes safety while accommodating dynamic typing through runtime checks.

### 6.1 Immutable Borrows

Function parameters are immutable borrows by default:

```nova
fn print_stats(data: tensor) {
    -- 'data' is an immutable borrow; cannot modify it
    print(f"shape: {data.shape}, mean: {data.mean()}")
}

let weights = tensor[f32, 768, 768].randn()
print_stats(weights)    -- immutable borrow
print_stats(weights)    -- can borrow again, weights still valid
```

Multiple immutable borrows can coexist:

```nova
fn compare(a: tensor, b: tensor) {
    -- both a and b are immutable borrows; this is fine
    print(f"cosine similarity: {cosine_sim(a, b)}")
}

let v1 = tensor[f32, 128].randn()
let v2 = tensor[f32, 128].randn()
compare(v1, v2)  -- two simultaneous immutable borrows of different values

-- borrowing the same value twice immutably is also allowed:
fn self_compare(x: tensor, y: tensor) {
    print(f"self-similarity: {cosine_sim(x, y)}")
}
self_compare(v1, v1)  -- two immutable borrows of v1, both valid
```

### 6.2 Mutable Borrows

The `mut` parameter modifier creates a mutable borrow:

```nova
fn normalize(mut data: tensor) {
    let mean = data.mean()
    let std = data.std()
    data = (data - mean) / std  -- modifies the original tensor in-place
}

let weights = tensor[f32, 768].randn()
normalize(mut weights)  -- caller must also write 'mut' to acknowledge mutation
print(weights)          -- weights is now normalized
```

**Exclusivity rule:** Only one mutable borrow may exist at a time, and no immutable borrows may coexist with it:

```nova
fn bad_example(mut a: tensor, b: tensor) {
    a += b
}

let data = tensor[f32, 128].randn()
bad_example(mut data, data)
-- RUNTIME ERROR: cannot borrow 'data' as immutable because it is also
-- borrowed as mutable
```

### 6.3 Borrow Lifetime Rules

A borrow must not outlive its owner. Since Nova uses runtime checking (see Section 6.4), this is enforced dynamically:

```nova
fn dangling_reference() -> tensor {
    let local = tensor[f32, 64].zeros()
    return local  -- this is a MOVE, not a borrow; ownership transfers out
}

fn return_borrow() {
    let data = tensor[f32, 64].zeros()
    let ref = borrow(data)
    return ref
    -- RUNTIME ERROR: borrow of 'data' escapes its owner's scope
}
```

**Borrow rules summary:**

| Rule | Description |
|------|-------------|
| Multiple immutable | Allowed: any number of `&T` borrows can coexist |
| Single mutable | Allowed: exactly one `mut` borrow, no concurrent immutable borrows |
| Outlive owner | Forbidden: a borrow must not escape the scope of its owner |
| Borrow moved value | Forbidden: cannot borrow a value that has been moved |
| Borrow during mutation | Forbidden: cannot create new borrows while a mutable borrow is active |

### 6.4 Runtime Enforcement

Nova enforces borrow rules at runtime rather than purely at compile time. This is a deliberate design choice: Nova's support for dynamic typing and runtime polymorphism prevents the compiler from performing complete static borrow analysis in all cases.

In **debug mode**, every borrow operation checks:
1. The value has not been moved (`moved == false`).
2. If requesting an immutable borrow: `mut_borrowed == false`.
3. If requesting a mutable borrow: `borrow_count == 0` and `mut_borrowed == false`.

In **release mode**, these checks are compiled out entirely for zero overhead. The programmer accepts responsibility for correctness, similar to how array bounds checks can be disabled in other systems languages.

```nova
-- Debug mode: all checks active
@[debug]
fn example() {
    let data = tensor[f32, 128].zeros()
    normalize(mut data)     -- check: borrow_count == 0, not moved
    print_stats(data)       -- check: not mut_borrowed, not moved
}

-- Release mode: checks stripped, identical behavior if code is correct
@[release]
fn example_release() {
    let data = tensor[f32, 128].zeros()
    normalize(mut data)     -- no check
    print_stats(data)       -- no check
}
```

---

## 7. Move Semantics

### 7.1 Explicit Moves

Ownership is transferred via the `move` keyword. After a move, the source binding is invalidated:

```nova
let data = tensor[f32, 1024, 1024].randn()
let data2 = move data    -- ownership transferred to data2
print(data)              -- RUNTIME ERROR: use of moved value 'data'
print(data2)             -- valid
```

A move does not copy data. It transfers the pointer (and metadata) from one binding to another. The original binding is marked as moved.

### 7.2 Move in Function Parameters

Functions can accept ownership of a value using the `move` modifier:

```nova
fn consume(move data: tensor) {
    let result = data.sum()
    print(f"sum: {result}")
}  -- data freed here, since this function now owns it

let batch = load_batch()
consume(move batch)
print(batch)   -- RUNTIME ERROR: use of moved value 'batch'
```

The `move` keyword must appear at both the call site and the function signature, making ownership transfer explicit and visible during code review:

```nova
fn train_step(move optimizer: Optimizer, mut model: Model, batch: Batch) -> Optimizer {
    let loss = model.forward(batch)
    loss.backward()
    optimizer.step(mut model)
    return move optimizer  -- return ownership to caller
}

let opt = Adam(lr=3e-4)
-- must explicitly move optimizer in, get it back out:
opt = train_step(move opt, mut model, batch)
```

### 7.3 Move Detection

The compiler performs basic static move detection where possible and inserts runtime checks elsewhere:

**Static detection (compile error):**

```nova
let data = load_data()
let data2 = move data
let data3 = move data   -- COMPILE ERROR: 'data' already moved on line above
```

**Dynamic detection (runtime error):**

```nova
let data = load_data()
if random() > 0.5 {
    consume(move data)
}
print(data)  -- may or may not have been moved; RUNTIME CHECK inserted
             -- ERROR if the branch was taken
```

---

## 8. Shared Ownership

### 8.1 Atomic Reference Counting

When multiple parts of a program need to hold references to the same value, `shared[T]` provides atomic reference counting:

```nova
let config = shared(ModelConfig(dim=512, heads=8, layers=6))
let c2 = config.clone()  -- reference count incremented (now 2)
let c3 = config.clone()  -- reference count incremented (now 3)

print(config.dim)   -- 512
print(c2.dim)       -- 512, same underlying data

-- c3 goes out of scope: count decremented to 2
-- c2 goes out of scope: count decremented to 1
-- config goes out of scope: count decremented to 0, value freed
```

**`shared[T]` rules:**

1. `shared[T]` is itself a value type (the wrapper is copyable; `.clone()` is explicit).
2. The underlying `T` is immutable through a `shared[T]`. For mutable shared data, use `shared[Mutex[T]]`.
3. The reference count is atomic (`fetch_add` / `fetch_sub`), making `shared[T]` safe to send across threads.
4. Dropping a `shared[T]` decrements the count. The value is freed when the count reaches zero.
5. Cycles are not automatically detected. Programmers must use `weak[T]` to break cycles (see Section 8.3).

### 8.2 Thread Safety

`shared[T]` is the primary mechanism for sharing data across threads:

```nova
let weights = shared(tensor[f32, 768, 768].randn())

spawn {
    let local_ref = weights.clone()
    -- use local_ref in this thread
    let output = matmul(local_ref, input)
}

spawn {
    let local_ref = weights.clone()
    -- same weights, different thread, safe because immutable
    let output = matmul(local_ref, other_input)
}
```

For mutable shared state:

```nova
let counter = shared(Mutex(0))

spawn {
    let c = counter.clone()
    c.lock(fn(mut val) {
        val += 1
    })
}
```

### 8.3 Weak References

`weak[T]` holds a non-owning reference to a `shared[T]` value. It does not prevent deallocation:

```nova
let data = shared(LargeStruct(...))
let weak_ref = data.downgrade()  -- weak[LargeStruct]

-- later:
match weak_ref.upgrade() {
    some(strong) => print("still alive: {strong}"),
    none => print("value has been freed"),
}
```

Weak references are essential for:
- Breaking reference cycles in graph structures.
- Caches that should not prevent deallocation.
- Observer patterns where the observer should not keep the subject alive.

```nova
struct Node {
    value: int,
    children: list[shared[Node]],
    parent: weak[Node],          -- weak to avoid cycle
}

let parent = shared(Node(value=1, children=[], parent=weak.empty()))
let child = shared(Node(value=2, children=[], parent=parent.downgrade()))
parent.clone().children.push(child.clone())
-- no cycle: child -> parent is weak, parent -> child is strong
```

---

## 9. Runtime Metadata

### 9.1 Value Metadata

Each owned (non-arena) heap value carries a metadata header in debug mode:

| Field | Type | Description |
|-------|------|-------------|
| `moved` | `bool` | Whether the value has been moved away from this binding |
| `borrow_count` | `u32` | Number of active immutable borrows |
| `mut_borrowed` | `bool` | Whether a mutable borrow is active |
| `owner_scope_id` | `u64` | Unique identifier for the owning scope |
| `arena_id` | `u64` | Arena this value belongs to (0 if not in an arena) |

**Memory layout (debug mode):**

```
+-------------------+
| moved        (1B) |
| borrow_count (4B) |
| mut_borrowed (1B) |
| padding      (2B) |
| owner_scope  (8B) |
| arena_id     (8B) |
+-------------------+  <- 24 bytes metadata header
| actual value data |
+-------------------+
```

### 9.2 Arena Metadata

Each arena maintains the following state:

| Field | Type | Description |
|-------|------|-------------|
| `base_ptr` | `*void` | Start of the arena's memory region |
| `current_ptr` | `*void` | Next free position (bump pointer) |
| `end_ptr` | `*void` | End of the arena's memory region |
| `budget_bytes` | `u64` | Declared budget in bytes |
| `used_bytes` | `u64` | Currently allocated bytes |
| `device` | `enum { cpu, gpu(u32), pinned }` | Device and optional device ID |

**Arena allocation pseudocode:**

```
fn arena_alloc(arena: *Arena, size: u64, align: u64) -> *void {
    let aligned = align_up(arena.current_ptr, align)
    let new_ptr = aligned + size
    if new_ptr > arena.end_ptr {
        panic("ArenaOverflow: allocation of {size} bytes exceeds remaining "
              "budget ({arena.end_ptr - arena.current_ptr} bytes free)")
    }
    arena.current_ptr = new_ptr
    arena.used_bytes += size
    return aligned
}
```

### 9.3 Release Mode Stripping

In release mode (`nova build --release`), the following are removed:

| Component | Debug | Release |
|-----------|-------|---------|
| Value metadata header (24 bytes) | Present | Stripped |
| Borrow count checks | Active | Removed |
| Move flag checks | Active | Removed |
| Scope ID tracking | Active | Removed |
| Arena budget checks (static) | Compile-time only | Compile-time only |
| Arena budget checks (dynamic) | Active | **Retained** (overflow protection) |

Note that dynamic arena budget checks are retained even in release mode because an arena overflow would corrupt memory. This is the sole runtime check that survives release builds. It can be explicitly disabled with `@[unsafe_no_arena_check]`.

---

## 10. Performance Characteristics

| Operation | Time Complexity | Notes |
|-----------|-----------------|-------|
| Arena allocation | O(1) | Bump pointer advance + alignment |
| Arena free (scope exit) | O(1) | Pointer reset; no per-object destructors |
| Ownership check | O(1) | Single flag read (debug mode only) |
| Borrow check | O(1) | Counter comparison (debug mode only) |
| Move | O(1) | Flag set + pointer copy; no data copied |
| `shared` clone | O(1) | Atomic `fetch_add` on reference count |
| `shared` drop | O(1) | Atomic `fetch_sub`; free if count reaches zero |
| `weak` upgrade | O(1) | Atomic compare-and-swap on reference count |
| Scope exit (N owned values) | O(N) | Each value freed in reverse order |
| Nested arena entry | O(1) | Save watermark, optionally allocate from parent |
| Nested arena exit | O(1) | Restore watermark |

**Memory overhead:**

| Allocation Type | Overhead per Value |
|-----------------|-------------------|
| Arena-allocated value | 0 bytes (no per-value metadata) |
| Owned value (debug) | 24 bytes metadata header |
| Owned value (release) | 0 bytes metadata header |
| `shared[T]` | 16 bytes (strong count u64 + weak count u64) |
| `weak[T]` | 8 bytes (pointer to control block) |

---

## 11. Comparison with Other Languages

| Feature | Nova | Rust | Python | C++ | JAX |
|---------|------|------|--------|-----|-----|
| **GC** | None | None | Reference counting + cycle collector | None | Python GC (host); XLA manages buffers |
| **Deterministic free** | Yes (scope exit) | Yes (scope exit via `Drop`) | No (GC-dependent) | Yes (RAII) | No (XLA runtime decides) |
| **Borrow checking** | Runtime (debug) | Compile-time (lifetime analysis) | None | None (raw pointers) | N/A |
| **Move semantics** | Explicit `move` keyword | Implicit (affine types) | N/A (everything ref-counted) | `std::move` (can still access source) | `jax.device_put` (copies) |
| **Arena allocation** | First-class `arena` blocks with budget | Custom allocators (verbose) | Not available | `std::pmr::monotonic_buffer_resource` | XLA internal buffer pools |
| **GPU memory management** | `arena gpu(budget)` with static checking | Manual (via `unsafe` or libraries) | Manual (PyTorch `cuda.memory`) | Manual (`cudaMalloc`) | XLA automatic, opaque |
| **Shared ownership** | `shared[T]` (atomic ref count) | `Arc<T>` (atomic ref count) | All objects (ref count) | `std::shared_ptr<T>` | N/A |
| **Weak references** | `weak[T]` | `Weak<T>` | `weakref.ref` | `std::weak_ptr<T>` | N/A |
| **Compile-time memory analysis** | Arena budget checking | Lifetime + borrow checker | None | None | XLA HLO buffer assignment |
| **Fragmentation** | None (arenas); normal (heap) | Normal (heap) | Normal (heap) | Normal (heap) | None (XLA buffer pools) |
| **Release mode overhead** | Zero (checks stripped) | Zero (already compile-time) | Always present | Zero (no checks to strip) | Python overhead always present |
| **Thread safety model** | `shared[T]` for sharing; borrows are local | `Send`/`Sync` traits | GIL (CPython) | Manual (`mutex`, atomics) | Functional (no mutation) |
| **Learning curve** | Moderate (runtime errors guide you) | Steep (lifetime annotations) | Low (no memory management) | High (manual, error-prone) | Low (functional, automatic) |

**Key differentiators from Rust:**

Nova trades compile-time borrow analysis for runtime checking. This means:
- No lifetime annotations (`'a`, `'b`) cluttering function signatures.
- No "fighting the borrow checker" during prototyping.
- Errors appear at runtime in debug mode rather than at compile time.
- Release mode trusts the programmer (like C++), but debug mode catches violations (unlike C++).

**Key differentiators from Python/JAX:**

Nova provides:
- Explicit control over GPU memory layout and lifetime.
- No GC pauses during training loops.
- Compile-time budget verification for GPU allocations.
- Deterministic memory freeing (critical for large model training where OOM is common).

**Key differentiators from C++:**

Nova improves on C++ by:
- Making moves explicit (no accidental use-after-move).
- Providing arena blocks as first-class syntax (not a library pattern).
- Runtime borrow checking in debug mode (C++ has no equivalent safety net).
- GPU-aware arenas with budget verification.

---

## 12. Complete Examples

### 12.1 Training Loop with Arena Management

```nova
fn train(
    model_path: str,
    dataset_path: str,
    epochs: int = 10,
    batch_size: int = 32,
    lr: float = 3e-4,
) {
    arena gpu(budget=80gb) {
        -- model parameters live for the entire training run
        let model = GPT.from_pretrained(model_path)
        let optimizer = AdamW(model.parameters(), lr=lr)
        let dataloader = DataLoader(dataset_path, batch_size=batch_size)

        for epoch in range(epochs) {
            arena gpu(budget=20gb) {
                -- activations and gradients are per-step, freed each iteration
                for batch in dataloader {
                    let logits = model.forward(batch.input_ids)
                    let loss = cross_entropy(logits, batch.labels)

                    print(f"epoch {epoch}, loss: {loss.item()}")

                    loss.backward()
                    optimizer.step()
                    optimizer.zero_grad()
                }  -- per-batch activations freed here
            }
        }
    }  -- model, optimizer freed; GPU memory fully released
}
```

### 12.2 Ownership and Move Through a Pipeline

```nova
fn tokenize(move text: str) -> list[int] {
    let tokens = bpe_encode(text)
    -- text is freed here (we own it and don't return it)
    return move tokens
}

fn embed(move tokens: list[int], embeddings: tensor) -> tensor {
    -- tokens consumed, embeddings borrowed
    let result = embeddings.index_select(0, tokens)
    return move result
}

fn pipeline(input: str) {
    let text = input.clone()          -- clone the borrowed input to own it
    let tokens = tokenize(move text)  -- text moved into tokenize
    -- text no longer valid here

    let embed_table = load_embeddings("model/embeddings.bin")
    let features = embed(move tokens, embed_table)
    -- tokens no longer valid here
    -- embed_table still valid (was borrowed, not moved)

    print(f"features shape: {features.shape}")
}  -- features and embed_table freed here
```

### 12.3 Shared Config Across Threads

```nova
fn serve(model_path: str, num_workers: int = 4) {
    let config = shared(ServerConfig(
        model_path=model_path,
        max_batch=32,
        timeout_ms=5000,
    ))

    let handles = list[ThreadHandle].empty()

    for i in range(num_workers) {
        let worker_config = config.clone()  -- arc increment
        let handle = spawn {
            let local_config = worker_config  -- each thread has its own shared ref
            print(f"worker {i} using model: {local_config.model_path}")
            serve_requests(local_config)
        }
        handles.push(handle)
    }

    for h in handles {
        h.join()
    }
}  -- config refcount drops to 0, freed
```

### 12.4 Mutable Borrow for In-Place Operations

```nova
fn layer_norm(mut x: tensor, gamma: tensor, beta: tensor, eps: float = 1e-5) {
    let mean = x.mean(dim=-1, keepdim=true)
    let var = x.var(dim=-1, keepdim=true)
    x = (x - mean) / (var + eps).sqrt()  -- in-place modification
    x = x * gamma + beta                  -- in-place modification
}

fn forward(input: tensor) -> tensor {
    let hidden = input.clone()  -- clone to get an owned mutable copy
    layer_norm(mut hidden, gamma, beta)
    -- hidden is now normalized, modified in-place (no extra allocation)
    return move hidden
}
```

### 12.5 Arena Budget Error at Compile Time

```nova
arena gpu(budget=80gb) {
    let model = GPT(vocab=50257, dim=12288, heads=96, layers=96)
    -- compiler analysis:
    --   parameters ≈ 175 billion
    --   f16 storage: 175B × 2 bytes = 350 GB
    --   COMPILE ERROR: static allocation of ~350 GB exceeds arena budget of 80 GB

    arena gpu(budget=40gb) {
        for batch in dataloader {
            let loss = model.forward(batch)
            loss.backward()
        }
    }
}
```

Correct version:

```nova
arena gpu(budget=700gb) {
    -- 8×A100 80GB NVLink, presented as unified pool
    let model = GPT(vocab=50257, dim=12288, heads=96, layers=96)
    -- ~350 GB in f16: fits within 700 GB  ✓

    arena gpu(budget=200gb) {
        for batch in dataloader {
            let loss = model.forward(batch)
            loss.backward()
        }
    }
}
```

---

## 13. Error Catalog

Nova produces specific, actionable error messages for memory violations:

### Compile-Time Errors

| Error Code | Message | Cause |
|------------|---------|-------|
| `E100` | `arena budget exceeded: static allocations total {N} bytes, budget is {M} bytes` | Sum of compile-time-known allocations exceeds the declared budget |
| `E101` | `value '{name}' already moved at {location}` | Attempting to move a value that was definitely moved earlier in the same scope |
| `E102` | `arena reference escapes scope: '{name}' declared in arena at {location}` | An arena-allocated value is returned or stored outside the arena scope |
| `E103` | `nested arena budget {N} exceeds parent remaining capacity {M}` | Static child arena budget exceeds static parent remaining budget |

### Runtime Errors (Debug Mode)

| Error Code | Message | Cause |
|------------|---------|-------|
| `R200` | `use of moved value '{name}' (moved at {location})` | Accessing a value after it was moved |
| `R201` | `cannot borrow '{name}' as mutable: already borrowed as immutable ({N} active borrows)` | Mutable borrow requested while immutable borrows exist |
| `R202` | `cannot borrow '{name}' as immutable: already borrowed as mutable` | Immutable borrow requested while a mutable borrow exists |
| `R203` | `borrow of '{name}' outlives owner (owner scope ended at {location})` | A borrowed reference is used after the owner was freed |
| `R204` | `ArenaOverflow: allocation of {N} bytes exceeds remaining arena budget ({M} bytes free)` | Dynamic arena allocation exceeds remaining capacity |
| `R205` | `double free: '{name}' has already been freed` | Attempting to free a value that was already freed (should not occur in correct Nova code) |

---

## 14. Future Considerations

The following features are under consideration for future versions of the memory model:

1. **Region inference.** Automatic inference of arena placement for tensor allocations, reducing the need for explicit `arena` blocks in simple cases.

2. **Compile-time borrow analysis (optional).** An opt-in mode where functions annotated with `@[static_borrows]` undergo Rust-style lifetime analysis, providing compile-time guarantees without runtime overhead even in debug mode.

3. **Custom allocators.** Allowing users to supply custom allocation strategies for arenas (slab allocators, pool allocators) beyond the default bump allocator.

4. **Arena checkpoints.** The ability to save and restore arena state for speculative computation:

   ```nova
   arena gpu(budget=10gb) {
       let checkpoint = arena.save()
       let result = try_computation()
       if !result.valid {
           arena.restore(checkpoint)  -- roll back allocations
       }
   }
   ```

5. **Distributed arenas.** Arenas spanning multiple devices or nodes for model-parallel training, with the compiler verifying that cross-device references use appropriate transfer primitives.

6. **Escape analysis.** Compiler optimization to stack-allocate values that provably do not escape their declaring function, avoiding heap allocation entirely.

---

*This specification is part of the Nova Language Reference. For related specifications, see the type system, concurrency model, and GPU execution model documents.*

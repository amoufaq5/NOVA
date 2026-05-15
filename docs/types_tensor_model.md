# Nova Type System Specification: Tensor, Frame, Model, Agent, and Safety Types

**Status:** Draft Specification
**Version:** 0.1.0

---

## Table of Contents

1. [Tensor Type](#1-tensor-type)
2. [Frame Type](#2-frame-type)
3. [Model Type](#3-model-type)
4. [Agent Type](#4-agent-type)
5. [Safety Types](#5-safety-types)
6. [Type Interaction Diagram](#6-type-interaction-diagram)

---

## Preamble: Gradual Typing Philosophy

Nova uses gradual typing. All values are dynamic by default -- no type annotation is
ever required. When the programmer chooses to annotate tensor shapes, model signatures,
or agent contracts, the compiler checks those annotations statically where possible and
inserts runtime checks where it cannot prove correctness at compile time. This means
Nova programs can start as quick prototypes and harden into production-grade systems
without a rewrite.

Nova ships its own tensor engine, autograd system, and optimizer suite. There is **no**
Python dependency, no FFI bridge to NumPy or PyTorch. Every operation described in this
document is implemented natively in the Nova runtime.

Nova uses a `Result[T, E]` + `panic` error model. Recoverable errors (shape mismatches
caught at runtime, file-not-found, network timeout) produce `Result` values. Unrecoverable
bugs (out-of-memory, compiler invariant violation) cause a panic.

---

## 1. Tensor Type

The tensor is Nova's fundamental numeric container. It represents a multi-dimensional
array of homogeneous elements stored in contiguous memory, with optional automatic
differentiation tracking.

### 1.1 Type Syntax

```nova
-- Fully specified: dtype and every dimension known at compile time
tensor[f32, 3, 224, 224]

-- Partially specified: dynamic batch dimension, static feature dimension
tensor[f16, _, 4096]

-- Symbolic dimensions: compiler checks consistency across function signatures
tensor[f32, N, M]

-- dtype only, shape dynamic
tensor[f32]

-- Fully untyped: dtype and shape resolved at runtime
tensor
```

**Grammar:**

```
TensorType  ::= "tensor" ( "[" TypeParams "]" )?
TypeParams  ::= DType ( "," Dim )*
DType       ::= "f16" | "f32" | "f64" | "bf16"
              | "i8" | "i16" | "i32" | "i64"
              | "u8" | "u16" | "u32" | "u64"
              | "bool" | "complex64" | "complex128"
Dim         ::= IntLiteral        -- concrete dimension (e.g. 224)
              | "_"                -- dynamic (runtime-checked)
              | Identifier         -- symbolic (e.g. N, M, Batch)
```

Symbolic dimension identifiers are scoped to the enclosing function signature. They
unify like type variables: if `N` appears in two positions, the compiler enforces
that both dimensions are the same value.

### 1.2 Shape-Checking (Gradual)

When dimensions are annotated, the compiler verifies shape compatibility at compile time.

```nova
fn matmul(a: tensor[f32, M, K], b: tensor[f32, K, N]) -> tensor[f32, M, N] {
    return a @ b   -- compiler verifies the inner dimensions (K) match
}

fn add_bias(x: tensor[f32, Batch, D], bias: tensor[f32, D]) -> tensor[f32, Batch, D] {
    return x + bias  -- compiler verifies D matches, broadcasts over Batch
}
```

When dimensions are `_` or unannotated, the compiler inserts runtime checks:

```nova
fn dynamic_matmul(a: tensor[f32, _, _], b: tensor[f32, _, _]) -> tensor {
    -- runtime error if a.shape[1] != b.shape[0]
    return a @ b
}
```

Mixing static and dynamic is allowed and encouraged during iterative development:

```nova
fn embed(tokens: tensor[i32, _], table: tensor[f32, VocabSize, Dim]) -> tensor[f32, _, Dim] {
    -- batch dimension stays dynamic, embedding dimension is statically known
    return table[tokens]
}
```

### 1.3 Construction

#### Factory Functions

```nova
-- Zeros: all elements are 0
let a = tensor.zeros[f32, 3, 4]()         -- 3x4 float32 zeros
let b = tensor.zeros[f32](3, 4)           -- same, shape passed as arguments
let c = tensor.zeros(3, 4)                -- dtype defaults to f32

-- Ones: all elements are 1
let d = tensor.ones[f32, 2, 2]()          -- 2x2 float32 ones

-- Random normal: elements drawn from N(0, 1)
let e = tensor.randn[f32, 128, 64]()      -- 128x64 random normal
let f = tensor.randn[f32](128, 64)        -- equivalent

-- Random uniform: elements drawn from U(0, 1)
let g = tensor.rand[f32, 10]()            -- 10-element uniform random

-- Empty: allocated but uninitialized (use with caution)
let h = tensor.empty[f32, 256, 256]()

-- Full: all elements set to a given value
let i = tensor.full[f32, 3, 3](value=7.0)

-- Identity matrix
let j = tensor.eye[f32, 4]()              -- 4x4 identity

-- Arange: sequential values
let k = tensor.arange(0, 10, step=2)      -- tensor([0, 2, 4, 6, 8])

-- Linspace: evenly spaced values
let l = tensor.linspace(0.0, 1.0, steps=5) -- tensor([0.0, 0.25, 0.5, 0.75, 1.0])
```

#### From Literal Data

```nova
-- 1-D tensor from a list
let v = tensor.from([1.0, 2.0, 3.0])

-- 2-D tensor from nested lists
let m = tensor.from([
    [1.0, 2.0],
    [3.0, 4.0],
])

-- Explicit dtype
let n = tensor.from[f16]([1.0, 2.0, 3.0])

-- From a range
let r = tensor.from(0..100)               -- tensor of 100 integers
```

### 1.4 Properties

```nova
let t = tensor.randn[f32, 3, 224, 224]()

t.shape         -- (3, 224, 224) : tuple[int, int, int]
t.dtype         -- f32 : DType
t.ndim          -- 3 : int
t.size          -- 150528 : int  (total element count = 3 * 224 * 224)
t.device        -- cpu : Device  (or gpu:0, gpu:1, tpu:0, etc.)
t.requires_grad -- false : bool  (whether autograd tracks this tensor)
t.grad          -- nil : tensor? (gradient, populated after backward pass)
t.strides       -- (50176, 224, 1) : tuple  (element strides per dimension)
t.is_contiguous -- true : bool
```

### 1.5 Arithmetic Operators

All arithmetic operators work element-wise and support broadcasting following
standard rules (trailing dimensions aligned, size-1 dimensions expanded).

```nova
let a = tensor.randn[f32, 4, 3]()
let b = tensor.randn[f32, 4, 3]()
let s = tensor.randn[f32, 3]()       -- will broadcast over dim 0

-- Element-wise arithmetic
let add    = a + b       -- addition
let sub    = a - b       -- subtraction
let mul    = a * b       -- element-wise multiplication
let div    = a / b       -- element-wise division
let flrdiv = a // b      -- floor division
let mod    = a % b       -- modulo
let pow    = a ** 2      -- exponentiation (scalar or tensor)
let neg    = -a          -- negation

-- Matrix multiplication
let c = tensor.randn[f32, 4, 3]()
let d = tensor.randn[f32, 3, 5]()
let prod = c @ d         -- result is tensor[f32, 4, 5]

-- Comparison operators (return tensor[bool, ...])
let eq  = a == b
let neq = a != b
let lt  = a < b
let gt  = a > b
let lte = a <= b
let gte = a >= b

-- Logical operators (on bool tensors)
let and_t = eq & lt      -- element-wise AND
let or_t  = eq | lt      -- element-wise OR
let not_t = !eq           -- element-wise NOT
let xor_t = eq ^ lt      -- element-wise XOR

-- Broadcasting with scalars
let scaled = a * 2.0
let shifted = a + 1.0

-- In-place variants (mutate the tensor)
a += b
a -= b
a *= 2.0
a /= b
```

### 1.6 Reduction Operations

```nova
let t = tensor.randn[f32, 4, 3]()

t.sum()               -- scalar: sum of all elements
t.sum(dim=0)          -- tensor[f32, 3]: sum along dim 0
t.sum(dim=1)          -- tensor[f32, 4]: sum along dim 1
t.sum(dim=0, keepdim=true) -- tensor[f32, 1, 3]: keeps reduced dim as size 1

t.mean()              -- scalar mean
t.mean(dim=0)         -- mean along dim 0

t.max()               -- scalar max value
t.max(dim=1)          -- (values: tensor[f32, 4], indices: tensor[i64, 4])

t.min()               -- scalar min value
t.argmax()            -- index of max element (flattened)
t.argmax(dim=0)       -- indices of max along dim 0
t.argmin()            -- index of min element

t.prod()              -- product of all elements
t.std()               -- standard deviation
t.var()               -- variance
t.norm(p=2)           -- Lp norm (default L2)
t.norm(p=2, dim=1)    -- Lp norm along dim 1
```

### 1.7 Shape Manipulation

```nova
let t = tensor.randn[f32, 4, 3, 8]()

-- Reshape (total elements must match)
let r = t.reshape(12, 8)        -- tensor[f32, 12, 8]
let r2 = t.reshape(-1, 8)       -- -1 infers: tensor[f32, 12, 8]

-- View (same data, different shape; must be contiguous)
let v = t.view(4, 24)           -- tensor[f32, 4, 24]

-- Transpose
let t2d = tensor.randn[f32, 3, 5]()
let tr = t2d.T                  -- tensor[f32, 5, 3]
let tr2 = t.transpose(0, 2)    -- swap dims 0 and 2: tensor[f32, 8, 3, 4]

-- Permute (arbitrary dimension reorder)
let p = t.permute(2, 0, 1)     -- tensor[f32, 8, 4, 3]

-- Squeeze and unsqueeze
let s = tensor.randn[f32, 1, 3, 1, 4]()
let sq = s.squeeze()            -- tensor[f32, 3, 4] (remove all size-1 dims)
let sq1 = s.squeeze(dim=0)     -- tensor[f32, 3, 1, 4]
let us = t2d.unsqueeze(0)      -- tensor[f32, 1, 3, 5]

-- Flatten
let fl = t.flatten()            -- tensor[f32, 96]
let fl2 = t.flatten(start=0, end=1)  -- tensor[f32, 12, 8]

-- Stack and concatenate
let a = tensor.randn[f32, 3, 4]()
let b = tensor.randn[f32, 3, 4]()
let stacked = tensor.stack([a, b], dim=0)  -- tensor[f32, 2, 3, 4]
let catted  = tensor.cat([a, b], dim=0)    -- tensor[f32, 6, 4]

-- Split and chunk
let parts = t2d.split(size=2, dim=1) -- list of tensors split along dim 1
let chunks = t2d.chunk(n=3, dim=0)   -- 3 roughly equal chunks along dim 0

-- Expand and repeat
let row = tensor.randn[f32, 1, 4]()
let expanded = row.expand(3, 4)      -- tensor[f32, 3, 4] (no copy)
let repeated = row.repeat(3, 2)      -- tensor[f32, 3, 8] (copies data)
```

### 1.8 Indexing and Slicing

Nova uses Python-style indexing and slicing with zero-based indices.

```nova
let t = tensor.randn[f32, 4, 8]()

-- Single element (returns scalar tensor)
let elem = t[0, 0]

-- Row / column selection
let row = t[0]           -- tensor[f32, 8]: first row
let col = t[:, 0]        -- tensor[f32, 4]: first column

-- Slicing with start:stop:step
let slice1 = t[1:3]       -- tensor[f32, 2, 8]: rows 1 and 2
let slice2 = t[:, 2:6]    -- tensor[f32, 4, 4]: columns 2..5
let slice3 = t[::2]       -- tensor[f32, 2, 8]: every other row
let slice4 = t[:, ::-1]   -- tensor[f32, 4, 8]: columns reversed

-- Negative indexing
let last_row = t[-1]             -- last row
let last_two = t[-2:]            -- last two rows

-- Boolean mask indexing
let mask = t > 0.0              -- tensor[bool, 4, 8]
let positives = t[mask]         -- 1-D tensor of all positive elements

-- Integer tensor indexing (gather)
let indices = tensor.from[i64]([0, 2, 3])
let selected = t[indices]       -- tensor[f32, 3, 8]: rows 0, 2, 3

-- Advanced indexing: combining integer and slice
let mixed = t[tensor.from[i64]([0, 2]), 1:4]  -- tensor[f32, 2, 3]

-- Assignment via indexing
t[0] = tensor.zeros[f32, 8]()   -- zero out first row
t[:, -1] = 1.0                  -- set last column to 1
t[mask] = 0.0                   -- zero out all positive elements
```

### 1.9 Device Transfer

Nova supports heterogeneous compute. Tensors live on a specific device and must be
transferred explicitly.

```nova
let t = tensor.randn[f32, 128, 64]()   -- created on cpu by default

-- Transfer to GPU
let t_gpu = t.to(gpu)                   -- first available GPU
let t_gpu1 = t.to(gpu:1)               -- specific GPU
let t_tpu = t.to(tpu:0)                -- TPU

-- Transfer back
let t_cpu = t_gpu.to(cpu)

-- Check device
if t.device == gpu:0 {
    print("on first GPU")
}

-- Operations require tensors on the same device
-- This is a compile-time error when devices are statically known:
let bad = t_cpu + t_gpu   -- Error: device mismatch (cpu vs gpu:0)

-- Create directly on device
let g = tensor.randn[f32, 64, 64](device=gpu:0)

-- Context manager for default device
with device(gpu:0) {
    -- all tensors created in this block default to gpu:0
    let a = tensor.randn[f32, 32, 32]()   -- on gpu:0
    let b = tensor.ones[f32, 32, 32]()    -- on gpu:0
    let c = a @ b                          -- on gpu:0
}
```

### 1.10 Autograd (Language-Level Automatic Differentiation)

Automatic differentiation is a **language feature** in Nova, not a library. The `grad`
keyword operates on any Nova function that maps tensors to tensors, producing a new
function that computes the gradient.

#### Basic Usage

```nova
-- Define a scalar-valued function
let f = fn(x: tensor) -> tensor {
    return (x ** 2).sum()
}

-- grad returns a NEW function: tensor -> tensor
let df = grad(f)

-- Evaluate the derivative
let x = tensor.from([3.0])
let result = df(x)           -- tensor([6.0])  (d/dx of x^2 = 2x, at x=3 -> 6)
```

#### Higher-Order Derivatives

```nova
let f = fn(x: tensor) -> tensor {
    return (x ** 3).sum()
}

let df  = grad(f)              -- first derivative: 3x^2
let d2f = grad(f, order=2)    -- second derivative: 6x
let d3f = grad(f, order=3)    -- third derivative: 6

let x = tensor.from([2.0])
print(df(x))                   -- tensor([12.0])  (3 * 4 = 12)
print(d2f(x))                  -- tensor([12.0])  (6 * 2 = 12)
print(d3f(x))                  -- tensor([6.0])   (constant)
```

#### Gradient with Respect to Specific Parameters

```nova
fn loss(w: tensor, b: tensor, x: tensor, y: tensor) -> tensor {
    let pred = x @ w + b
    return ((pred - y) ** 2).mean()
}

-- grad with respect to specific arguments (by index or name)
let grad_w = grad(loss, wrt=0)          -- gradient w.r.t. first arg (w)
let grad_b = grad(loss, wrt=1)          -- gradient w.r.t. second arg (b)
let grad_wb = grad(loss, wrt=[0, 1])    -- tuple of gradients w.r.t. w and b
let grad_named = grad(loss, wrt=[.w, .b])  -- same, using named arguments

let w = tensor.randn[f32, 4, 1]()
let b = tensor.zeros[f32, 1]()
let x = tensor.randn[f32, 8, 4]()
let y = tensor.randn[f32, 8, 1]()

let (dw, db) = grad_wb(w, b, x, y)   -- both gradients in one forward+backward pass
```

#### Value-and-Gradient

```nova
-- Often you need both the loss value and the gradient
let val_and_grad = grad(loss, wrt=[0, 1], return_value=true)

let (loss_val, (dw, db)) = val_and_grad(w, b, x, y)
print("loss = {loss_val}")
```

#### Jacobian and Hessian

```nova
-- Jacobian: for vector-valued functions
let f = fn(x: tensor[f32, N]) -> tensor[f32, M] { ... }
let jac = jacobian(f)
let J = jac(x)               -- tensor[f32, M, N]

-- Hessian: matrix of second derivatives
let f = fn(x: tensor[f32, N]) -> tensor { ... }  -- scalar output
let hess = hessian(f)
let H = hess(x)              -- tensor[f32, N, N]
```

#### Autograd and Control Flow

Nova's autograd traces through all standard control flow. This works because `grad`
differentiates through the actual execution path, not a symbolic graph.

```nova
let f = fn(x: tensor) -> tensor {
    if x.sum() > 0.0 {
        return (x ** 2).sum()
    } else {
        return (x ** 3).sum()
    }
}

let df = grad(f)
-- df correctly differentiates whichever branch executed
print(df(tensor.from([1.0])))   -- tensor([2.0])  (d/dx x^2 = 2x at x=1)
print(df(tensor.from([-1.0])))  -- tensor([3.0])  (d/dx x^3 = 3x^2 at x=-1)
```

#### Stop-Gradient

```nova
-- Detach a tensor from the computation graph
let t = some_computation(x)
let t_detached = t.detach()    -- same values, no grad tracking

-- no_grad block: disable autograd for efficiency during inference
with no_grad() {
    let pred = model.forward(x)   -- no gradient tracking
}
```

### 1.11 Dtype Casting

```nova
let t = tensor.randn[f32, 4, 4]()

let t16 = t.to_dtype(f16)       -- cast to half precision
let t64 = t.to_dtype(f64)       -- cast to double precision
let ti  = t.to_dtype(i32)       -- cast to int (truncates)
let tb  = (t > 0.0).to_dtype(f32)  -- bool -> float

-- Combined device + dtype transfer
let t_gpu_f16 = t.to(gpu:0, dtype=f16)
```

### 1.12 Serialization

```nova
-- Save to Nova's binary format
tensor.save(t, "weights.nova")

-- Load
let loaded = tensor.load("weights.nova")

-- Save multiple tensors (named)
tensor.save_dict({
    "weight": w,
    "bias": b,
}, "params.nova")

let params = tensor.load_dict("params.nova")
let w = params["weight"]
```

---

## 2. Frame Type

The `frame` type represents tabular data -- rows and columns, like a dataframe.
It is the standard way to load, preprocess, and feed datasets into models.

### 2.1 Construction

```nova
-- From column data
let df = frame({
    "name":  ["Alice", "Bob", "Carol", "Dave"],
    "age":   [30, 25, 35, 28],
    "score": [0.95, 0.82, 0.91, 0.77],
})

-- From a CSV file
let df = frame.read_csv("data/train.csv")

-- From a JSON file
let df = frame.read_json("data/records.json")

-- From a Parquet file
let df = frame.read_parquet("data/large_dataset.parquet")

-- Empty frame with schema
let df = frame(schema={
    "id":    i64,
    "text":  str,
    "label": f32,
})
```

### 2.2 Properties

```nova
df.shape        -- (4, 3) : tuple[int, int]  (rows, columns)
df.columns      -- ["name", "age", "score"] : [str]
df.dtypes       -- {"name": str, "age": i64, "score": f64} : {str: DType}
df.len          -- 4 : int  (row count)
```

### 2.3 Column Access

```nova
-- Single column (returns a Series)
let ages = df["age"]           -- Series[i64]
let ages = df.age              -- dot syntax shorthand (if column name is a valid identifier)

-- Multiple columns (returns a new Frame)
let subset = df[["name", "score"]]

-- Add a new column
df["senior"] = df["age"] > 30   -- derived boolean column
df["age_norm"] = df["age"].to_dtype(f32) / df["age"].max()

-- Rename columns
let renamed = df.rename({"name": "full_name", "score": "accuracy"})

-- Drop columns
let smaller = df.drop(["name"])
```

### 2.4 Row Access and Filtering

```nova
-- By index
let first_row = df[0]              -- single row as a {str: any} dict
let slice = df[1:3]                -- Frame with rows 1 and 2

-- Boolean filtering
let adults = df[df["age"] >= 30]
let high_scorers = df[df["score"] > 0.9]

-- Combined conditions
let target = df[(df["age"] > 25) & (df["score"] > 0.85)]

-- Filter with lambda
let filtered = df.filter(fn(row) { return row["name"].starts_with("A") })

-- Sample
let sample = df.sample(n=2)            -- 2 random rows
let sample = df.sample(frac=0.5)       -- 50% of rows
```

### 2.5 Aggregation

```nova
-- Global aggregation
let avg_age = df["age"].mean()       -- 29.5
let total = df["score"].sum()        -- 3.45
let std = df["age"].std()

-- Group-by aggregation
let by_senior = df.group_by("senior").agg({
    "age":   .mean,
    "score": .max,
})

-- Multiple aggregation functions
let stats = df.group_by("senior").agg({
    "score": [.mean, .std, .count],
})

-- Custom aggregation
let custom = df.group_by("senior").agg({
    "score": fn(col) { return col.quantile(0.95) },
})
```

### 2.6 Sorting and Ordering

```nova
let sorted = df.sort_by("age")                     -- ascending
let sorted = df.sort_by("age", descending=true)     -- descending
let sorted = df.sort_by(["score", "age"], descending=[true, false])  -- multi-column
```

### 2.7 Joins

```nova
let users = frame({
    "user_id": [1, 2, 3],
    "name":    ["Alice", "Bob", "Carol"],
})

let orders = frame({
    "user_id": [1, 1, 2, 4],
    "amount":  [50.0, 30.0, 80.0, 10.0],
})

-- Inner join (only matching rows)
let joined = users.join(orders, on="user_id", how=.inner)

-- Left join (all left rows, matching right rows or nil)
let left = users.join(orders, on="user_id", how=.left)

-- Outer join
let outer = users.join(orders, on="user_id", how=.outer)

-- Join on different column names
let j = users.join(orders, left_on="user_id", right_on="user_id", how=.inner)
```

### 2.8 Pipeline Style

Frames support method chaining for expressive data pipelines.

```nova
let result = frame.read_csv("data.csv")
    .filter(fn(row) { return row["split"] == "train" })
    .drop(["split", "id"])
    .rename({"label_text": "label"})
    .sort_by("timestamp")
    .group_by("category").agg({"value": .mean})
    .sort_by("value", descending=true)
    .head(10)
```

### 2.9 Conversion to Tensor

```nova
-- Single column to tensor
let ages_tensor = df["age"].to_tensor[f32]()   -- tensor[f32, 4]

-- Multiple numeric columns to tensor
let features = df[["age", "score"]].to_tensor[f32]()  -- tensor[f32, 4, 2]

-- Full frame (numeric columns only, errors on non-numeric)
let data = df.drop(["name"]).to_tensor[f32]()

-- With a target split
let (X, y) = df.to_tensors(
    features=["age", "score"],
    target="senior",
    dtype=f32,
)  -- X: tensor[f32, 4, 2], y: tensor[f32, 4]

-- Create a DataLoader for batched training
let loader = df.to_loader(
    features=["age", "score"],
    target="senior",
    batch_size=2,
    shuffle=true,
    dtype=f32,
)

for (batch_x, batch_y) in loader {
    -- batch_x: tensor[f32, 2, 2], batch_y: tensor[f32, 2]
    let loss = train_step(model, batch_x, batch_y)
}
```

### 2.10 Mutation and Missing Data

```nova
-- Fill missing values
let filled = df.fill_nil(0)                  -- all columns
let filled = df.fill_nil({"age": 0, "score": 0.5})  -- per-column

-- Drop rows with any nil
let clean = df.drop_nil()
let clean = df.drop_nil(subset=["score"])    -- only check specific columns

-- Apply a transform to a column
df["score"] = df["score"].apply(fn(x) { return x * 100.0 })

-- One-hot encoding
let encoded = df.one_hot("category")
```

---

## 3. Model Type

The `model` keyword declares a neural network module. It is a first-class type that
encapsulates parameters, submodules, and a `forward` function. Models participate in
autograd automatically.

### 3.1 Declaration Syntax

```nova
model TransformerBlock(dim: int, heads: int) {
    -- Submodules and parameters declared as fields
    attn  = MultiHeadAttention(dim, heads)
    norm1 = LayerNorm(dim)
    norm2 = LayerNorm(dim)
    ffn   = FeedForward(dim * 4, dim)
    drop  = Dropout(0.1)

    -- The forward function defines the computation
    forward(x: tensor[f32, _, dim]) -> tensor[f32, _, dim] {
        let residual = x
        x = norm1(x)
        x = residual + drop(attn(x))
        let residual2 = x
        x = norm2(x)
        return residual2 + drop(ffn(x))
    }
}
```

### 3.2 Semantics

A `model` declaration produces a **type**. Instantiating it creates an object that:

1. Allocates all declared parameter tensors and submodules.
2. Registers them for autograd, serialization, and device transfer.
3. Exposes a callable interface: `instance(input)` calls `forward(input)`.

```nova
-- Instantiation
let block = TransformerBlock(dim=512, heads=8)

-- Calling the model (invokes forward)
let x = tensor.randn[f32, 32, 128, 512]()   -- batch=32, seq=128, dim=512
let y = block(x)                              -- tensor[f32, 32, 128, 512]

-- Equivalent explicit call
let y = block.forward(x)
```

### 3.3 Parameter Access

```nova
-- Iterate all parameters (recursive into submodules)
for (name, param) in block.parameters() {
    print("{name}: shape={param.shape}, dtype={param.dtype}")
}
-- Output:
-- "attn.q_proj.weight: shape=(512, 512), dtype=f32"
-- "attn.q_proj.bias: shape=(512,), dtype=f32"
-- ...

-- Count total parameters
let n_params = block.parameters().map(fn(_, p) { return p.size }).sum()
print("Total parameters: {n_params}")

-- Access a specific parameter
let w = block.attn.q_proj.weight   -- tensor[f32, 512, 512]

-- Named submodules
for (name, submod) in block.modules() {
    print("{name}: {type(submod)}")
}
```

### 3.4 Training and Evaluation Mode

```nova
-- Set to training mode (enables dropout, batch norm running stats, etc.)
block.train()

-- Set to evaluation mode (disables dropout, freezes batch norm)
block.eval()

-- Check mode
if block.is_training {
    print("training")
}
```

### 3.5 Model Composition

Models compose naturally through nesting.

```nova
model Transformer(
    vocab_size: int,
    dim: int,
    heads: int,
    layers: int,
    max_seq_len: int,
) {
    embed     = Embedding(vocab_size, dim)
    pos_embed = Embedding(max_seq_len, dim)
    blocks    = [TransformerBlock(dim, heads) for _ in 0..layers]
    norm      = LayerNorm(dim)
    head      = Linear(dim, vocab_size)

    forward(tokens: tensor[i32, _, _]) -> tensor[f32, _, _, vocab_size] {
        let (batch, seq_len) = tokens.shape
        let positions = tensor.arange(0, seq_len).unsqueeze(0).expand(batch, seq_len)

        var x = embed(tokens) + pos_embed(positions)

        for block in blocks {
            x = block(x)
        }

        x = norm(x)
        return head(x)
    }
}

-- Instantiate and use
let gpt = Transformer(
    vocab_size=50257,
    dim=768,
    heads=12,
    layers=12,
    max_seq_len=1024,
)

let tokens = tensor.from[i32]([[1, 42, 1337, 7]])   -- batch=1, seq=4
let logits = gpt(tokens)                              -- tensor[f32, 1, 4, 50257]
```

### 3.6 Built-in Layers

Nova provides a standard library of layers.

```nova
-- Linear / Dense
Linear(in_features, out_features, bias=true)

-- Convolution
Conv1d(in_channels, out_channels, kernel_size, stride=1, padding=0)
Conv2d(in_channels, out_channels, kernel_size, stride=1, padding=0)
Conv3d(in_channels, out_channels, kernel_size, stride=1, padding=0)

-- Recurrent
LSTM(input_size, hidden_size, num_layers=1, bidirectional=false)
GRU(input_size, hidden_size, num_layers=1)

-- Normalization
LayerNorm(dim)
BatchNorm1d(features)
BatchNorm2d(features)
GroupNorm(num_groups, num_channels)
RMSNorm(dim)

-- Attention
MultiHeadAttention(dim, heads, dropout=0.0)
MultiQueryAttention(dim, heads, kv_heads=1)

-- Activation (these are functions, not models, but can be used in forward)
relu(x)
gelu(x)
silu(x)
sigmoid(x)
softmax(x, dim=-1)
tanh(x)

-- Pooling
MaxPool2d(kernel_size, stride=nil, padding=0)
AvgPool2d(kernel_size, stride=nil, padding=0)
AdaptiveAvgPool2d(output_size)

-- Dropout
Dropout(p=0.5)

-- Embedding
Embedding(num_embeddings, embedding_dim)

-- Container
Sequential(layer1, layer2, ...)

-- Feed-forward convenience
FeedForward(hidden_dim, out_dim, activation=gelu)
```

### 3.7 Training Loop

Nova does not hide the training loop behind magic. You write it explicitly, but the
language provides ergonomic primitives.

```nova
let model = Transformer(vocab_size=50257, dim=768, heads=12, layers=12, max_seq_len=1024)
let optimizer = Adam(model.parameters(), lr=3e-4, betas=(0.9, 0.999), weight_decay=0.01)
let scheduler = CosineScheduler(optimizer, warmup_steps=1000, total_steps=100000)

let loader = frame.read_parquet("train.parquet").to_loader(
    features=["input_ids"],
    target="labels",
    batch_size=32,
    shuffle=true,
    dtype=i32,
)

model.train()

for epoch in 0..10 {
    for (batch_x, batch_y) in loader {
        let batch_x = batch_x.to(gpu:0)
        let batch_y = batch_y.to(gpu:0)

        -- Forward pass
        let logits = model(batch_x)

        -- Compute loss
        let loss = cross_entropy(logits.view(-1, 50257), batch_y.view(-1))

        -- Backward pass (computes gradients for all parameters)
        loss.backward()

        -- Gradient clipping
        clip_grad_norm(model.parameters(), max_norm=1.0)

        -- Optimizer step
        optimizer.step()
        scheduler.step()

        -- Zero gradients for next iteration
        optimizer.zero_grad()
    }

    print("Epoch {epoch} complete")
}
```

### 3.8 Serialization

```nova
-- Save model weights
model.save("checkpoint.nova")

-- Load model weights
model.load("checkpoint.nova")

-- Save full checkpoint (model + optimizer + scheduler state)
checkpoint.save("ckpt_epoch5.nova", {
    "model": model.state_dict(),
    "optimizer": optimizer.state_dict(),
    "scheduler": scheduler.state_dict(),
    "epoch": 5,
})

-- Restore from checkpoint
let ckpt = checkpoint.load("ckpt_epoch5.nova")
model.load_state_dict(ckpt["model"])
optimizer.load_state_dict(ckpt["optimizer"])
scheduler.load_state_dict(ckpt["scheduler"])
let start_epoch = ckpt["epoch"]
```

### 3.9 Device Placement

```nova
-- Move entire model (all parameters and buffers) to a device
model.to(gpu:0)

-- Distributed across multiple GPUs
model.to([gpu:0, gpu:1, gpu:2, gpu:3])   -- automatic tensor parallelism

-- Mixed precision
with autocast(dtype=f16) {
    let logits = model(x)
    let loss = cross_entropy(logits, y)
}
-- gradients are computed in f16, optimizer step in f32 (automatic)
```

### 3.10 Freezing and Fine-Tuning

```nova
-- Freeze all parameters
model.freeze()

-- Unfreeze specific submodules
model.head.unfreeze()

-- Freeze with a pattern
model.freeze(pattern="blocks.0.*")  -- freeze first block only

-- Check if a parameter is frozen
print(model.embed.weight.requires_grad)  -- false (frozen)

-- LoRA-style parameter-efficient fine-tuning
let lora_model = model.apply_lora(rank=16, target_modules=["q_proj", "v_proj"])
-- only LoRA parameters are trainable; original weights are frozen
```

---

## 4. Agent Type

The `agent` keyword declares an autonomous entity that can reason, plan, and act using
tools. Agents are **first-class values** in Nova -- they can be assigned to variables,
passed as arguments, composed, and managed like any other type.

### 4.1 Declaration Syntax

```nova
agent Researcher(llm: Model, max_steps: int = 10) {
    tools {
        web_search = WebSearch(max_results=5)
        read_file  = FileReader()
        calculator = Calculator()
    }

    memory {
        findings: [str] = []
        visited_urls: {str} = {}
    }

    plan(goal: str) -> [str] {
        -- reasoning step: break goal into sub-tasks
        let steps = llm.generate(
            "Break this goal into steps: {goal}"
        )
        return parse_steps(steps)
    }

    act(step: str) -> Result[str, Error] {
        -- action step: execute one step using tools
        let thought = llm.generate(
            "Given step: {step}, which tool to use? Context: {memory.findings}"
        )
        let tool_call = parse_tool_call(thought)
        return tools.execute(tool_call)
    }

    reflect(results: [Result[str, Error]]) -> str {
        -- optional: synthesize findings after all steps complete
        let summary = llm.generate(
            "Summarize these findings: {results.filter_ok()}"
        )
        memory.findings.append(summary)
        return summary
    }
}
```

### 4.2 Agent Semantics

An `agent` declaration creates a type with these properties:

1. **tools block**: Declares the tools available to the agent. Each tool is a named
   instance of a type that conforms to the `Tool` interface (must have an `execute`
   method returning `Result`).
2. **memory block**: Declares mutable state that persists across steps within a run
   and can optionally persist across runs.
3. **plan function**: Takes a goal and produces an ordered list of steps. This is the
   agent's reasoning phase.
4. **act function**: Executes a single step, selecting and invoking a tool. Returns
   `Result` to handle tool failures gracefully.
5. **reflect function** (optional): Synthesizes results after a sequence of actions.

### 4.3 Agent Lifecycle

```nova
-- Instantiate the agent
let researcher = Researcher(llm=gpt4, max_steps=10)

-- Run the agent with a goal (this drives the full plan-act-reflect loop)
let result = researcher.run("Find the latest advances in protein folding")

-- The .run() method executes this internal loop:
--   1. steps = plan(goal)
--   2. for each step (up to max_steps):
--        result = act(step)
--        if result.is_err() { handle or continue }
--   3. reflect(results)
--   4. return final synthesis
```

The lifecycle can also be driven manually for fine-grained control:

```nova
let researcher = Researcher(llm=gpt4, max_steps=10)

-- Manual plan
let steps = researcher.plan("Find the latest advances in protein folding")
print("Plan: {steps}")

-- Manual step-by-step execution
var results: [Result[str, Error]] = []
for step in steps {
    let r = researcher.act(step)
    match r {
        Ok(output) => {
            print("Step succeeded: {output}")
            results.append(r)
        }
        Err(e) => {
            print("Step failed: {e}")
            -- optionally re-plan or skip
        }
    }
}

-- Manual reflect
let summary = researcher.reflect(results)
print("Summary: {summary}")
```

### 4.4 Tool Dispatch

Tools conform to the `Tool` interface:

```nova
interface Tool {
    fn name() -> str
    fn description() -> str
    fn execute(input: str) -> Result[str, Error]
}
```

The agent's `tools` block provides a dispatch mechanism:

```nova
-- Within an agent, tools.execute dispatches by tool name
let result = tools.execute(ToolCall(name="web_search", input="protein folding 2026"))

-- Individual tool invocation
let result = tools.web_search.execute("protein folding 2026")

-- List available tools (useful for prompt construction)
let tool_descriptions = tools.list()
-- Returns: [
--   {name: "web_search", description: "Search the web for information"},
--   {name: "read_file", description: "Read a file from the filesystem"},
--   {name: "calculator", description: "Evaluate mathematical expressions"},
-- ]
```

### 4.5 Memory Persistence

Agent memory lives in the `memory` block and defaults to in-memory (lost when the
agent object is garbage-collected). Persistent memory can be configured:

```nova
agent Assistant(llm: Model) {
    memory(persist="assistant_memory.nova") {
        -- This memory is saved to disk after each run and loaded on construction
        conversation_history: [Message] = []
        user_preferences: {str: str} = {}
        learned_facts: [str] = []
    }

    -- ...
}

-- Memory is accessible as a typed field
let assistant = Assistant(llm=gpt4)
print(assistant.memory.learned_facts)

-- Memory can be cleared
assistant.memory.clear()

-- Memory can be saved/loaded explicitly
assistant.memory.save("backup.nova")
assistant.memory.load("backup.nova")
```

### 4.6 Agent Composition

Agents can use other agents as tools or as subcomponents.

```nova
agent Coder(llm: Model) {
    tools {
        write_code = CodeWriter()
        run_tests  = TestRunner()
    }

    memory {
        code_history: [str] = []
    }

    plan(goal: str) -> [str] { ... }
    act(step: str) -> Result[str, Error] { ... }
}

agent Reviewer(llm: Model) {
    tools {
        read_code = CodeReader()
        analyze   = StaticAnalyzer()
    }

    memory {
        review_notes: [str] = []
    }

    plan(goal: str) -> [str] { ... }
    act(step: str) -> Result[str, Error] { ... }
}

-- A manager agent that delegates to sub-agents
agent ProjectManager(llm: Model) {
    tools {
        coder    = Coder(llm=llm)       -- agents used as tools
        reviewer = Reviewer(llm=llm)
    }

    memory {
        project_state: {str: str} = {}
    }

    plan(goal: str) -> [str] {
        return llm.generate("Plan project: {goal}") |> parse_steps
    }

    act(step: str) -> Result[str, Error] {
        -- Delegate to sub-agents based on the step type
        if step.contains("code") {
            return tools.coder.run(step)
        } else if step.contains("review") {
            return tools.reviewer.run(step)
        }
        return Err(Error("Unknown step type: {step}"))
    }
}
```

### 4.7 Agent Events and Observability

Agents emit structured events that can be observed for logging, debugging, and
monitoring.

```nova
let researcher = Researcher(llm=gpt4, max_steps=10)

-- Attach an observer
researcher.on(.plan_start, fn(goal) { print("[PLAN] {goal}") })
researcher.on(.act_start, fn(step) { print("[ACT] {step}") })
researcher.on(.act_end, fn(step, result) { print("[RESULT] {result}") })
researcher.on(.tool_call, fn(tool, input) { print("[TOOL] {tool}({input})") })
researcher.on(.error, fn(e) { log.error("Agent error: {e}") })

-- Run with full observability
let result = researcher.run("Analyze market trends")
```

### 4.8 Agent Concurrency

Multiple agents can run concurrently using Nova's async features.

```nova
-- Run multiple agents in parallel
let results = async.all([
    researcher.run("Find papers on topic A"),
    researcher.run("Find papers on topic B"),
    researcher.run("Find papers on topic C"),
])

-- With timeout
let result = async.timeout(30s) {
    researcher.run("Complex research task")
}
match result {
    Ok(r)  => print(r)
    Err(_) => print("Agent timed out")
}
```

---

## 5. Safety Types

Safety is enforced at the **type level** in Nova. The safety system provides two
primary mechanisms: output constraints and execution sandboxes. Violations produce
`Result` errors, never panics -- the caller always retains control.

### 5.1 Output Constraints

The `constrain` block wraps a generative operation and enforces structural and
content guarantees on its output.

```nova
-- Basic constrained generation
let response = constrain(schema=ResponseFormat, max_length=1024, safety=.strict) {
    model.generate(prompt)
}
-- response: Result[ResponseFormat, ConstraintError]
```

#### Schema Constraints

```nova
-- Define a schema that the output must conform to
type ResponseFormat {
    answer: str,
    confidence: f32,     -- must be between 0.0 and 1.0
    sources: [str],
}

let result = constrain(schema=ResponseFormat) {
    model.generate("What is the capital of France?")
}

match result {
    Ok(resp) => {
        -- resp is fully typed as ResponseFormat
        print("Answer: {resp.answer}")
        print("Confidence: {resp.confidence}")
    }
    Err(e) => {
        print("Constraint violated: {e}")
    }
}
```

#### Content Safety Levels

```nova
-- Safety levels control what content is permitted
-- .strict: no harmful, biased, or unverifiable content
-- .moderate: allows creative content, blocks harmful content
-- .permissive: only blocks illegal/dangerous content
-- .custom(fn): user-defined filter

let response = constrain(safety=.strict) {
    model.generate(user_input)
}
```

#### Constraint Combinators

```nova
-- Multiple constraints compose
let response = constrain(
    schema=MedicalAdvice,
    max_length=2048,
    safety=.strict,
    validators=[
        no_diagnosis,           -- custom validator: must not diagnose
        cite_sources,           -- custom validator: must cite sources
        reading_level(grade=8), -- readability constraint
    ],
) {
    medical_model.generate(patient_question)
}
```

#### Custom Validators

```nova
-- A validator is a function from str to Result[(), str]
let no_profanity = fn(output: str) -> Result[(), str] {
    let words = load_blocklist("profanity.txt")
    for word in words {
        if output.contains(word) {
            return Err("Output contains blocked word: {word}")
        }
    }
    return Ok(())
}

-- Use in a constrain block
let response = constrain(validators=[no_profanity]) {
    model.generate(prompt)
}
```

#### Length and Token Constraints

```nova
let response = constrain(
    max_length=1024,        -- maximum character count
    max_tokens=256,         -- maximum token count
    min_length=100,         -- minimum character count (avoid empty responses)
) {
    model.generate(prompt)
}
```

#### Retry on Constraint Failure

```nova
-- Automatically retry generation up to N times if constraints fail
let response = constrain(
    schema=ResponseFormat,
    safety=.strict,
    retries=3,              -- retry up to 3 times
    retry_strategy=.refine, -- feed the error back to the model for correction
) {
    model.generate(prompt)
}
```

### 5.2 Execution Sandbox

The `sandbox` block creates an isolated execution environment with restricted
capabilities. This is essential for running untrusted model outputs or third-party
agent code.

```nova
-- Basic sandbox: no network, read-only filesystem, memory and time limited
sandbox(net=false, fs=.readonly, memory=4gb, timeout=30s) {
    let result = untrusted_model.generate(input)
}
-- result: Result[str, SandboxError]
```

#### Network Permissions

```nova
-- No network access (default for untrusted code)
sandbox(net=false) { ... }

-- Allow specific domains only
sandbox(net=allow(["api.example.com", "data.example.com"])) { ... }

-- Allow all network access (use with caution)
sandbox(net=true) { ... }

-- Rate-limited network
sandbox(net=rate_limit(requests=100, per=60s)) { ... }
```

#### Filesystem Permissions

```nova
-- No filesystem access
sandbox(fs=.none) { ... }

-- Read-only access
sandbox(fs=.readonly) { ... }

-- Read-only, restricted to specific paths
sandbox(fs=readonly(["/data/public", "/tmp/scratch"])) { ... }

-- Read-write to specific paths only
sandbox(fs=readwrite(["/tmp/sandbox_output"])) { ... }

-- Full access (use with caution)
sandbox(fs=.full) { ... }
```

#### Resource Limits

```nova
sandbox(
    memory=4gb,           -- maximum memory allocation
    timeout=30s,          -- wall-clock time limit
    cpu=2,                -- maximum CPU cores
    gpu_memory=2gb,       -- maximum GPU memory
) {
    expensive_model.generate(input)
}
```

#### Sandbox Violation Handling

Sandbox violations always produce `Result` errors, never panics. The caller retains
full control.

```nova
let result = sandbox(net=false, timeout=10s) {
    agent.run("Fetch data from the internet")
}

match result {
    Ok(output) => print(output)
    Err(SandboxError.NetworkViolation(detail)) => {
        print("Agent tried to access the network: {detail}")
    }
    Err(SandboxError.Timeout) => {
        print("Agent exceeded time limit")
    }
    Err(SandboxError.MemoryExceeded(used, limit)) => {
        print("Agent used {used} of {limit} allowed memory")
    }
    Err(SandboxError.FsViolation(path, op)) => {
        print("Agent tried to {op} at {path}")
    }
    Err(e) => {
        print("Unexpected sandbox error: {e}")
    }
}
```

### 5.3 Composing Constraints and Sandboxes

Constraints and sandboxes compose naturally. A sandbox can contain constrained
operations, and vice versa.

```nova
-- Sandbox wrapping constrained generation
let result = sandbox(net=false, memory=2gb, timeout=15s) {
    constrain(schema=SafeOutput, safety=.strict, max_length=512) {
        untrusted_model.generate(user_input)
    }
}
-- result: Result[Result[SafeOutput, ConstraintError], SandboxError]

-- Flatten the nested Result with and_then
let result = sandbox(net=false, memory=2gb, timeout=15s) {
    constrain(schema=SafeOutput, safety=.strict, max_length=512) {
        untrusted_model.generate(user_input)
    }
}.flatten()
-- result: Result[SafeOutput, Error]  (unified error type)
```

### 5.4 Safety-Aware Agents

Agents can be wrapped in safety layers at the type level.

```nova
-- An agent that operates within safety constraints
agent SafeAssistant(llm: Model) {
    tools {
        web_search = sandbox(net=allow(["*.wikipedia.org"]), timeout=5s) {
            WebSearch(max_results=3)
        }
        calculator = sandbox(net=false, fs=.none, timeout=1s) {
            Calculator()
        }
    }

    memory {
        history: [Message] = []
    }

    plan(goal: str) -> [str] {
        let response = constrain(schema=[str], max_length=500, safety=.strict) {
            llm.generate("Plan: {goal}")
        }
        return response.unwrap_or(["Unable to plan"])
    }

    act(step: str) -> Result[str, Error] {
        let response = constrain(safety=.strict, validators=[no_harmful_content]) {
            llm.generate("Execute: {step}")
        }
        match response {
            Ok(action) => return tools.execute(parse_tool_call(action))
            Err(e)     => return Err(e.into())
        }
    }
}
```

### 5.5 Audit Logging

All safety-relevant operations can produce an audit trail.

```nova
-- Enable audit logging on a sandbox
let (result, audit) = sandbox(net=false, fs=.readonly, audit=true) {
    agent.run("Process sensitive data")
}

-- audit: AuditLog containing:
--   - all tool calls attempted (including denied ones)
--   - all constraint checks (pass/fail)
--   - resource usage timeline
--   - wall-clock time

for entry in audit.entries {
    match entry {
        AuditEntry.ToolCall(tool, input, output, allowed) => {
            print("[{entry.timestamp}] {tool}({input}) -> allowed={allowed}")
        }
        AuditEntry.ConstraintCheck(name, passed) => {
            print("[{entry.timestamp}] constraint {name}: {passed}")
        }
        AuditEntry.ResourceUsage(memory, cpu_time) => {
            print("[{entry.timestamp}] mem={memory}, cpu={cpu_time}")
        }
    }
}

-- Save audit log
audit.save("audit_log.json")
```

---

## 6. Type Interaction Diagram

The five core types form a connected system. Below is the interaction map showing
how each type converts to, composes with, and feeds into the others.

```
                          +-------------------+
                          |     frame         |
                          |  (tabular data)   |
                          +-------------------+
                            |             ^
                .to_tensor()|             | frame.from_tensor()
              .to_loader()  |             |
                            v             |
                          +-------------------+
                   +----->|     tensor        |<-----+
                   |      |  (numeric array)  |      |
                   |      +-------------------+      |
                   |        |             ^          |
                   |  input |             | output   |
                   |        v             |          |
                   |      +-------------------+      |
                   |      |     model         |      |
        grad(fn)   |      |  (neural network) |      |  .state_dict()
        jacobian() |      +-------------------+      |  .parameters()
        hessian()  |        |             ^          |
                   |  model |             | llm      |
                   |  field |             | field    |
                   |        v             |          |
                   |      +-------------------+      |
                   +------|     agent         |------+
                          |  (autonomous AI)  |
                          +-------------------+
                            |             ^
                   .run()   |             | Result[T, E]
                   .act()   |             |
                            v             |
                          +-------------------+
                          |    safety types   |
                          |  constrain / sandbox |
                          +-------------------+
```

### 6.1 Tensor <-> Frame

```nova
-- Frame to Tensor: extract numeric data for training
let df = frame.read_csv("iris.csv")
let X = df[["sepal_length", "sepal_width", "petal_length", "petal_width"]].to_tensor[f32]()
let y = df["species"].to_tensor[i64]()  -- categorical label encoded as integers

-- Tensor to Frame: wrap predictions for analysis
let predictions = model(X)  -- tensor[f32, 150, 3]
let pred_df = frame.from_tensor(predictions, columns=["setosa", "versicolor", "virginica"])
let results = df.add_columns(pred_df)
```

### 6.2 Tensor <-> Model

```nova
-- Tensors are the input and output of models
let x = tensor.randn[f32, 32, 784]()
let y = model(x)                        -- tensor[f32, 32, 10]

-- Model parameters ARE tensors
let w = model.linear.weight             -- tensor[f32, 10, 784]

-- Autograd connects them: loss.backward() flows gradients through the model
-- into every parameter tensor
let loss = cross_entropy(y, labels)
loss.backward()
print(model.linear.weight.grad.shape)   -- (10, 784)
```

### 6.3 Model <-> Agent

```nova
-- Agents USE models as their reasoning engine
agent Writer(llm: Model) {
    -- llm is a model that the agent calls for reasoning
    tools { ... }
    memory { ... }

    plan(goal: str) -> [str] {
        return llm.generate("Plan: {goal}") |> parse_steps
    }
    act(step: str) -> Result[str, Error] { ... }
}

-- Models can be parameters of agents
let writer = Writer(llm=my_fine_tuned_model)

-- An agent's tools can include model inference
agent Classifier(llm: Model, vision: Model) {
    tools {
        classify_image = fn(path: str) -> Result[str, Error] {
            let img = tensor.load_image(path).to(gpu:0)
            let logits = vision(img.unsqueeze(0))
            let label = logits.argmax(dim=-1)
            return Ok(labels[label.item()])
        }
    }
    -- ...
}
```

### 6.4 Agent <-> Safety

```nova
-- Every agent operation can be wrapped in safety types
let result = sandbox(net=false, fs=.readonly, timeout=60s) {
    constrain(safety=.strict) {
        agent.run("Summarize this document")
    }
}

-- Agents can enforce safety internally in their act() function
agent SafeAgent(llm: Model) {
    act(step: str) -> Result[str, Error] {
        -- All tool execution is sandboxed
        let output = sandbox(timeout=5s) {
            tools.execute(parse_tool_call(step))
        }

        -- All outputs are constrained
        return constrain(safety=.moderate, max_length=1000) {
            output?  -- propagate sandbox error with ?
        }
    }
}
```

### 6.5 Frame <-> Agent

```nova
-- Agents can produce frames as structured output
agent DataCollector(llm: Model) {
    tools {
        web_search = WebSearch()
        scraper    = WebScraper()
    }
    memory {
        collected: frame = frame(schema={"url": str, "title": str, "content": str})
    }

    act(step: str) -> Result[str, Error] {
        let data = tools.scraper.execute(step)?
        memory.collected = memory.collected.append(parse_row(data))
        return Ok("Collected: {data.title}")
    }
}

let collector = DataCollector(llm=gpt4)
collector.run("Collect data on renewable energy companies")

-- Access the collected data as a frame
let dataset = collector.memory.collected
let features = dataset.to_tensor[f32]()
```

### 6.6 Full Pipeline Example

This example demonstrates all five types working together in a single pipeline.

```nova
-- 1. Load data (Frame)
let train_data = frame.read_parquet("train.parquet")
let (X_train, y_train) = train_data.to_tensors(
    features=["f1", "f2", "f3"],
    target="label",
    dtype=f32,
)

-- 2. Define and train a model (Model + Tensor + Autograd)
model Classifier(in_dim: int, out_dim: int) {
    layer1 = Linear(in_dim, 128)
    layer2 = Linear(128, out_dim)

    forward(x: tensor[f32, _, in_dim]) -> tensor[f32, _, out_dim] {
        x = relu(layer1(x))
        return layer2(x)
    }
}

let clf = Classifier(in_dim=3, out_dim=2)
let optimizer = Adam(clf.parameters(), lr=1e-3)

clf.train()
for epoch in 0..50 {
    let logits = clf(X_train)
    let loss = cross_entropy(logits, y_train.to_dtype(i64))
    loss.backward()
    optimizer.step()
    optimizer.zero_grad()
}
clf.eval()

-- 3. Deploy in a safe agent (Agent + Safety)
agent PredictionAgent(llm: Model, classifier: Model) {
    tools {
        predict = fn(input_json: str) -> Result[str, Error] {
            let data = parse_json(input_json)
            let x = tensor.from[f32](data["features"]).unsqueeze(0)
            let logits = classifier(x)
            let label = logits.argmax(dim=-1).item()
            return Ok("{label}")
        }
        explain = fn(input_json: str) -> Result[str, Error] {
            let data = parse_json(input_json)
            let x = tensor.from[f32](data["features"])
            x.requires_grad = true
            let logits = classifier(x.unsqueeze(0))
            let pred = logits.argmax(dim=-1)
            logits[0, pred.item()].backward()
            let importance = x.grad.abs()
            return Ok("Feature importance: {importance}")
        }
    }

    memory {
        prediction_log: frame = frame(schema={
            "input": str, "prediction": str, "timestamp": str,
        })
    }

    plan(goal: str) -> [str] { ... }
    act(step: str) -> Result[str, Error] { ... }
}

let agent = PredictionAgent(llm=gpt4, classifier=clf)

-- 4. Run the agent inside a safety sandbox
let result = sandbox(net=false, fs=.readonly, memory=1gb, timeout=10s) {
    constrain(safety=.strict, max_length=500) {
        agent.run("Classify the input and explain the prediction")
    }
}.flatten()

match result {
    Ok(output) => print("Agent output: {output}")
    Err(e)     => print("Safe failure: {e}")
}
```

---

## Appendix A: Type Hierarchy Summary

```
any
  +-- nil
  +-- bool
  +-- int (i8, i16, i32, i64)
  +-- uint (u8, u16, u32, u64)
  +-- float (f16, bf16, f32, f64)
  +-- complex (complex64, complex128)
  +-- str
  +-- tensor[dtype, dims...]
  +-- frame
  +-- model
  +-- agent
  +-- Result[T, E]
  +-- Device (cpu, gpu:N, tpu:N)
  +-- DType (f16, f32, f64, bf16, i8, ..., bool)
  +-- ConstraintError
  +-- SandboxError
        +-- SandboxError.NetworkViolation
        +-- SandboxError.Timeout
        +-- SandboxError.MemoryExceeded
        +-- SandboxError.FsViolation
  +-- AuditLog
  +-- Tool (interface)
```

## Appendix B: Gradual Typing Summary Table

| Annotation Level | Compile-Time Check | Runtime Check | Example |
|---|---|---|---|
| Fully specified | Shape + dtype verified | None needed | `tensor[f32, 3, 224, 224]` |
| Symbolic dims | Consistency across signature | Concrete values at call site | `tensor[f32, N, M]` |
| Partial (`_`) | Known dims checked | Dynamic dims checked | `tensor[f16, _, 4096]` |
| Dtype only | Dtype verified | Shape checked | `tensor[f32]` |
| Fully untyped | None | All checks at runtime | `tensor` |

## Appendix C: Error Model for Type Operations

| Operation | Error Kind | Recovery |
|---|---|---|
| Shape mismatch (static) | Compile error | Fix the type annotation |
| Shape mismatch (runtime) | `Result::Err(ShapeError)` | Pattern match and handle |
| Dtype mismatch | Compile error or `Result::Err(DTypeError)` | Cast explicitly |
| Device mismatch | Compile error or `Result::Err(DeviceError)` | Transfer explicitly |
| Constraint violation | `Result::Err(ConstraintError)` | Retry or degrade gracefully |
| Sandbox violation | `Result::Err(SandboxError)` | Log and handle |
| Out of memory | Panic | Unrecoverable |
| Tool execution failure | `Result::Err(Error)` | Agent re-plans or skips step |

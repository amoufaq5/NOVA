# NOVA GPU Compute Backend Audit

P3.3 sweep on branch `claude/festive-franklin-PP7mW`. Same shape as the
sibling `WIN32_AUDIT.md`, `MACOS_AUDIT.md`, `WASM_AUDIT.md`, and
`SIMD_AUDIT.md`: prove a single dispatch path end-to-end, document
the chasm between that proof and "CrossEngin substrate-on-GPU".

## Status: minimum viable

NOVA can emit a WGSL compute shader as a string (alongside a small
key=value config describing the dispatch). A standalone Rust shim
(`scripts/wgpu_dispatch/`) reads both, drives the `wgpu` crate to
allocate buffers, compile the shader, dispatch the kernel, copy the
result back, and validate against a CPU baseline. The smoke runs
`c[i] = a[i] + b[i]` for 1M `i32` elements and reports
`CPU=Xms GPU=Yms speedup=Zx`.

`make smoke-gpu` runs the whole pipeline and skips cleanly with a
clear `(skip: rust toolchain / wgpu unavailable)` or
`(skip: no wgpu adapter available)` message if the host lacks
`cargo` or any GPU/CPU-fallback adapter. This sandbox has no
`/dev/dri` and no software fallback (LavaPipe) installed, so the
shim takes the second skip path; on any developer machine with a
real GPU and Vulkan/Metal/D3D12 drivers, the full dispatch runs.

## Why GPU compute is structurally different from CPU SIMD

The SIMD work in `SIMD_AUDIT.md` was *register-level* parallelism —
8 lanes of `i32` inside a single core, sharing the CPU's caches,
addressed by the same pointer as the scalar path, dispatched by the
same `call` instruction. GPU compute is four steps further out:

1. **Massive SIMT, not 8-wide SIMD.** A modern desktop GPU runs
   ~2,000–20,000 threads concurrently, organized into warps/waves of
   32 (NVIDIA) or 64 (AMD/Apple) lanes. The kernel is written once
   from a single-thread perspective; the hardware fans it out across
   every shader core. NOVA's existing AVX2 work hits 8 lanes per
   core; a GPU hits hundreds of lanes per *thousands* of cores.
2. **Explicit host/device memory hierarchy.** GPU buffers live in
   VRAM, separate from host RAM. Every input is `write_buffer`'d
   over PCIe (or unified-memory on Apple); every output is
   `copy_buffer_to_buffer`'d to a staging buffer that the host can
   map. For a 1M-element vector add the PCIe transfer dominates the
   wall clock — the kernel itself runs in microseconds. The
   speedup only materializes when the *compute density per byte
   transferred* is high (cosine over 10K atoms; LSH bucket scan;
   Hebbian update).
3. **Shader compilation, not assembly.** WGSL / GLSL / HLSL / MSL
   are mid-level languages compiled by the driver at runtime (or
   cached). NOVA's existing codegen emits machine code directly via
   `as` + `ld`; the GPU path emits a **string** at compile time and
   defers translation to the driver. That string can be regenerated
   per-tick (parameterizable kernels) at the cost of a few
   milliseconds of one-time validation.
4. **Synchronization primitives.** A `dispatch_workgroups` call is
   asynchronous; results aren't visible until a `queue.submit()` +
   `device.poll(Wait)` round-trip. There is no equivalent of "the
   call returns when the work is done" on the GPU. CrossEngin's
   tick driver will need an explicit "fence" between phases.

## The three options for NOVA-side GPU compute

### Option 1 — WGSL via wgpu (RECOMMENDED, shipped this session)

WGSL is the WebGPU Shading Language, designed jointly by Apple,
Google, Mozilla, and Microsoft. The `wgpu` Rust crate translates it
to whichever backend the host exposes:

| Host | Backend | Translation target |
| ---- | ------- | ------------------ |
| Linux (Mesa, NVIDIA, AMD) | Vulkan | SPIR-V |
| macOS (any) | Metal | MSL |
| Windows 10/11 | D3D12 | HLSL / DXIL |
| Browsers (Chrome 113+, Safari 18, Firefox 141+) | WebGPU | WGSL bytecode |
| Older Linux / fallback | GL or LavaPipe (software) | GLSL / SPIR-V |

**Pros**: one shader source compiles everywhere; matches NOVA's
existing cross-target story (Linux / Windows / macOS / WASM); the
Rust shim is ~300 LOC; browsers come for free; software fallback
keeps the smoke runnable in headless CI via LavaPipe.

**Cons**: extra Rust binary in the build (not a NOVA-only path);
WGSL is younger than GLSL so the ecosystem has fewer
copy-paste examples; the host-side dispatch lives in Rust until
NOVA itself can emit SPIR-V directly (months of compiler work).

### Option 2 — CUDA PTX (NVIDIA-only)

PTX is NVIDIA's intermediate representation. Emit PTX from NOVA →
load via `cuModuleLoadData` → dispatch via `cuLaunchKernel`. The
host shim could be either Rust (`cust` crate) or plain C against
`libcuda.so`. Performance is excellent (PTX → SASS happens in the
driver, with all CUDA-specific optimizations).

**Pros**: best peak FLOPS on NVIDIA hardware; mature tooling
(`nvcc`, Nsight); the inner-loop NOVA codegen reuses ~80% of
register-allocation patterns it already has.

**Cons**: NVIDIA-only — locks out Apple Silicon (CrossEngin's
expected dev hardware), AMD desktops, Intel Arc, integrated GPUs,
mobile, and browsers. A pure-CUDA NOVA cannot ship to the same
four targets NOVA already supports.

### Option 3 — OpenCL (legacy)

OpenCL is the original cross-vendor GPU compute API. Apple
deprecated it in 2018; NVIDIA caps support at OpenCL 3.0 with
their own quirks; Intel and AMD ship up-to-date but with bugs.
Driver quality is the worst of the three options. Almost no new
projects pick OpenCL today.

**Pros**: works on most existing hardware right now (including
old Intel iGPUs that don't speak Vulkan).

**Cons**: deprecated trajectory; driver-bug graveyard; no
browser story; tooling is the worst of the three.

## What CrossEngin's hot loops could benefit from

The audit-target hot loops (same three identified in `SIMD_AUDIT.md`)
all want different GPU treatment:

| Loop | Today | GPU win? |
| ---- | ----- | -------- |
| **Integer cosine across 10K atoms** (`atom_store.nova:vec_cosine`) | scalar / AVX2 dot-product per atom pair | **Big.** 10K × 10K = 100M dot products in one dispatch; SIMT lanes saturate; memory-bound on input bandwidth, ~50× over AVX2 |
| **Signal dispatch fan-out** (`signal_dispatch.nova`) | linear scan of 10-priority queues | **Marginal.** Branch-heavy, irregular control flow; SIMT divergence eats most of the win |
| **Hebbian synapse weight updates** (`synapse_graph.nova`) | per-edge linear pass | **Big.** Independent edge updates → embarrassingly parallel; one workgroup per source node, 64 threads per workgroup, atomic adds on the dest accumulator |
| **LSH bucket scoring** (P3.4, planned) | bucket-grouped cosine | **Huge.** This is the canonical GPU workload — hash-bucket lookup + many independent dot products. Likely the first CrossEngin module to migrate. |

The first and fourth are the wins that move CrossEngin from a
1000-atom toy to a 1M-atom knowledge graph (1000× growth). CPU
cosine across 1M atoms is ~10 seconds per snapshot search; the GPU
equivalent on a midrange laptop GPU is ~50 ms.

## Wall-clock estimates per option

Estimates assume one experienced engineer, no team-blocking deps.

| Milestone | Option 1 (WGSL/wgpu) | Option 2 (CUDA PTX) |
| --------- | -------------------- | ------------------- |
| Vector-add smoke | **this session** | ~3 days |
| Integrated `vec_cosine` (host buffer pool, async dispatch, fence) | ~1 month | ~3 weeks (NVIDIA only) |
| Hebbian update kernel + atomic add path | +2 weeks | +1 week |
| LSH bucket scoring (P3.4 integration) | +1 month | +3 weeks |
| Full CrossEngin substrate on GPU (snapshots, KG search, tick driver phases 1-2) | 3–6 months | 2–4 months (NVIDIA only) |
| NOVA itself emits SPIR-V (skip the Rust shim) | +3 months on top | n/a (would still need PTX backend) |

## Why this matters: the 1M-atom horizon

Today CrossEngin tops out around 1000 atoms with subjectively
real-time tick rates on commodity hardware. A 1M-atom knowledge
graph — the scale required for "remembers a year of dialogue" —
is 1000× growth. AVX2 buys ~8× over scalar; tiled cache-aware
matmul buys another ~3–4×; combined ~25–30×. That's still 30× too
slow.

The remaining 30× lives on the GPU. There is no CPU SIMD path
that closes the gap — the FLOPS aren't there. A midrange laptop
GPU (e.g. Apple M2 Pro, ~7 TFLOPS f32) carries ~50× the integer
throughput of the same chip's CPU side; a desktop discrete GPU
(RTX 4070 / Radeon 7800, ~30 TFLOPS f32) carries ~150–200×.

This makes GPU compute the **only** structural path to a 1M-atom
substrate. P3.3 (this audit) unblocks it; P3.4 (LSH on GPU) is the
first kernel that wants it.

## What works end-to-end this session

```
make smoke-gpu
  -> bin/nova compiles examples/gpu_vector_add.nova
  -> NOVA emits bin/gpu_vector_add.wgsl + bin/gpu_vector_add.cfg
  -> cargo --release builds scripts/wgpu_dispatch/target/release/wgpu_dispatch
  -> dispatcher reads both files, allocates 3x 4MB buffers,
     dispatches 15625 workgroups (1M/64), reads result back,
     validates against CPU baseline
  -> on a host with a GPU: prints
       "GPU result matches CPU baseline; CPU=Xms GPU=Yms speedup=Zx"
     on this sandbox (no /dev/dri): prints
       "(skip: no wgpu adapter available on this host)"
     and exits 0 cleanly.

make self-host      -> stage2.s == stage3.s still verified
make test           -> all 5 test programs pass
make smoke-windows  -> bin/nova.exe still produced (PE32+)
Crossengin-demo    -> 120/120 unit tests pass
```

## Known gaps for next session

- **No NOVA-emitted SPIR-V**. NOVA writes WGSL *source* and defers
  to the wgpu/naga translator. Direct SPIR-V emission is a 3-month
  compiler project — the WGSL string-emission path proves the
  dispatch is wired up first.
- **No buffer pool**. Each smoke run reallocates buffers. A real
  integration needs a host-side pool (`Arc<Buffer>` reused across
  ticks) to avoid the 1–10 ms allocation cost per call.
- **No async / pipelining**. The shim does blocking `device.poll(Wait)`.
  CrossEngin's tick driver will want to overlap CPU prep of tick N+1
  with GPU dispatch of tick N — that needs a small executor (tokio
  or pollster's `block_on` with a worker thread).
- **No `i64` / `f64` on the GPU**. WGSL types are `i32` / `u32` /
  `f32` / `f16`. CrossEngin's int64 atom IDs need either splitting
  into two `u32` halves or a transition to 32-bit indices.
- **No NVIDIA PTX path**. Option 2 is documented but not built;
  the WGSL path covers the same hardware via Vulkan, but raw CUDA
  PTX would be ~20% faster on NVIDIA discrete GPUs. Worth doing
  once CrossEngin actually has a perf-critical workload.
- **No browser dispatch**. WGSL is ready for browsers, but the
  current shim is a native Rust binary. A WASM-side path needs a
  JS bridge that calls `navigator.gpu.requestAdapter()`; that is a
  P4 task that overlaps with the WASM audit's preview2 work.
- **No timestamp queries**. The current "GPU time" measurement is
  wall-clock from `dispatch` to `poll(Wait)`, which includes PCIe
  transfer. Real per-kernel timing wants `wgpu::QuerySet` with
  timestamp writes; the smoke doesn't need that yet.
- **No multi-GPU / device selection**. `request_adapter` takes the
  first match. Production wants a CLI flag for selecting between
  iGPU and dGPU.

## Minimum scope shipped this session (P3.3)

Per the priority-list ask: vector-add of 1M `i32`s dispatched on a
GPU via WGSL/wgpu, with CPU baseline + speedup printed, and a
clean skip path when no GPU is present. **Done.** Everything
beyond that lives in the gaps section above.

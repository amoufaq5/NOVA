# NOVA Compatibility & Breaking-Change Log

Every BREAKING change in NOVA's public surface gets an entry here
with date, commit SHA, what broke, why, and a migration step.
"Public surface" = language syntax, builtin names + arities + return
shapes, runtime `_nova_*` ABI, `src/runtime/*.nova` signatures and
value layouts, and target-flag spellings. Internal compiler refactors
invisible to programs do NOT count.

## Versioning policy (effective 2026-05-30, this commit)

`VERSION` at repo root tracks semver:

- **MAJOR** — language syntax break, builtin removal, runtime ABI
  break, breaking stdlib change.
- **MINOR** — new builtin, new target, new stdlib module, additive
  language feature.
- **PATCH** — bug fix, performance, internal refactor, docs.

Current line in the sand: **0.1.0** at commit `ac692f7`. Future
breaking changes append here AND bump MAJOR.

## Breaking changes in the past 100 commits

The repo has 63 total commits; the window covers full history. **One**
rises to "breaking"; the rest are additive or internal.

### 1. `str_new` format reconciled — 2026-05-29, `56322bb`

- **What broke:** `src/runtime/string.nova`'s `str_new`, `str_len`,
  `str_data`, `str_char_at` and every helper touching raw string
  memory switched FROM `[len:i64][data...]` (8-byte length prefix at
  offset 0) TO `[data...][\0]` (NUL-terminated at offset 0).
- **Why:** the native `len()` builtin (`_nova_len`) scans bytes
  until 0. The prefix format disagreed — for any `len < 256`, the
  second byte of the little-endian length is 0, so
  `len(str_new(buf, 11))` returned 1. `io_input()` was returning a
  1-byte string regardless of how many bytes `sys_read` delivered.
- **Migration:**
  - Passing `str_new` to native operators (`len`, `+`, `println`):
    no source change — code now works where it silently misreported
    length before.
  - Manually reading bytes assuming a length prefix: drop it, read
    from offset 0; byte at offset `len` is now `\0`.
- **Compat shim:** none today. The old layout was unreachable via
  the public language because `len(str_new(...))` was already
  broken. A future `compat-0.1.nova` could expose a `str_new_pfx`
  for prefix-layout callers; no such caller currently exists.

## Additive (non-breaking) — same window

| Commit | Date | Change |
| ------ | ---- | ------ |
| `ac692f7` | 2026-05-30 | 10 new `int_*` builtins (PTR_THRESHOLD escape hatch) |
| `a84f0bf` | 2026-05-30 | Mobile audit + ARM64 reference smoke targets |
| `8fa5130` | 2026-05-30 | GPU compute audit + WGSL smoke |
| `e8d27a5` | 2026-05-30 | WASM hello-world via WASI preview1 |
| `b543be3` | 2026-05-30 | New `__intrinsic_dot_i32` AVX2 builtin |
| `72e929d` | 2026-05-30 | macOS hello-world via Mach-O + Darwin syscalls |
| `0a97f24` | 2026-05-29 | Win32 finish: argv, envp, sockets, fork, exec, pipe, waitpid |
| `7356959` | 2026-05-29 | Runtime: `sys_fsync`, `sys_rename`, `nanotime` |
| `d0e2f44` | 2026-05-26 | Import-path canonicalization (resolver-only) |
| `f98c8d7` | 2026-05-25 | 7th-parameter calling convention fix |
| `1acd9a8` | 2026-05-25 | Hash-accelerated symbol tables (internal) |
| `accad2f` | 2026-05-25 | N12-N29 cognitive/runtime modules (additive) |
| `73795eb` | 2026-05-24 | N1-N11 cognitive primitives (additive) |
| `a108b79` | 2026-05-24 | v4.2: IEEE 754, syscall FFI, ARM64 codegen (partial) |
| `e521870` | 2026-05-23 | Cognitive overhaul — additive new modules |

The remaining ~40 commits added syntax features (match expressions,
`do` expressions, range step, type-based match, flow operators, etc.);
none removed or renamed anything pre-existing.

## Why "almost-zero recent breaks" matters for 0.1.0

The 0.1.0 line is being drawn here precisely because the only
breaking change in visible history was a silent format reconciliation
that fixed a bug. NOVA has been de-facto stable for the past 100
commits even without a formal semver promise. The point of this file
is to make the promise explicit: **from commit `ac692f7` onward,
breaking changes will bump MAJOR and document here with a migration;
additive changes bump MINOR; fixes and refactors bump PATCH.**

## Future-breaking changes already on the roadmap

Known-breaking changes targeted at 1.0.0, listed now so downstream
users can plan:

1. **Tagged-value codegen** (closes PTR_THRESHOLD bug class).
   Integers become `(i << 1) | 1`, pointers stay 8-byte aligned with
   bit 0 = 0. `_nova_*` helpers mask the tag in scalar paths.
   Migration: recompile against the new ABI; pure NOVA programs need
   no source change. Design: `NOVA_BUG_THRESHOLD.md` Future-Work
   Option 1.

2. **ARM64 target split.** Current single `cg_target == 4` will
   split into Linux-arm64, Darwin-arm64, iOS-arm64, Android-arm64
   once `la_lower_function` integrates. Default `--target=arm64`
   could remap to `--target=arm64-linux` to preserve spelling.

Both will ship as a single coordinated 1.0.0 release.

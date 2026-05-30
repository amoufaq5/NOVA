# NOVA Stability Audit

Snapshot at commit `ac692f7` on `claude/festive-franklin-PP7mW`,
2026-05-30. This is the audit accompanying the move from "Nova v4.2"
marketing-style versioning to a real semver `0.1.0` line in the sand
(see `VERSION`, `COMPAT.md`).

The premise: NOVA has been a moving target during CrossEngin P0-P3.
Six independent agents hit the same codegen pointer-threshold bug.
Three of five declared backends are partially complete. This document
inventories every dimension of drift, ranks each by impact and effort,
and proposes a concrete first-`1.0` roadmap.

## 1. Language syntax — STABLE

`src/compiler/parser.nova` (2305 lines, 59 fn) and
`src/compiler/lexer.nova` (867 lines) have not been touched in the
last ~30 commits. Most recent syntax-affecting commits are weeks old:
`d52470d` (v3.0 novel syntax), `91cf5b9` (map iteration, `break if`,
`typename`), `5a3918e` (flow operators `~>` etc.), `1c9bf76`
(`is int` match patterns), `9ee83ba` (rest patterns, map
comprehension, `enumerate`, `unless`), `5346cfa` (`match`/`if` as
expressions), `978a22d` (range step + inclusive), `342118d` (`do`
blocks). **Verdict:** syntax has been stable for the lifetime of the
CrossEngin port. No keyword renames, no removals, no breaking grammar
changes.

## 2. Builtin function set — 152 names, partial contract

`grep 'if name == ' src/compiler/codegen.nova | wc -l` reports 154;
deduplicating gives **152 unique builtin names** (plus the `"main"`
sentinel and `"0"` boundary). Categories:

- Core IO (10): `print`, `println`, `print_int`, `read_line`,
  `read_stdin`, `read_file`, `write_file`, `file_size`, `mkdir`,
  `unlink`.
- Strings (27): `concat`, `substr`, `chr`, `char_at`, `char_code`,
  `int_to_str`, `str_to_int`, `str_eq`, `str_find`, `starts_with`,
  `ends_with`, `str_upper`, `str_lower`, `str_trim`, `str_repeat`,
  `str_replace`, `str_count`, `pad_left`, `pad_right`, `strcmp`,
  `chars`, `split`, `join`, `to_str`, `to_int`, `hex`, plus boundary.
- Lists (22): `list_new`, `push`, `pop`, `len`, `list_set`,
  `contains`, `list_remove`, `list_copy`, `list_slice`, `append_list`,
  `index_of`, `last_index_of`, `sort`, `reverse`, `range_list`,
  `range_step`, `range`, `enumerate`, `zip`, `flat_map`, `flatten`,
  `unique`.
- Maps (9): `map_new`, `map_set`, `map_get`, `map_has`, `map_remove`,
  `map_count`, `map_merge`, `keys`, `values`.
- Higher-order (10): `map_list`, `filter`, `reduce`, `foreach`, `any`,
  `all`, `sum`, `product`, `min_list`, `max_list`.
- Math + scalar int (13): `abs`, `min`, `max`, plus `int_add`,
  `int_sub`, `int_mul`, `int_div`, `int_mod`, `int_shl`, `int_shr`,
  `int_and`, `int_or`, `int_xor` (the 10 added 2026-05-30 by
  `ac692f7` as escape hatches around `PTR_THRESHOLD`).
- Floats (9): `float_add/sub/mul/div`, `float_cmp`, `float_to_str`,
  `to_float`, `from_float`, `fsqrt`.
- Runtime + introspection (13): `assert`, `type_of`, `typename`,
  `debug_print`, `time`, `sleep_ms`, `alloc`, `getenv`, `random`,
  `random_seed`, `get_error`, `__arg`, `exit`.
- Sockets (9): `socket`, `bind_socket`, `listen_socket`,
  `accept_conn`, `connect_socket`, `send_data`, `recv_data`,
  `make_sockaddr_in`, `close_fd`.
- Processes (5): `fork_process`, `waitpid`, `exec_program`,
  `pipe_create`, `system_exec`.
- FFI (13): `ffi_open`, `ffi_sym`, `ffi_close`, `ffi_call0`..`ffi_call5`,
  `ffi_calln`, `ffi_callf1`..`ffi_callf4`.
- Coroutines (6): `coro_new`, `coro_resume`, `coro_yield`,
  `coro_done`, `coro_result`, `coro_state`.
- Raw memory (5): `store64`, `load64`, `store8`, `load8`, `memcpy_raw`.
- Intrinsics (1): `__intrinsic_dot_i32` (Linux-x86-64 only).

**Recent additions (NOT breaking):** `int_*` (10 fns, ac692f7),
`__intrinsic_dot_i32` (b543be3), `sum` and `product` (c0de38c,
75aaee5), `pad_left/pad_right/str_count` (e74c663), `enumerate`
(9ee83ba), `typename` (91cf5b9). **No builtin has been removed or had
its signature changed in any of the 63 commits** that comprise the
repo's full history. **Gap:** there is no machine-readable arity /
return-type contract. A future `BUILTINS.md` table would lock the
152-fn surface as an explicit promise; this audit names the gap.

## 3. Codegen target ABIs — five declared, two production-ready

`cg_target` enum: `0=linux, 1=macos, 2=wasm, 3=windows, 4=arm64`.

| Target  | Status        | Verified                              | Gaps                                                  |
| ------- | ------------- | ------------------------------------- | ----------------------------------------------------- |
| Linux x86-64 | PRODUCTION | `make self-host`, `make test`, CrossEngin 121/121 | none |
| Windows x86-64 | PRODUCTION (Wine-only) | smoke + kg-sync two-process | `setsid`/signal stubs return -1; `WSACleanup` never called; fork re-spawns exe |
| macOS x86-64 | MINIMUM VIABLE | smoke produces Mach-O | no real-Darwin run; sockets/fork untested |
| WASM (WASI preview1) | HELLO-WORLD | node WASI | file I/O stubbed; no SIMD; FFI N/A |
| ARM64 (AArch64) | STUBBED | reference .o smoke | `gen_program` emits 7-instr exit stub; TODO at `codegen.nova:3211` |

`la_lower_function` and `la_lower_inst` exist (lower_arm64.nova) but
`gen_program` short-circuits to an exit stub.

## 4. Runtime helpers — 167 `_nova_*` symbols, undocumented ABI

`grep -oE '_nova_[a-zA-Z_0-9]+' src/compiler/codegen.nova | sort -u`
gives **167 distinct `_nova_*` symbols** in three classes:
**builtin implementations** (~110, register-passed in
`rdi,rsi,rdx,rcx,r8,r9`, returns in `rax`); **smart-op dispatch
helpers** (8: `_nova_add`, `_nova_mul`, `_nova_lt`, `_nova_gt`,
`_nova_le`, `_nova_ge`, `_nova_eq`, `_nova_neq` — the PTR_THRESHOLD
surface); and **state globals** (~50: `_nova_catch_stack`,
`_nova_error_flag/value`, `_nova_coro_*`, `_nova_flow_*`,
`_nova_win_init_wsa`). None are documented today. A future
`RUNTIME_ABI.md` listing each symbol, signature, and target
availability would lock the contract.

## 5. Standard library — STABLE except one recent semantic shift

40+ runtime modules. Counts: `string.nova` 16, `list.nova` 15,
`map.nova` 11, `io.nova` 20, `math.nova` 35, `syscall.nova` 15,
`json.nova` 15. Recent runtime commits:

- `7356959` (2026-05-29): **additive only** — `sys_fsync`,
  `sys_rename`, `nanotime`.
- `56322bb` (2026-05-29): **SILENT FORMAT CHANGE** — `str_new` and
  helpers switched from length-prefixed (`[len:i64][data...]`) to
  NUL-terminated (`[data...][\0]`). This is the **one breaking change**
  in stdlib in the visible history; see `COMPAT.md` for migration.

## 6. Self-hosting bootstrap — STABLE

`boot/nova_boot.s` (106,045 lines x86-64 assembly) has been touched
**twice** in the entire repo: `91cf5b9` (2026-05-20) and `438e798`
(v4.0, 2026-05-23). NOT regenerated during P0-P3, including the
recent `ac692f7` `int_*` add. Healthy. **Risk:** if `codegen.nova`
ever emits something the bootstrap can't understand, the chain breaks
— mitigation today is the `make self-host` check before every
codegen commit.

## 7. PTR_THRESHOLD bug — 6 incidents closed, root cause OPEN

See `NOVA_BUG_THRESHOLD.md`. The smart-op helpers dispatch on
`cmp reg, 0x100000` (1 MiB); both-above = string concat/list repeat;
both-below = integer arithmetic. Six known CrossEngin incidents
(P0.6 pacer, P0 win32 IP parser, P1.4 HTTP port, P2.3 SIMD bench,
P2.6 Klatt LCG, P3.6 DP LCG) closed by `int_*` builtins in ac692f7.

**Root cause still live.** Any future `a * b` with values above the
threshold hits it again. The fix is **Option 1 (tagged values)** in
`NOVA_BUG_THRESHOLD.md`: shift-and-tag (bit 0 = 1 int, 0 ptr),
dispatch on tag instead of magnitude. **2-3 weeks** of compiler work
+ bootstrap regen + full test sweep. Single biggest semver-MAJOR
change on the roadmap.

## 8. Known incompleteness

| Item | Status | Source | Effort |
| ---- | ------ | ------ | ------ |
| ARM64 `la_lower_function` integration | `gen_program` stub | `codegen.nova:3211`, `MOBILE_AUDIT.md` | 4-6 wk (P3.8) |
| ARM64 4-target split (Linux/Darwin/iOS/Android) | not started | `MOBILE_AUDIT.md` | (included) |
| WASI file I/O (`fd_read`, `path_open`, `fd_close`) | runtime no-op | `WASM_AUDIT.md` | 1-2 wk (P2.7) |
| WASM SIMD v128 lowering | scalar fallback | `WASM_AUDIT.md` | 2-3 wk |
| Win32 `setsid` / signal stubs | return -1 | `WIN32_AUDIT.md` | 1-2 wk |
| Win32 `WSACleanup` never called | OS reclaims | `WIN32_AUDIT.md` | low |
| Win32 real-hardware run | only Wine | `WIN32_AUDIT.md` | 2-4 days |
| macOS real-Darwin run | `file`/`llvm-objdump` only | `MACOS_AUDIT.md` | 1 day |
| Unverified syscall paths | mapped but untested | `syscall_num()` | 1-2 wk |
| Bootstrap robustness | segfaults on subtle codegen bug | by-design | n/a |
| FFI on WASM | silently emits ELF-shaped output | `WASM_AUDIT.md` | 1 wk |
| `tools/` (lsp, dap, vscode, Formula) | untracked | `git status` | 1 day |

## Roadmap for first 1.0 — ranked by `impact × (1/effort)`

1. **Tagged-value codegen** (PTR_THRESHOLD root fix). 2-3 wk.
   **Semver MAJOR.**
2. **BUILTINS.md contract.** 2-3 days. Locks the 152-fn surface.
3. **RUNTIME_ABI.md.** 2-3 days. Locks 167 `_nova_*` symbols.
4. **ARM64 `la_lower_function` integration.** 4-6 wk. Adds 5th real
   target. **Semver MINOR.**
5. **WASI file I/O completion.** 1-2 wk.
6. **macOS hardware verification + Darwin stat/sockets.** 1-2 wk.
7. **Win32 real-hardware verification.** 2-4 days.
8. **Unit tests for unverified syscall paths.** 1-2 wk.
9. **Compat shim framework (`compat-0.1.nova`).** Framework only;
   populate on first breaking 1.0 → 1.1.

Cumulative estimate to defensible 1.0: **3-4 months** of focused work
on the top 4 items.

# ADR 0010: Two-tier tag-polymorphism contract for runtime builtins

## Status

Proposed (2026-10-31). Formalizes the design that landed informally
across the Crossengin latent-triage arc R43–R59
(`claude/zealous-keller-NTz2n` + `claude/nova-language-design-dZn3q`
NOVA commits `f2fbbe6`, `ffeaac9`, `333394f`, `013ca17`, `57cbfac`,
`d0c974a`, `a6e9100`, plus the three stream_stdin asm shims landed in
Crossengin R49/R58/R59) and documented informally in
`/home/user/Crossengin-demo/docs/UPSTREAM_NOVA_BUGS.md` §17/§17b/§18.
Supersedes nothing; coexists with `docs/PTR_TAGGING_PLAN.md` as the
authoritative design surface for runtime-fn boundaries.

## Context

NOVA tags user-visible integers and some pointers so a single register
can hold both: a value `V` with bit 0 = 1 is a tagged NOVA value
representing `(V - 1) / 2`; bit 0 = 0 is a raw register value (a heap
pointer, a syscall return, a stack address, an argv/envp string).
This convention makes the compiler's arithmetic uniform at the cost of
every runtime builtin that handles a mixed-origin argument needing to
decide what the bit means.

Three forces interact:

1. **`alloc()` returns tagged.** The user-facing wrapper at
   `codegen.nova:22094` ends with `lea rax, [rax + rax + 1]`. Any
   `alloc(n)`-derived buffer therefore enters runtime fns with bit 0 =
   1 even though the real heap address is 8-byte aligned (bit 0 = 0 if
   you untag).
2. **Compiler-internal callers pass raw pointers.** `_nova_int_to_str`
   at `codegen.nova:16515` passes `[rbp-1]` (a stack address) directly
   to `_nova_strlen`. `_nova_concat` passes `inc rdi` pointers mid-walk.
   Both are RAW. A naive `test rdi, 1 / sar rdi, 1` prolog on
   `_nova_strlen` halves these pointers into unmapped memory. R46's
   broader sweep of this pattern broke self-host for exactly this
   reason.
3. **Raw pointers aren't always even.** String literals in `.rodata`
   are `.align 2`-emitted so their addresses have bit 0 = 0 and can
   coexist with the alloc'd-is-tagged convention. **Kernel-pushed
   strings — `argv[i]`, `envp[i]` — have no such alignment
   guarantee**; a `/root` env string can land at `0x7FFF_FFFF_C617`
   (bit 0 = 1) and look identical in a register to a tagged NOVA
   value. R55 observed this empirically in `test_p256_keypair_load`
   via `len(getenv("HOME"))`.

Prior to R43, the handling was ad-hoc: some runtime fns untagged, some
didn't; some callers pre-untagged, some didn't; some assumed `alloc`
returned raw. The resulting latent-SEGV class spanned at least
face_recognize, kg_rss_ingest, nat_traversal, p256_keypair_load,
truststore_dir, and the stream_stdin pipeline — all surfaced during
R43–R59, all traceable to a convention mismatch between a caller and
a callee.

## Decision

Adopt a **two-tier tag-polymorphism contract**:

### Tier 1 — Internal `_nova_*` fns are raw-pointer-only

Every `fn _nova_<name>` emitted inside `codegen.nova`'s `gen_runtime()`
assumes its pointer args have **bit 0 = 0**. It does not test, does
not untag, does not tag-poly. It dereferences `[rdi]`, `[rdi + rax]`,
`[rsi]`, etc. directly.

Compiler-internal callers of these fns (`_nova_int_to_str` → strlen,
`_nova_concat` → strlen, `_nova_split` → _nova_substr, …) pass raw
pointers from their own working-register state, so the invariant is
already satisfied along those edges. **Do not patch these fns to
tag-poly** — that is the specific mistake R46 made and reverted when
self-host broke on compiler-internal literal-pointer callers.

### Tier 2 — Public shims gate tag transitions at the API boundary

Every user-callable string / list / socket / syscall name has a
corresponding **public shim** label (`out_label("strlen")`,
`out_label("bind_socket")`, …) at `codegen.nova:22060–22565`. These
shims are the sole bridge between user code (which routinely holds
alloc'd tagged pointers) and the raw-pointer internal world.

Each shim performs, in order:

1. **Tag-poly on each pointer arg:**
   ```nova
   out("    test rdi, 1")
   out("    jz   .<fn>_sh_rdi")
   out("    sar  rdi, 1")
   out(".<fn>_sh_rdi:")
   ```
   This leaves raw literal pointers (bit 0 = 0 by `.align 2`)
   untouched and untags tagged alloc'd buffers in place. Apply once
   per pointer arg. The label namespace is `.<fn-slug>_sh_<reg>` to
   stay collision-free.

2. **Unconditional `sar` on int args** (fd, count, length, offset,
   port, etc.): user code always passes NOVA ints which are always
   tagged, so no polymorphism is needed. Writing a plain `sar rdi, 1`
   is correct and shorter than a tag-poly block.

3. **For socket-fd args** specifically: `_nova_socket` and
   `_nova_sys_socket_udp` tag their rax return so downstream
   `bind_socket(fd, …)` / `close_fd(fd)` / etc. always see a tagged fd
   at the shim entry. Keep this invariant consistent across any new
   fd-returning syscall builtin (future `pipe`, `accept`, `dup`, …).

4. **Jump or call-with-tag on return:**
   - If the internal fn returns a non-int (buffer, list handle,
     structure), use `jmp _nova_<fn>` — nothing to re-tag.
   - If the internal fn returns a raw int, use
     `call _nova_<fn>` + `lea rax, [rax + rax + 1]` + `ret`. Required
     for `str_eq`, `str_find`, and every socket shim.

### Tier 0 — Normalize raw-kernel-pointer builtins at source

Fns that return a pointer into memory NOT owned by NOVA — argv /
envp / future `readlink` / `realpath` / `strerror` / `getcwd` — must
copy the kernel region into a NOVA-allocated heap buffer **before
returning**. `_nova___arg` at `codegen.nova:16997` and `_nova_getenv`
at `codegen.nova:19249` both go through `_nova_concat("", raw_ptr)`
for this: `_nova_concat` goes through `_nova_alloc` whose return is
8-byte aligned, so the final returned pointer is guaranteed bit 0 =
0, matching Tier 1's expectation and Tier 2's `test rdi, 1`
assumption.

**Rule:** any new builtin whose source material is kernel-pushed or
otherwise outside NOVA's alignment guarantees must apply this
normalization before returning. The pattern is literally
`lea rdi, [raw_source] / lea rsi, [rip + _empty_str] / call _nova_concat`.

### Numeric list-element convention

`_nova_list_new` + `_nova_push` build lists whose elements the user
reads as `list[i]`. The user interprets each element as a NOVA value.
Therefore **every element pushed must be a NOVA-tagged value**:
tagged int, tagged bool, raw ptr (bit 0 = 0 as above), or a list
handle. Pushing a raw int (as `_nova_sys_recvfrom` did pre-R53 for
`bytes_read` / `sender_ip` / `sender_port`) produces list elements
that read back off by a factor of 2 under `int_to_str`'s `sar rax, 1`.
The convention is: when a syscall produces a numeric result that
flows into a list, tag it with `lea rsi, [rsi + rsi + 1]` before
`call _nova_push`.

### Inline-asm shim conventions (style)

Hand-rolled asm blocks that invoke syscalls directly (not through
`_nova_*` labels) carry the same obligations. The canonical shape,
from `_stream_sys_read` and `_stream_fcntl`:

```asm
"mov rax, <syscall>"
"mov rdi, [rbp-8]"       # fd — tagged int from a NOVA literal
"sar rdi, 1"
"mov rsi, [rbp-16]"      # buf — tag-poly (may be alloc'd or literal)
"test rsi, 1"
"jz .shim_rsi_raw"
"sar rsi, 1"
".shim_rsi_raw:"
"mov rdx, [rbp-24]"      # count — tagged int from a NOVA literal
"sar rdx, 1"
"syscall"
"test rax, rax"          # non-negative return → tag; -errno self-tagging
"js .shim_ret"
"lea rax, [rax + rax + 1]"
".shim_ret:"
```

Omit `sar rdi` only when the fd is known to arrive raw (e.g. from
`sys_open`'s raw return, as `_sys_read_tagged` in `sys_tagged.nova`
does). Omit the tag-on-non-neg only when the caller never does
`== 0` / `< 0` NOVA-int comparisons on the return (rare).

## Consequences

### Positive

- **Latent-SEGV class closed.** The R43–R59 arc retired every tag-
  convention-mismatch SEGV surface the project had: face_recognize
  (R30/R46), kg_rss_ingest (R47), nat_traversal (R41/R53),
  p256_keypair_load (R55), truststore_dir flake (R55), stream_stdin
  pipeline (R49/R58/R59), + the generic `len`/`substr`/`char_at`/
  `str_to_int`/`sys_*` class across strings and sockets. Full
  Crossengin unit matrix: 478/478 clean. Integration
  `scenario_l_stream_stdin.sh`: 5/5.
- **Future intrinsics have a decision tree.** Adding a new string
  intrinsic? Public shim with Tier 2 prolog. New socket wrapper?
  Same pattern plus fd-return-tag if applicable. New
  kernel-pointer-returning fn? Tier 0 normalize before return.
- **The compiler-internal fast path stays raw.** Hot loops like
  `_nova_concat`'s byte-by-byte copy pay zero overhead per call —
  the branch at the shim boundary is paid once per user call, not
  once per internal step.
- **Drift becomes grep-able.** A code reviewer can `grep "jmp _nova_"
  codegen.nova` and audit whether each shim implements the Tier 2
  prolog; a missing `sar rdi` on a shim with tagged-int args is now
  a clear ADR violation, not a judgment call.

### Negative

- **Two parallel conventions to remember.** Internal fn vs public
  shim have opposite expectations. Mitigation: this ADR + per-shim
  comment pointing at it.
- **Shim size grows.** A `jmp _nova_<fn>` was 1 LOC; a full Tier 2
  shim with pointer tag-poly on two args, int untag on two args, and
  call-with-tag return is ~15 LOC. Net inline-asm size growth across
  the R50/R52/R53 sites: ~250 LOC. Acceptable given the SEGV
  retirement.
- **Named exceptions already exist.** `_nova_str_len` is a duplicate
  of `_nova_strlen` with no public shim; `_nova_eq` calls
  `_nova_str_eq` directly for its own tagging flow (bypasses the
  shim). Both are documented as INTENTIONAL exceptions in UPSTREAM
  §17; this ADR carries them forward. New exceptions must be
  justified against the same contract.
- **Tier 0 adds a strlen+alloc+memcpy per `getenv` / `__arg` call.**
  Negligible: both are called rarely, mostly at process start.

### Alternatives considered

- **Patch internal fns to tag-poly.** Tried in R46; broke self-host
  because the compiler itself passes stack addresses to internal
  fns, and `sar rdi, 1` on a stack address `0x7FFF_FFFF_C617`
  halves it to an unmapped page. Rejected.
- **Make `alloc()` return raw.** Breaks every caller that currently
  stores alloc'd pointers through tag-sensitive paths (`push`, list
  storage, polymorphic equality, truthiness). Rejected as a global
  refactor without clear win.
- **Use a different tag bit** (bit 48 / bit 63). Larger structural
  change; would require every runtime fn to be re-audited anyway and
  loses the "raw heap address IS already a valid raw int" property.
  Rejected.
- **Tree-wide caller audit** instead of per-shim gating. Fragile:
  requires every new `alloc()` user to remember to tag before passing
  to runtime fns. Rejected — the shim-level fix is centrally
  verifiable.

### Named future-audit targets

Any new builtin in these categories inherits an ADR obligation:

- **Kernel-pointer-returning fns** (Tier 0 normalize-at-source):
  `_nova_readlink`, `_nova_realpath`, `_nova_strerror`,
  `_nova_getcwd` (if ever added), any `/proc` /`/sys` readers.
- **Fd-returning syscalls** (tag-rax on non-neg return):
  future `pipe`, `dup`, `dup2`, `accept4` variants.
- **List-element-pushing syscalls** (Tier §Numeric list-element):
  any new `_nova_sys_*` that pushes raw syscall data to a result
  list.
- **Inline-asm shims** anywhere else in the tree:
  R49/R58/R59 patterns in `src/io/transducers/stream_stdin.nova`;
  any new such shim must use the canonical shape above.

## References

- `/home/user/Crossengin-demo/docs/UPSTREAM_NOVA_BUGS.md` §17, §17b,
  §18 — informal spec and the per-fn coverage table across R44 / R46
  / R50 / R52 / R53 / R55.
- `/home/user/NOVA/docs/PTR_TAGGING_PLAN.md` — upstream tagging
  design context.
- `src/compiler/codegen.nova:22060–22565` — current shim block.
- `src/compiler/codegen.nova:16629`, `16903`, `16927`, `16953` —
  internal-fn prologs (R44 + R46 exceptions where internal-fn
  tag-poly is safe because no stack-pointer callers).
- `src/compiler/codegen.nova:16997, 19249` — Tier 0 normalize
  sites.
- `src/compiler/codegen.nova:18020–18128` — numeric list-element
  tagging (R53).
- `/home/user/Crossengin-demo/src/io/transducers/stream_stdin.nova`
  R49 / R58 / R59 inline-asm shims — canonical pattern.

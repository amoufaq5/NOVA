# ADR 0007: Durable storage engine -- WAL + crash recovery

## Status
Proposed -- 2026-06-15. First infrastructure sub-decision under
ADR 0006. Design and scope; the engine is largely unbuilt. v1 targets
single-node durability only -- distributed replication is NOVA-0008.

## Context
CrossEngin persists its working state to a plain text file written via
tmp-file-plus-rename. This is the persistence layer ADR 0006 commits
NOVA to replace. Its problems are concrete:

  - **No durability under crash.** A crash partway through writing the
    file -- or before the rename's directory entry is fsynced -- can
    leave a truncated or absent snapshot. There is no log to replay
    forward from.
  - **No indexing.** Reads scan the whole file. A 100k-atom store is
    roughly 10MB of flat text and grows linearly (CrossEngin ADR-0003,
    ADR-0048).
  - **No incremental write.** The substrate writes atoms and beliefs
    continuously; the flat file forces a full rewrite to persist any
    change.

CrossEngin needs durable, crash-safe, indexed persistence for two
consumers: the **atom store** at scale (CrossEngin ADR-0003) and the
**knowledge-graph snapshots** that rehydrate in soul -> KGs ->
episodic order on restart (CrossEngin ADR-0048). Both need atomic
write -- a snapshot is either fully present or fully absent after a
crash, never half-written.

The hard constraints from NOVA:

  - **No libc.** Durability primitives are raw Linux syscalls only:
    `pwrite`/`pread` for positioned I/O, `fsync`/`fdatasync` for
    durability barriers, through the existing `src/runtime` syscall
    layer.
  - **No GC, arena memory.** The engine manages its own page/block
    buffers inside arenas with explicit lifetimes (ADR 0001). No
    hidden allocation on the write path.

The workload shape matters for the on-disk structure: the substrate is
**write-heavy and continuous** -- it appends atoms and revises beliefs
constantly, with reads concentrated on recent and indexed data rather
than large historical range scans.

## Decision
**Design a native NOVA storage engine providing single-node durability
and crash recovery, built on a write-ahead log and a log-structured
merge-tree, talking to disk through raw Linux syscalls and managing
its buffers in arenas.**

### Core elements

  - **Write-ahead log (WAL).** Every mutation is appended to the WAL
    and made durable (`fdatasync`) *before* it is acknowledged. The
    WAL is the source of truth for recovery. Each record carries a
    checksum and a monotonic sequence number.
  - **On-disk structure: log-structured merge-tree (LSM), primary
    choice.** New writes land in an in-arena memtable, flushed to
    immutable on-disk sorted runs (SSTable-style), with background
    compaction merging runs. LSM is chosen because the substrate's
    write-heavy continuous-learning pattern maps onto append-and-
    compact far better than in-place updates. (See alternatives for
    the B-tree comparison.)
  - **Crash recovery via WAL replay.** On startup the engine finds the
    last consistent checkpoint and replays WAL records after it,
    stopping at the first record whose checksum fails (a torn tail).
    Recovery is idempotent: replaying the same WAL twice yields the
    same state.
  - **Checkpoints / snapshots.** Periodically the engine writes a
    consistent checkpoint and truncates the WAL prefix it subsumes.
    Snapshot write is **atomic**: write to a temporary object, fsync
    it, fsync the directory, then rename -- or, equivalently, mark a
    WAL checkpoint record durable. This is the atomic-snapshot
    guarantee CrossEngin's flat-file state lacks (ADR-0048).
  - **Checksums / corruption detection.** Every WAL record and every
    on-disk block carries a checksum. A failed checksum on read is a
    detected corruption, not silent bad data. This is mandatory, not
    optional.
  - **Arena + no-GC integration.** Memtable, block cache, and
    compaction scratch all live in engine-owned arenas. Buffer
    lifetimes are explicit; there is no allocation the engine does not
    account for.

### Scope of v1
  - In scope: single-node WAL durability, crash recovery to a
    consistent checkpoint, an LSM store with basic compaction,
    checksums, atomic snapshot write.
  - Out of scope: distributed replication (that is NOVA-0008, which
    persists the Raft log *via this engine*); aggressive performance
    tuning (compaction scheduling, write-amplification minimisation,
    bloom filters) is acknowledged future work, not v1.

## Consequences
**Positive.**
  - **Durability under crash.** The WAL gives CrossEngin a real
    forward-recovery story; a crash mid-write loses at most the
    unacknowledged tail, never the committed prefix.
  - **Atomic snapshots.** CrossEngin's KG snapshot write becomes all-
    or-nothing, fixing the truncation risk in the flat-file approach
    (ADR-0048).
  - **Scales past the flat file.** Indexed LSM reads replace whole-
    file scans; incremental writes replace full rewrites. The ~10MB-
    per-100k-atom flat bloat is no longer the persistence model
    (ADR-0003).
  - **Foundation for consensus.** NOVA-0008's Raft log needs exactly
    this: an append-only, fsync-durable, recoverable log. Building it
    here once serves both.

**Negative.**
  - **Crash-safe storage is genuinely hard.** fsync ordering, torn
    writes, the directory-entry durability subtlety on rename, partial
    page writes, and the long tail of recovery edge cases are where
    mature databases spent *years*. We will get some of this wrong
    before we get it right; the test strategy below is the mitigation,
    not a guarantee.
  - **LSM has write amplification.** Compaction rewrites data multiple
    times. Untuned, this costs I/O. v1 accepts unoptimised compaction;
    tuning is future work.
  - **No range-scan optimisation in v1.** If a later CrossEngin
    workload becomes read-heavy with large range scans, the B-tree
    tradeoff (below) may need revisiting or a hybrid.
  - **More code to keep auditable.** A storage engine is a large,
    subtle body of NOVA. It widens the surface a small team must hold
    in its head.

**Follow-up.**
  - Performance: compaction scheduling, bloom filters, write-
    amplification tuning.
  - NOVA-0008 consumes this engine for the Raft log.
  - Revisit B-tree / hybrid if a read-heavy range-scan workload
    emerges.

## Implementation notes
  - Disk I/O goes through the existing `src/runtime` syscall layer:
    `pwrite`/`pread`/`fsync`/`fdatasync`/`rename`. No libc.
  - Buffers are arena-allocated; the engine owns its memtable and
    block-cache arenas explicitly (ADR 0001). No GC.
  - WAL record layout: sequence number, checksum, payload. Replay
    stops at the first checksum mismatch (the torn tail).

## Testing
  - **Crash injection.** Kill the process mid-write at varied points;
    on restart, replay the WAL and assert recovery to the last
    consistent checkpoint with **no partial atoms** -- a record is
    either fully present or absent.
  - **Corruption detection.** Flip bytes in a WAL record / on-disk
    block; assert the checksum catches it on read rather than
    returning bad data.
  - **Snapshot atomicity.** Kill during snapshot write; assert the
    prior snapshot is intact and the new one is either complete or
    absent, never half-applied.
  - **Recovery idempotence.** Replay the same WAL twice; assert
    identical resulting state.

## Alternatives considered
  - **Keep flat-file + tmp-rename.** Rejected: no durability under
    crash, no indexing, full-rewrite-per-change, ~10MB-per-100k-atom
    bloat (ADR-0003, ADR-0048). It is the gap this ADR exists to
    close.
  - **LSM engine.** Chosen primary -- matches the write-heavy,
    continuous-learning substrate workload; append-and-compact suits
    constant atom/belief writes.
  - **B-tree engine.** Considered. Better for read-heavy workloads and
    range scans, and lower write amplification per logical write *in
    place* -- but in-place updates fit the substrate's append-heavy
    pattern worse, and a crash-safe B-tree (with its own WAL and page-
    split torn-write handling) is no simpler to harden. Kept on the
    table for a future read-heavy workload or a hybrid.
  - **Memory-mapped file (`mmap`).** Rejected: leans on OS page-cache
    write-back semantics for durability, giving weaker and less
    explicit guarantees than `pwrite` + `fdatasync`; msync ordering is
    subtle, and the model is awkward to drive cleanly without libc.
    Explicit positioned I/O is easier to reason about and to crash-
    test.

## Implementation status

**Increment 1 (landed): the write-ahead log.** `src/storage/wal.nova`
implements the durable append-only log the LSM/index layers will build on.
Records are framed `[magic][seq][len][crc32][payload]` (little-endian);
`wal_append` writes a whole record per `write()` and `wal_sync` calls
`fdatasync`. Recovery (`wal_read_*`, `wal_recover_count`,
`wal_recover_next_seq`) replays from the start and stops at the first torn
(short read at a boundary) or corrupt (bad magic / CRC mismatch) record,
preserving exactly the durably-committed prefix; `wal_truncate_to`
physically drops a torn tail. The four durability syscalls this needs --
`pread`(17), `pwrite`(18), `fdatasync`(75), `ftruncate`(77) -- were added
to `src/runtime/syscall.nova`.

Verified by `tests/test_wal.nova` (compiled and run via the bootstrap; the
standard harness skips file-I/O tests): append + sequence assignment,
fdatasync, replay with payload/seq verification, clean EOF, next-seq
recovery across reopen, torn-tail recovery, physical truncation + fresh
append, and CRC detection of payload corruption -- all green.

Two findings worth recording for later increments:
  - **Pointer-safe equality is mandatory for 32-bit values.** Nova's
    `==`/`!=` structurally dereference operands above the `0x100000`
    pointer threshold, so comparing two unequal ~4e9 values (magic words,
    CRCs) faults. The WAL uses `wal_u32_eq` (`int_sub(a,b) == 0`, the same
    idiom as `runtime/crypto.nova`'s `int_eq`); offset/seq arithmetic uses
    `int_add`. The LSM layer must follow the same discipline.
  - **Scope still open:** records capped at 4 KiB; seq is effectively
    32-bit under Nova's tagged-integer width; the LSM index, compaction,
    and checkpoints remain future increments (per NOVA-0006).

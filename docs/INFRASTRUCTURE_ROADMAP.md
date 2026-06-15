# NOVA Infrastructure Roadmap

> Companion to ADR-0006. Tracks NOVA's infrastructure build-out for the
> NOVA-everywhere stack.

This is a design / status document, not an ADR. The decisions it tracks
live in `docs/adr/0006-nova-full-stack-infrastructure.md` (umbrella),
`0007-durable-storage-engine.md`, and
`0008-distributed-consensus-raft.md`.

## Motivation

CrossEngin (a non-LLM cognitive substrate written entirely in NOVA) is
expanding into a truth-seeking reasoning engine that must scale from a
single embedded device to a distributed cluster (CrossEngin ADR-0086).
CrossEngin's **Project Rule 1** is absolute: *no third-party
dependencies*. The entire stack -- cognition and the infrastructure
under it -- is NOVA. "NOVA everywhere."

The consequence is that NOVA, not a vendored database or consensus
library, is responsible for the production systems infrastructure
CrossEngin needs. Today that infrastructure is mostly absent:
persistence is a plain text file, and there is no consensus layer. This
roadmap enumerates the layers, their build order, and which CrossEngin
phase each one gates.

## The stack

```
+-----------------------------------------------------------+
|  Application:  CrossEngin cognition                       |
|    substrate, knowledge graphs, debate / truth-seeking    |
+-----------------------------------------------------------+
|  NOVA Infrastructure  (reusable, stdlib-level tier)       |
|                                                           |
|    Serving + observability ........... PLANNED  (future)  |
|    Consensus ......................... PLANNED  NOVA-0008  |
|    Storage engine (WAL + recovery) ... PLANNED  NOVA-0007  |
|    Transport (DTLS/ICE/STUN/TURN/TLS)  DONE               |
+-----------------------------------------------------------+
|  NOVA Core                                                |
|    compiler  |  arena allocator  |  syscalls (no libc)    |
|    self-hosting fixed point (stage2.s == stage3.s)        |
+-----------------------------------------------------------+
```

Each infrastructure layer sits on the one below it. Consensus needs the
storage engine (for its persistent log) and the transport (for RPC).
The storage engine needs only the NOVA core. Transport is already built.

## Status

| Layer                      | ADR       | Status  | Gates CrossEngin phase                       |
|----------------------------|-----------|---------|----------------------------------------------|
| Transport (DTLS/ICE/STUN/TURN/TLS) | (pre-0006) | **DONE**    | Federation, already in production use        |
| Storage engine (WAL + recovery)    | NOVA-0007  | PLANNED | ADR-0086 Phase 1 (durable persistence)       |
| Consensus (Raft)                   | NOVA-0008  | PLANNED | ADR-0086 Phase 5 (distributed scale)         |
| Serving + observability            | future     | PLANNED | Operability of the scaled cluster            |

Build order is strict: **transport -> storage -> consensus -> serving**.
A layer cannot be trusted until the one beneath it is solid; NOVA-0008's
Raft log is persisted through NOVA-0007, so consensus work cannot
meaningfully start until storage is hardened.

## What "massive scale" actually requires

It is worth stating plainly how far today's reality is from the goal, so
that downstream scale claims are read as a roadmap rather than a
capability.

**Today (NOVA 0.1.0, experimental):**

  - Persistence is a single plain text file written via tmp-file +
    rename. No write-ahead log, no crash recovery, no indexing. A
    100k-atom store is roughly 10MB of flat text and a crash mid-write
    can truncate it (CrossEngin ADR-0003, ADR-0048).
  - One process per tenant (CrossEngin ADR-0047). State lives on one
    node; a node loss is a data loss.
  - No consensus, no replication, no sharding across machines.

**The goal (CrossEngin ADR-0086 Phase 5):**

  - Knowledge graphs sharded across many nodes -- the 1M-node to
    1B-node jump in CrossEngin ADR-0003 as a multi-machine reality.
  - Each shard replicated for durability and availability, surviving
    node loss as long as a majority is up.
  - One-tenant-per-process isolation (CrossEngin ADR-0047) preserved
    across the cluster: a tenant maps to a set of replica groups, not a
    single machine.

**The gap, in dependency order:**

  1. **Transport -- DONE.** The federation stack (DTLS 1.2, ICE, STUN
     RFC 8489, TURN client + server, SRTP, SCRAM-SHA-256, DNS-over-UDP,
     a TLS 1.3 client) is RFC-grade and already used in production. This
     is the one layer that is genuinely finished.

  2. **Storage engine (NOVA-0007).** A native WAL with crash recovery, a
     log-structured merge-tree on disk, checksummed records, and atomic
     snapshot writes -- all through raw Linux syscalls (no libc) and
     arena-managed buffers (no GC). Correct crash-safe storage (fsync
     ordering, torn writes, the long tail of recovery edge cases) is
     work that mature databases spent *years* hardening. v1 is
     single-node durability only.

  3. **Consensus (NOVA-0008).** Raft implemented natively in NOVA over
     the transport for RPC and over NOVA-0007 for its persistent log:
     leader election, log replication, safety, snapshotting, membership
     changes. Distributed consensus is among the hardest things in
     systems engineering -- partitions, split-brain, clock skew, the
     long tail of liveness bugs -- and demands deterministic
     fault-injection testing for any real confidence. It is the last
     and most dependent layer; it cannot be trusted until NOVA-0007 is.

  4. **Serving + observability (future).** A request/serving surface and
     metrics/tracing primitives so the scaled cluster is operable.
     Scoped in ADR-0006, not yet designed.

The honest summary: NOVA owns this entire ladder because Rule 1 leaves
no alternative. Each rung above transport is multi-year systems work
that must be built and fault-tested before any CrossEngin scale phase
that depends on it can be more than a roadmap. None of this code may
break NOVA's core invariants -- no libc, no GC, arena memory, and the
self-hosting fixed point (`stage2.s == stage3.s`, ADR-0002).

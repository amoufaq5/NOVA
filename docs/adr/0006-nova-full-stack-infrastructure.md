# ADR 0006: NOVA as full-stack infrastructure -- boundary + roadmap

## Status
Proposed -- 2026-06-15. Umbrella ADR. Sets direction and boundaries
for the systems-infrastructure layers NOVA must grow; the work itself
is largely unbuilt. NOVA-0007 (storage) and NOVA-0008 (consensus) are
the first two committed sub-decisions.

## Context
NOVA is 0.1.0 and experimental. It is a self-hosting, compiled
language with no libc, no GC, no runtime interpreter (ADR 0001,
ADR 0002). Its standard library is deliberately small and there is no
third-party ecosystem -- that absence is a design stance, not a gap to
be filled with dependencies (ADR 0001).

The downstream project CrossEngin (a non-LLM cognitive substrate
written entirely in NOVA) is expanding from a single embedded device
into a "truth-seeking reasoning engine" that must scale to a
distributed cluster (CrossEngin ADR-0086). CrossEngin's Project Rule 1
is absolute: **no third-party dependencies**. The entire stack --
cognition *and* the infrastructure underneath it -- is NOVA. "NOVA
everywhere."

That rule has a consequence NOVA cannot dodge: NOVA becomes
responsible for production systems infrastructure it does not yet
have. The current reality is stark:

  - CrossEngin persists its working state to a **plain text file**.
    There is no write-ahead log, no crash recovery, no indexing. A
    100k-atom store bloats to ~10MB of flat text and a crash mid-write
    can truncate it (CrossEngin ADR-0048, ADR-0003).
  - There is **no consensus layer**. CrossEngin runs as a single
    process per tenant (CrossEngin ADR-0047). The 1M-node to 1B-node
    jump in CrossEngin ADR-0003 implies sharding across processes and
    devices, which implies replication and agreement -- none of which
    exists.

What NOVA *does* already have is transport. The networking/federation
stack used by CrossEngin is hand-rolled and RFC-grade: DTLS 1.2, ICE,
STUN (RFC 8489), a TURN client and server, SRTP, SCRAM-SHA-256, plus
DNS-over-UDP and a TLS 1.3 client. So the bottom of the infrastructure
stack is done. The middle and top are not.

The question this ADR answers is not "should NOVA build all of this"
(Rule 1 forces yes) but **where the boundary sits** between NOVA and
CrossEngin, and **in what order** the missing layers get built.

## Decision
**NOVA commits to growing a first-class infrastructure layer -- a
reusable, stdlib-level tier sitting between the NOVA core and any
application -- sufficient to host CrossEngin end-to-end with zero
third-party dependencies, while preserving every NOVA core invariant.**

### Invariants the new code must not break
  - **No libc.** Infrastructure talks to the OS through the existing
    syscall layer only (`src/runtime`). No new C linkage.
  - **No GC.** All buffers, logs, indexes, and consensus state live in
    arenas with explicit lifetimes (ADR 0001). No hidden allocation.
  - **Self-host fixed point.** None of this code is in the compiler's
    bit-identical critical path (ADR 0002), but anything that *is*
    must keep `stage2.s == stage3.s`. Infrastructure is application-
    level NOVA and is gated by the normal test suite, not the
    self-host invariant -- but it must never introduce non-determinism
    that leaks back into codegen.

### The boundary
What belongs **in NOVA** (language + stdlib-level, reusable by any
NOVA program):

  - **Storage engine** -- durable, crash-safe, indexed local
    persistence (NOVA-0007).
  - **Distributed consensus** -- replicated agreement over the
    existing transport (NOVA-0008).
  - **Serving + observability** -- a request/serving surface and
    metrics/tracing primitives (future ADR; scoped here, not yet
    decided).

What stays **in CrossEngin** (cognition, not infrastructure):

  - The substrate itself, knowledge graphs, the debate/truth-seeking
    engine, snapshot rehydration order (CrossEngin ADR-0048),
    tenant isolation policy (CrossEngin ADR-0047).

The test for "does it belong in NOVA": *would a second, unrelated NOVA
program want it?* A storage engine, yes. A belief-revision rule, no.
Reusable infrastructure belongs at the language layer so that NOVA
applications do not each reinvent a WAL.

### The roadmap and dependency order
The layers have a strict build order because each depends on the one
below it:

  1. **Transport -- DONE.** DTLS/ICE/STUN/TURN/TLS already exist and
     are in production use by CrossEngin's federation.
  2. **Storage engine -- NOVA-0007.** WAL + crash recovery + on-disk
     structure. Single-node durability first.
  3. **Consensus -- NOVA-0008.** Raft over the transport (1) with its
     log persisted by the storage engine (2). Cannot start before
     (2) is solid.
  4. **Serving + observability.** A serving surface and metrics on top
     of (1)-(3). Scoped, not yet specified.

CrossEngin's scale phases are gated on this order: ADR-0086 Phase 1
(persistence) needs NOVA-0007; Phase 5 (distributed scale) needs
NOVA-0008, which needs NOVA-0007.

## Consequences
**Positive.**
  - **Total control and auditability.** Every byte of the stack is
    NOVA the team wrote and can read. No opaque dependency, no supply-
    chain surface, no version-skew between a vendored DB and the
    language. This is the same bet ADR 0001 and ADR 0002 make at the
    language level, extended to infrastructure.
  - **Zero third-party dependencies preserved.** Rule 1 holds without
    exception across the whole stack.
  - **Reusable beyond CrossEngin.** The storage engine and consensus
    layer are not CrossEngin-specific; any future NOVA system gets
    them for free. The boundary is what makes this true.
  - **The differentiator is intact.** A fully auditable, dependency-
    free cognitive *and* infrastructure stack is NOVA's whole pitch.
    Adopting a third-party DB would surrender it.

**Negative.**
  - **Enormous surface area for a small team.** Storage engines and
    consensus protocols are each multi-year systems projects. The
    industry took *decades* to harden the things we are committing to
    rebuild. This ADR sets direction; it does not pretend the work is
    near done.
  - **NOVA is 0.1.0.** Production-hardening of these layers is far
    off. Anything that depends on them at scale (CrossEngin Phase 5)
    is, today, gated on years of work.
  - **Scale claims are promissory.** Until NOVA-0007 and NOVA-0008
    ship and are fault-tested, CrossEngin's distributed-scale story is
    a roadmap, not a capability. We state this plainly to downstream
    consumers.
  - **Opportunity cost.** Every hour on infrastructure is an hour not
    on the language. We accept this because Rule 1 leaves no
    alternative.

**Follow-up.**
  - NOVA-0007: durable storage engine (WAL + crash recovery).
  - NOVA-0008: distributed consensus (Raft over federation transport).
  - Future ADR: serving + observability layer.
  - `docs/INFRASTRUCTURE_ROADMAP.md` tracks layer status and the
    CrossEngin phases each layer gates.

## Alternatives considered
  - **Adopt a third-party database / consensus library** (SQLite,
    RocksDB, etcd, a Raft library). Rejected: violates Rule 1
    outright, reintroduces a C/libc dependency NOVA exists to escape
    (ADR 0001), and surrenders the zero-dependency auditability that
    is NOVA's entire bet. The convenience is real; the cost is the
    differentiator.
  - **Keep infrastructure ad-hoc, per-application, inside CrossEngin.**
    Rejected: every NOVA application would then reinvent storage and
    replication. Infrastructure that is reusable belongs at the
    language/stdlib layer. Leaving it in CrossEngin also blurs the
    cognition/infrastructure boundary that keeps both projects
    auditable.
  - **Declare the existing flat-file + single-process model
    sufficient.** Rejected: it does not survive a crash mid-write,
    does not index, and does not scale past one device. CrossEngin
    ADR-0086 explicitly requires more.
  - **Grow a first-class NOVA infrastructure layer with a clear
    boundary.** Chosen. It is the only option that honours Rule 1,
    avoids per-app reinvention, and keeps the cognition/infrastructure
    line legible.

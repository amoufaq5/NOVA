# ADR 0008: Distributed consensus -- Raft over the federation transport

## Status
Proposed -- 2026-06-15. Second infrastructure sub-decision under
ADR 0006 and the last layer in the infrastructure build order. Design
and scope only; the consensus layer is unbuilt and **depends on
NOVA-0007 being solid first** -- the Raft log is persisted via that
storage engine. v1 targets a small cluster, not planet scale.

## Context
ADR 0006 commits NOVA to growing the infrastructure CrossEngin needs
under Project Rule 1 (no third-party dependencies), in a strict build
order: transport (DONE) -> storage (NOVA-0007) -> consensus (this ADR)
-> serving. This ADR is the top of that order.

CrossEngin today runs as a single process per tenant (CrossEngin
ADR-0047). There is **no consensus layer**: state lives on one node,
and a node loss is a data loss. CrossEngin ADR-0086 Phase 5 requires
distributed scale, and CrossEngin ADR-0003's 1M-node to 1B-node jump
is, concretely, a sharding-across-machines problem: knowledge graphs
partitioned over many nodes, each shard replicated for durability and
availability. None of that is possible without an agreement protocol
that keeps replicas consistent through failures.

What NOVA already has makes this tractable rather than green-field:

  - **Transport is done.** The federation stack is RFC-grade and
    hand-rolled: DTLS 1.2, ICE, STUN (RFC 8489), a TURN client and
    server, SRTP, SCRAM-SHA-256 (ADR 0006). Consensus needs an
    authenticated, ordered, reliable channel between peers; the
    federation transport already provides one. We are not writing a
    network stack -- we are writing a state machine on top of one.
  - **A durable log is being built.** NOVA-0007's WAL is an append-
    only, fsync-durable, crash-recoverable log. Raft's persistent log
    has exactly those requirements. NOVA-0008 does not invent its own
    persistence; it persists the Raft log *through* NOVA-0007.

The hard constraints from NOVA carry over unchanged: no libc (RPC and
disk I/O go through the existing transport and syscall layers), no GC
(all consensus state -- log, peer tables, vote records -- lives in
arenas with explicit lifetimes, ADR 0001).

## Decision
**Implement the Raft consensus algorithm natively in NOVA, layered on
the existing federation transport for RPC and on the NOVA-0007 storage
engine for the persistent log.**

### Why Raft (and not Paxos)
Raft was designed for understandability, and that is the deciding
factor for a small team that must hold the whole protocol in its head
(the same constraint that shapes ADR 0001 and ADR 0002). Raft is
*prescriptive*: it specifies leader election, log replication, the
commit rule, snapshotting, and -- critically -- a concrete
cluster-membership-change protocol. Paxos describes a consensus
*primitive*; turning it into a replicated log with reconfiguration and
log compaction is left as an exercise, and that exercise is exactly
where correctness bugs hide. We choose the protocol that leaves the
least undefined.

### Scope
  - **Leader election.** Randomised election timeouts, terms, votes.
    One leader per term; a candidate that wins a majority becomes
    leader.
  - **Log replication.** The leader appends client entries to its log
    (NOVA-0007), replicates via `AppendEntries`, and advances the
    commit index once a majority has persisted an entry.
  - **Safety.** Committed entries are durable and consistent across the
    cluster: an entry acknowledged to a client is never lost and never
    diverges. The election restriction (a candidate must have an
    up-to-date log to win) and the commit rule enforce this.
  - **Snapshotting / log compaction.** The Raft log cannot grow without
    bound. Periodically the state machine is snapshotted and the log
    prefix discarded, using NOVA-0007 checkpoints as the compaction
    mechanism. Lagging followers are caught up with an
    `InstallSnapshot` RPC.
  - **Cluster membership changes.** Joint-consensus single-server
    add/remove so the cluster can be reconfigured without downtime and
    without a window where two disjoint majorities exist.

### Mapping onto existing layers
  - **RPC** (`RequestVote`, `AppendEntries`, `InstallSnapshot`) rides
    the federation transport. Peers are already authenticated; we do
    not add a parallel network path.
  - **Persistent state** (current term, voted-for, the log itself) is
    written through NOVA-0007's WAL and made durable before any RPC
    response that depends on it. The fsync-before-acknowledge
    discipline NOVA-0007 already enforces is exactly Raft's persistence
    requirement.

### Purpose for CrossEngin
This layer is what makes CrossEngin's distributed-scale story real:
sharded knowledge graphs spread across nodes (CrossEngin ADR-0003's
1M -> 1B jump as a multi-node reality), each shard a replicated Raft
group, with one-tenant-per-process isolation (CrossEngin ADR-0047)
preserved across the cluster -- a tenant maps to a set of replica
groups, not a single machine. This is CrossEngin ADR-0086 Phase 5.

### Scope of v1
  - In scope: leader election, log replication, the commit/safety
    rules, snapshotting via NOVA-0007, single-server membership
    changes, a deterministic fault-injection test harness.
  - Out of scope: planet-scale clusters, geo-distribution / WAN
    optimisation, Multi-Raft autosharding orchestration, leadership
    balancing. v1 is a small cluster proving the protocol is correct,
    not a tuned distributed database.

## Consequences
**Positive.**
  - **Replicated durability and availability.** With a Raft group, a
    node loss is no longer a data loss; the cluster keeps serving as
    long as a majority survives. This is the property CrossEngin Phase
    5 is gated on.
  - **Reuses the stack we already have.** Transport (DONE) and the
    NOVA-0007 log do the heavy lifting; this ADR adds the state machine
    between them. That is the payoff of ADR 0006's build order.
  - **Understandable by construction.** Choosing Raft means the
    protocol the team maintains is the protocol the team can fully
    reason about -- consistent with NOVA's whole "hold it in your head"
    philosophy.
  - **Zero third-party dependencies preserved.** No etcd, no Raft
    library, no C linkage. Rule 1 holds to the top of the stack.

**Negative.**
  - **Consensus is among the hardest things to get right.** Network
    partitions, split-brain, asymmetric links, clock skew, and the long
    tail of liveness bugs (elections that never converge, livelock
    under flapping links) are where distributed systems fail in
    production. The literature is full of *published, peer-reviewed*
    consensus implementations that later proved buggy. We will not be
    an exception on the first try.
  - **Correctness needs more than unit tests.** Real confidence comes
    from deterministic simulation / fault injection -- driving the
    cluster through partitions, delays, reorderings, and crashes under
    a controlled clock. Building that harness is itself substantial
    work and is part of this ADR's scope, not an afterthought.
  - **It is the last and most dependent layer.** NOVA-0008 cannot be
    trusted until NOVA-0007 is solid; a bug in WAL recovery surfaces as
    a consensus safety violation, which is far harder to debug than the
    storage bug that caused it. Build order is not negotiable.
  - **More subtle code for a small team.** A correct Raft is a large,
    careful body of NOVA with invariants that are not locally checkable.
    It widens the surface the team must hold in its head more than any
    other infrastructure layer.

**Follow-up.**
  - Deterministic simulation harness (controlled clock + injected
    network faults) as the primary correctness instrument.
  - Multi-Raft / autosharding orchestration once single-group Raft is
    trusted (CrossEngin ADR-0003 sharding).
  - Future ADR: serving + observability layer on top of consensus.

## Implementation notes
  - The Raft state machine is plain NOVA: a per-node struct holding
    current term, voted-for, role (follower / candidate / leader),
    commit index, and the peer table -- all arena-allocated, no GC
    (ADR 0001).
  - `RequestVote` / `AppendEntries` / `InstallSnapshot` are messages
    over the existing federation transport; no new network code.
  - The log and the durable Raft state (term, vote) are written through
    NOVA-0007's WAL with fsync-before-acknowledge. Log compaction uses
    NOVA-0007 checkpoints.
  - Time is injectable: the election/heartbeat clock is a parameter, so
    the test harness can drive it deterministically.

## Testing
  - **Leader election under partition.** Partition the cluster; assert
    at most one leader per term, and that the majority side elects a
    leader while the minority side cannot.
  - **Log convergence after heal.** Drive divergent appends during a
    partition, heal it, and assert all surviving replicas converge to
    an identical committed log.
  - **Committed-entry durability across crash + restart.** Acknowledge
    an entry, crash and restart a node (exercising NOVA-0007 recovery),
    and assert the entry is still present and consistent with the
    cluster -- no acknowledged write is ever lost.
  - **No split-brain.** Under repeated partition / heal cycles with
    flapping links, assert the safety invariant (no two leaders commit
    conflicting entries at the same index) never breaks.
  - **Snapshot install.** Force a follower to fall far behind, trigger
    `InstallSnapshot`, and assert it catches up to a consistent state.

## Alternatives considered
  - **Raft.** Chosen. Understandable and prescriptive: it specifies
    election, replication, membership change, and snapshotting
    concretely, which is what a small team needs to implement
    *correctly*, not just plausibly.
  - **Paxos / Multi-Paxos.** Rejected. Harder to implement correctly
    and far less prescriptive about the parts that bite in practice --
    log replication, reconfiguration, and compaction are under-
    specified, and that ambiguity is precisely where consensus
    implementations go wrong.
  - **No consensus; primary-backup replication only.** Rejected. A
    weaker consistency/availability tradeoff with manual failover and a
    real window for split-brain or data loss on an unclean failover. It
    is less code now but does not give CrossEngin Phase 5 the
    guarantees it needs.
  - **A third-party consensus library** (etcd/raft, a Paxos library).
    Rejected: violates Rule 1, reintroduces external/C dependencies
    NOVA exists to escape (ADR 0001), and surrenders the zero-
    dependency auditability that is the whole point (ADR 0006). The
    convenience is real; the cost is the differentiator.

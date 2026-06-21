# AGENTS.md — the agent-roster contract

This is the authoritative contract for every agent in `bayanihan-net`: its role, what it may
observe, the tools it is scoped to, what it is permitted to do, what it must **never** do, how
it escalates, and what state it holds. It mirrors the agent-design discipline from the course
(roles + tools + memory + permissions + escalation), and it is enforced in code: each agent
carries a [`SecurityContext`](src/bayanihan_net/messages.py) with capability *scopes*, and the
[`governance/policy.py`](src/bayanihan_net/governance/policy.py) layer checks them.

A design principle runs through the whole roster: **the safety-critical control plane is pure,
deterministic Python.** Agents are small decision units that read the shared Common Operating
Picture (COP) and emit typed messages; they do not mutate shared state through arbitrary
writes — state changes flow through the blackboard's guarded mutators, where the invariants
(idempotency, no-double-commit, audit) are enforced in one place.

---

## Roster

| Agent | Responsibility | Observes | Tools (mocked) | Memory / state | Scopes / permissions | Escalates when |
|---|---|---|---|---|---|---|
| **Sensing scout** ×2 ([`sensing.py`](src/bayanihan_net/agents/sensing.py)) | Convert citizen reports for its barangays into typed COP incidents; flag implausible (Sybil) reports | Raw reports for its nodes; COP river | report queue | covered-node set; message-sequence counter (otherwise stateless per tick) | `cop:write` | — (writes only) |
| **Hydromet / forecast** ([`forecast.py`](src/bayanihan_net/agents/forecast.py)) | Post river level to the COP each tick | True river level | PAGASA gauge (MCP) | message-sequence counter (stateless gauge) | `cop:write`, `tool:pagasa.read` | — |
| **Triage** ([`triage.py`](src/bayanihan_net/agents/triage.py)) | Prioritize open incidents (severity-first), suppress uncorroborated Sybil reports, announce tasks (CFP) | COP incidents | task board | message-sequence counter (reasons over the COP; no private memory) | `taskboard:write` | — |
| **Logistics** ×2 (rescue, relief) ([`logistics.py`](src/bayanihan_net/agents/logistics.py)) | Own scarce assets; bid feasible owned assets into auctions | COP tasks; own asset state; routing | asset registry | owner id; message-sequence counter (asset state lives on the COP, not the agent) | `asset:bid`, `asset:commit` | — (bids only) |
| **Medical** ([`medical.py`](src/bayanihan_net/agents/medical.py)) | Bid medical teams; watch for mass-casualty overload | COP medical demand vs. team capacity | DOH hospital-bed adapter (MCP, *registered/not invoked* in the current loop) | owner id; `_in_overload` episodic flag (edge-trigger); seq counter | `asset:bid`, `asset:commit`, `escalation:raise` | medical demand > team capacity (edge-triggered) |
| **Routing** ([`routing.py`](src/bayanihan_net/agents/routing.py)) | Compute risk-aware routes over a *belief* of the flooded network | COP river (its belief); never the true flood | MMDA road-closure adapter (MCP, *scoped/not invoked* in the current loop) | **belief network** — a persistent private model of the flood, updated from the COP; seq counter | `tool:mmda.read` | — (advises) |
| **Coordinator** (supervisor) ([`coordinator.py`](src/bayanihan_net/agents/coordinator.py)) | Re-score bids on global welfare, award, run the HITL gate, atomic commit, rollback | All bids, the gate verdict | award authority | message-sequence counter (decisions are stateless given the COP) | `asset:award`, `asset:commit`, `escalation:raise`, `rollback:issue` | no feasible asset for an overdue incident |
| **Human approver** (HITL) ([`escalation.py`](src/bayanihan_net/coordination/escalation.py)) | Approve / deny irreversible, high-stakes commitments | A decision package | final authority | none — a fixed, stateless decision policy | — (the authority) | n/a |
| **Auditor** ([`auditor.py`](src/bayanihan_net/agents/auditor.py)) | Read the event log + ground truth; compute outcome & emergence reports | Everything (read-only) | event sink | none — reads the COP + event log (the system's shared long-term memory) | `audit:read` | n/a |

### Memory model

Memory is **deliberately externalized**. The shared, durable memory is the blackboard COP and
its append-only event log — one inspectable, auditable source of truth — *not* hidden state
scattered across agents. Individual agents hold only what they must: a message-sequence counter
(for deterministic IDs), and, where a private world-model is genuinely needed, the routing
agent's **belief network** (its persistent estimate of the flood, which can lag the truth — the
seat of stale-COP mis-routing) and the medical agent's `_in_overload` **episodic flag** (so a
sustained alarm is one escalation, not a storm). This is a safety choice: state that drives
irreversible decisions lives where it can be audited, not in an agent's opaque scratchpad.

The intro deck's **four-type memory taxonomy** (working / episodic / semantic / procedural) maps
cleanly, and its default posture — *"store less, redact aggressively, expire fast"* — is enforced
by our leases and freshness checks:

| Deck memory type | Purpose | Where it lives here |
|---|---|---|
| **Working** | current task state / scratch | the COP's live incident & asset state; each agent's message-sequence counter; routing's belief network |
| **Episodic** | immutable event log, for audit / replay / incident response | the blackboard **append-only event log** (`bb.events`) with `trace_id` — the system's durable memory |
| **Semantic** | documents / embeddings / grounding | not used (deliberate) — grounding is the deterministic scenario fixtures, not a vector store, keeping the safety-critical plane inspectable |
| **Procedural** | skills / policies / playbooks | the pure `governance/policy` rules (scopes + HITL gate) and the contract-net scoring — behaviour that is the same every run |

### Mapping to the use-case menu

The brief's *Disaster response* row suggests the roster **scout → logistics → medical → routing →
command center**. Every one is present and is the backbone of this system — `scout` =
**Sensing scout**, `logistics` = **Logistics**, `medical` = **Medical**, `routing` =
**Routing**, `command center` = **Coordinator**. We add four justified roles the problem demands:
a **Hydromet/forecast** sensor (the hazard signal), a **Triage** prioritizer (the scarce-asset
queue), a **Human approver** (the irreversible-action authority the brief's safety section
requires), and a read-only **Auditor** (so the system can be evaluated without grading itself).

---

## Per-agent contracts

### Sensing scout

- **May:** post `INCIDENT_REPORT` messages for nodes it covers; set a *suspected-false* flag
  when a severe report comes from a barangay that cannot plausibly be flooding yet.

- **Never:** dispatch assets; suppress incidents (that is triage's call); fabricate severity
  (severity is a pure function of reported facts).

- **State:** the set of nodes it covers; a per-agent message sequence counter (for
  deterministic message IDs). Partial observability is *by design* — no scout sees the whole
  basin.

### Hydromet / forecast

- **May:** post river observations; source them through the scoped PAGASA MCP tool.
- **Never:** decide routes; act on the flood. If it is knocked out (a stress injection), its
  last observation goes **stale** under the COP lease — and downstream routing must cope.

### Triage

- **May:** suppress an **uncorroborated** Sybil-suspect incident (`report_count < 2`); announce
  the worst open incidents as contract-net tasks.

- **Never:** suppress a *corroborated* incident; allocate assets; bypass severity ordering
  silently (the ordering policy is explicit and logged).

### Logistics / Medical

- **May:** bid *feasible* owned, idle assets; report a selfish `local_cost`.
- **Never:** self-award (the coordinator decides); double-bid a committed asset; **hoard** —
  because the auctioneer re-scores bids on *global* welfare, a logistics agent cannot win by
  optimizing its own utilization. Medical additionally raises one mass-casualty escalation per
  overload episode (not one per tick — the over-escalation failure mode is mitigated).

### Routing

- **May:** plan risk-aware routes from its **belief** network (updated from the COP river).
- **Never:** read the true flood directly (that would erase partial observability); override a
  human or commit an asset. It *advises*; the engine validates every route against the true
  flood at arrival and strands a mis-routed asset rather than teleporting it.

### Coordinator (supervisor)

- **May:** award the welfare-maximizing feasible bid; route high-stakes commitments to the HITL
  gate; **atomically commit** (the single no-double-commit chokepoint); **roll back** a still-
  reversible award (lease reclaim, equity guardrail); escalate genuinely unreachable demand.

- **Never:** commit a gated action the human denied; reclaim an asset that is already on-scene
  (irreversible work is never yanked); commit without the atomic guard.

### Human approver (HITL)

- **May:** approve or **deny** a high-stakes commitment from a structured decision package
  (context, options, recommendation, risk) — *not* raw agent chatter.

- **Policy (deterministic stand-in):** withhold the last unit of a genuinely scarce type (the
  two medical teams) from an incident below HIGH priority — preserving a reserve for a possible
  critical call — and approve otherwise. A real deployment swaps in a person; the *gate and the
  decision package* are what is real in code.

### Auditor

- **May:** read everything and compute reports. **Never:** act. The thing being graded does not
  grade itself — scoring lives in a read-only observer.

---

## Cross-cutting invariants (enforced, tested)

1. **No double-commit.** An asset leaves `IDLE` exactly once; verified at full scenario scale
   by replaying the audit log (`tests/test_engine.py`).

2. **Idempotency.** Exact message re-delivery is dropped at the bus (by `message_id`); distinct
   citizen reports of the same event are *fused* at the blackboard (by content key) with a
   corroboration count.

3. **Leases / freshness.** Observations go stale after `lease_ttl_ticks`; a committed asset
   whose owner goes dark has its lease reclaimed.

4. **Least privilege, enforced.** Boundaries are not merely documented here — they are checked
   in code: every cross-boundary tool/work call (MCP, A2A) is checked against the caller's
   scopes/trust, and **every COP write is gated at the bus against the sender's `cop:write`
   scope** (an under-scoped write is refused and audited as `scope_denied`). Internal actions are
   additionally bounded *structurally* — an agent has no method to do what its role forbids.

5. **Auditability.** Every state change is appended to an immutable event log with a `trace_id`
   that follows an incident end-to-end.

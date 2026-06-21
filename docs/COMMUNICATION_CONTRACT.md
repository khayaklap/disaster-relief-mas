# Communication Contract

Coordination is only as good as the messages that carry it. This document specifies the wire
contract — the envelope, the message types, routing, shared-state semantics, and the failure
modes the contract defends against. It is implemented and validated in
[`messages.py`](../src/bayanihan_net/messages.py) (Pydantic, `extra="forbid"`) and exercised by
[`tests/test_messages.py`](../tests/test_messages.py).

---

## The envelope

Every inter-agent message is a versioned `Envelope` with routable/auditable **header** fields and
a typed **payload**. The header is what makes coordination *inspectable*: routing, deduplication,
escalation, and the audit trail read these fields, never free text.

| Field | Purpose |
|---|---|
| `schema_version` | contract version (`"v1"`) — forward-compatibility |
| `trace_id` | one id per **incident lifecycle** → end-to-end tracing across hops |
| `message_id` | deterministic id `agent:tick:seq:type` (no `uuid4` → reproducible) |
| `correlation_id` | links a reply to its request (bid→CFP, decision→request) |
| `idempotency_key` | duplicate-suppression key; **content key** for citizen reports |
| `agent_id` / `recipient` | sender / addressee (`None` = broadcast on the blackboard) |
| `msg_type` | the protocol verb (closed enum) |
| `priority` | `critical`/`high`/`medium`/`low` — routing order, drop protection |
| `tick` / `deadline_tick` | when created / when the work becomes moot |
| `security_context` | identity + capability scopes (the trust basis at boundaries) |
| `payload` | typed body, validated against a per-`msg_type` registry |

**Reproducibility by construction.** `message_id` is a deterministic string from
`(agent_id, tick, seq, msg_type)`; there is no randomness on the wire, so a given seed replays
the exact message sequence.

**Strictness by construction.** Each payload model is `extra="forbid"`: an unknown field is a
protocol violation that fails fast at the boundary, not silent drift. `Envelope.typed_payload()`
validates the body against the registry for its `msg_type`, and the registry is *closed* — a
test asserts every `MsgType` has a payload model.

---

## The verbs (message types)

```text
OBSERVATION_POSTED   forecast → COP (river; road/sighting kinds are schema-supported, not yet emitted)
INCIDENT_REPORT      scout → COP (deduplicated by content key)
TASK_ANNOUNCED       triage → bidders (contract-net call-for-proposals)
BID                  logistics/medical → coordinator (feasible asset proposal)
AWARD                coordinator → winner (with the global welfare score)
ASSET_COMMITTED      the atomic commitment record
ROUTE_COMPUTED       routing result for a committed asset
APPROVAL_REQUESTED   coordinator → human (a decision package)
APPROVAL_DECISION    human → coordinator (approve/deny + reason)
ESCALATION_RAISED    medical/coordinator → command (mass-casualty, unreachable)
ROLLBACK_ISSUED      coordinator (reverse a reversible award)
STATUS_UPDATE        general state change
```

## Routing

- **Pub/sub on the blackboard** for observations and incident reports (broadcast; `recipient`
  is `None`).

- **Directed contract-net** for allocation: `TASK_ANNOUNCED` → `BID` (correlated by `task_id`) →
  `AWARD`.

- **Dedicated escalation channel** (`ESCALATION_RAISED`, `APPROVAL_REQUESTED`/`_DECISION`) to the
  coordinator and the human. Escalations and approval *requests* are raised at `critical` priority; the
  whole approval loop travels the no-drop delivery path, so it is never lost to comms degradation
  (the `APPROVAL_DECISION` reply rides that protected path at default priority).

## Shared state semantics (the blackboard)

- **Idempotency, two layers.** The bus drops an *exact* re-delivery (same `message_id`) —
  transport idempotency. The blackboard *fuses* distinct citizen reports that share a **content
  key** (`node:type:time-window`) onto one COP incident, keeping a corroboration count and the
  worst-case headcount. The two are deliberately separate: a retried packet must not double-apply,
  but two residents reporting the same flood *should* corroborate.

- **Leases / freshness.** River/road observations carry a posted tick and go **stale** after
  `lease_ttl_ticks`; a committed asset holds a renewable lease so a resource whose owning agent
  goes dark is reclaimed rather than stranded.

- **Atomic commitment.** `try_commit` moves an asset out of `IDLE` exactly once — the single
  guarantee behind *no double-commit*, verified at scenario scale by replaying the audit log.

- **Ordered audit log.** Every state change appends to an immutable event list with its tick and
  `trace_id` — the spine the auditor and the evaluation read.

## Failure modes handled (the MAS failure catalogue)

The MAS deck gives a six-row catalogue of distributed-systems failures that "return in agent
clothing" (deadlock, live-lock, message storm, clock drift, duplicate work, stale shared state),
each with a prescribed mitigation. We address every one, plus the two domain threats (lossy comms,
Sybil):

| Deck failure mode | Deck's prescribed mitigation | In this system |
|---|---|---|
| **Deadlock** | TTLs + transaction idempotency | task deadlines; idle-only bidding; lease-TTL reclaim |
| **Live-lock** | exponential backoff + jitter | structurally avoided — commitments stand (idle-only bidding; an asset is released only by lease expiry or disable), so there is no rapid re-grab loop to back off from |
| **Message storm** | rate limits + circuit breakers | content-key dedup + transport idempotency absorb the flood; `report_storm` stress keeps service near baseline (a dedup-as-rate-limiter) |
| **Clock drift** | vector clocks / Lamport timestamps | N/A under one simulation clock — the monotonic `tick` *is* the Lamport timestamp every message carries; ordering is total by construction |
| **Duplicate work** | idempotency keys | idempotency keys → no duplicate dispatch (tested) |
| **Stale shared state** | leases + freshness checks | observation leases + freshness; routes validated against the *true* flood at arrival (mis-route → strand, not teleport) |
| *Lossy comms* (domain) | — | optional drop probability; *critical* traffic (awards, rollbacks, escalations) never dropped; drops logged, never silent |
| *Sybil / false reports* (domain) | — | sensing plausibility heuristic + triage suppression of uncorroborated suspects; `cop:write` scope gate on the bus |

## Security context

Identity and authorization travel with every message: the owning agency, the agent's role, its
capability **scopes**, and an (opaque, mocked) capability token. This is the basis of trust at the
A2A / MCP boundaries — *who is asking, on whose behalf, and what they are scoped to do* — checked
by [`governance/policy.is_authorized`](../src/bayanihan_net/governance/policy.py).

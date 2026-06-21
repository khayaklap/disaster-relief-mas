# Safety & Governance

Disaster response is safety-critical: decisions are irreversible, communities can be left behind,
and a confidently-wrong autonomous action can kill. This system's stance is therefore explicit:
**safety constraints are enforced, not learned; high-stakes actions are human-gated; everything is
auditable; and the learned component is sandboxed and advisory.**

---

## 1. Human-in-the-loop (HITL)

**What is gated.** A commitment is routed to the human when it is high-stakes by an *explicit,
auditable* rule ([`governance/policy.evaluate_commit_gate`](../src/bayanihan_net/governance/policy.py)),
never by a learned or free-text judgement:

- **last unit of a scarce type** — committing the last ready unit of a genuinely small fleet (the
  two medical teams), which depletes the reserve;

- **large incident** — committing to an incident with ≥ `large_commit_people` at risk.

A third gate reason — **forced evacuation** at 3rd-alarm river level — is defined and unit-tested in
`evaluate_commit_gate`, but the current run loop only issues `commit_asset` actions, so it is **not
raised at runtime**; it is kept (and flagged here, like the unwired `should_auto_rollback` hook) for the
evacuation-order action a real deployment would add. The two reasons above are the ones that fire in a run.

The gate is fleet-size aware on purpose: it protects the scarce medical reserve but does **not** page
a human for every busy-boat commit (boats are plentiful) — calibrated to avoid the over-escalation
failure mode.

**The decision package, not chatter.** The human receives a structured package — *context* (the
incident), *recommendation* (the welfare-winning asset, its ETA and score), *options* (runner-up
bids), and *risk* (the gate reasons) — exactly as in
[`evidence/decision_package.json`](../evidence/decision_package.json). This is the deck's HITL
guidance and echoes Assignment 1's decision packet.

**The approver's policy (deterministic stand-in).** For a reproducible simulation the "human" is a
scripted duty-officer policy ([`coordination/escalation.HumanApprover`](../src/bayanihan_net/coordination/escalation.py)):
withhold the last unit of a scarce type from an incident below HIGH priority (preserve a reserve for a
possible critical call), approve otherwise. A real deployment swaps in a UI for a person — the *gate
and the decision contract* are what is real in code. In a worked run the duty officer typically
approves ~12 large-commit requests and denies ~5–7 last-reserve requests.

---

## 2. Rollback

The coordinator can **reverse a still-reversible award** — releasing an asset and reverting its
incident to `OPEN` for re-auction ([`coordinator.rollback_award`](../src/bayanihan_net/agents/coordinator.py)).
Reversibility is a first-class lifecycle property:

- An award is reversible while the asset is `EN_ROUTE`; rollback is permitted.
- Once the asset is `ON_SCENE`, the work is **irreversible** and rollback leaves it untouched — you
  never yank a crew mid-rescue.

Runtime rollback triggers: **lease reclaim** — an asset whose owning agent went dark is released and
its incident re-auctioned, emitting a `ROLLBACK_ISSUED` record (the live trigger). A **stranded
mis-route** (a planned route that fails against the true flood at arrival) also releases the asset, via
`release_asset` rather than a logged rollback. A third trigger — an **equity guardrail** that would roll
back when projected unmet-need inequity crosses a Gini threshold
([`escalation.should_auto_rollback`](../src/bayanihan_net/coordination/escalation.py)) — exists as a
**tested pure hook but is not wired into the runtime** (no Gini-triggered rollback fires during a run);
it is flagged here honestly rather than presented as live. Every rollback that does fire is logged.

---

## 3. Audit & observability

Every state change appends to an **immutable event log** with its tick and a `trace_id` that follows
an incident end-to-end — including the MCP and A2A boundary hops. The auditor
([`agents/auditor.py`](../src/bayanihan_net/agents/auditor.py)) is **read-only**: the thing being graded
does not grade itself. The MAS **golden signals** (throughput, latency, errors, saturation, cost,
safety) plus the emergence metrics (coverage Gini, reassignment rate, load-spread entropy) are sampled
every tick into `evidence/scenario_report.json` and visualized in `figures/response_timeline.png`.

---

## 4. Abuse & failure cases (red-team)

| Case | Threat | Defense | Evidence |
|---|---|---|---|
| **Sybil / false reports** | fabricated demand wastes scarce assets | plausibility heuristic + suppression of uncorroborated suspects | `sybil_injection` stress run |
| **Report storm** | message flood melts coordination | content-key dedup + transport idempotency | `report_storm` (service near baseline) |
| **Comms degradation** | dropped messages blind the system | critical traffic never dropped; drops logged | `comms_blackout` |
| **Asset / agent loss** | command of a unit is lost | lease reclaim + re-auction; graceful degradation | `agent_kill` |
| **Stale situational picture** | acting on an out-of-date flood | leases + arrival-time validation vs. true flood | `forecast_outage` |
| **Unauthorized COP write** | a spoofed / compromised sender writes to the shared picture | the bus gates every COP write on the sender's `cop:write` scope; refused and audited as `scope_denied` | `test_bus_enforces_cop_write_scope` |
| **Compound failure** | several at once | all of the above compose | `compound_crisis` |

All six stress scenarios (plus the baseline reference) satisfy the Tier-A invariants (no
double-commit, serve some, re-stabilize).

---

## 5. The MARL safety boundary

The reinforcement-learning component ([MARL_BRIDGE.md](MARL_BRIDGE.md)) is **deliberately outside the
live safety loop**:

- It learns **offline** on a sandboxed routing MDP, never on the live system.
- Its output is **advisory** — a route *recommendation* — with the deterministic, auditable heuristic
  always the fallback and the authority. *RL informs; code and the human decide.*

- Safety constraints (passability, the HITL gate, no-double-commit) are **enforced by the control
  plane**, not learned — a learned policy cannot violate them because it never holds the commit
  authority.

This is the deck's stance made concrete: *most business multi-agent systems should start without MARL
and earn it*; here MARL earns a bounded, offline, advisory role on exactly the sub-problem where it
helps, and nowhere near the irreversible decisions.

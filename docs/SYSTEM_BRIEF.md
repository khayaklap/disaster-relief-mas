# System Brief — Marikina flood response

## The use case

When a typhoon stalls over Metro Manila, the **Marikina River** rises through its official
alarm levels and the dense, low-lying barangays along it flood within hours. Streets become
impassable to vehicles on a timeline; residents are stranded on rooftops; the injured and the
elderly need evacuation and medical care; relief goods must reach shelters. The **Marikina City
Disaster Risk Reduction and Management Office (CDRRMC)** must allocate a small fleet of rescue
boats, transport trucks, and medical teams across many simultaneous, evolving incidents — some
of which are unreachable at the flood peak, some of which are irreversible once begun.

This is the use case the student finds most relevant while interning in the Philippines, a
country among the world's most exposed to typhoons, floods, landslides, earthquakes, and
volcanic hazards. It is chosen deliberately because it is **operationally real, multi-agency,
resource-constrained, time-critical, and safety-critical** — the conditions under which a
multi-agent system has to earn its keep.

> **Illustrative, not authoritative.** Every number in the scenario — alarm thresholds, barangay
> populations, the road graph, the fleet — is a plausible stand-in, *not* an official PAGASA /
> MMDA / Marikina LGU figure. The point is to make realistic edge cases fall out, not to model
> the city to spec. See [`config.py`](../src/bayanihan_net/config.py).

## Stakeholders

| Stakeholder | Stake | In the model |
|---|---|---|
| **Affected residents** (≈5,100 at risk across 6 barangays) | survival, timely rescue, equitable treatment | incident demand; the *severity-weighted served* and *worst-served barangay* metrics |
| **CDRRMC command center** | allocate scarce assets, maintain a common picture, stay accountable | coordinator + blackboard + audit log |
| **Field responders** (boat/truck crews, medical teams) | clear tasking, safe routes, not being double-committed | logistics/medical agents + atomic commitment + risk-aware routing |
| **Duty officer (human)** | authority over irreversible / high-stakes decisions | the HITL approval gate |
| **Partner agencies** (Quezon City CDRRMC, DSWD, Red Cross, NDRRMC) | mutual aid across organizational boundaries | the A2A interoperability boundary |
| **Upstream data providers** (PAGASA, MMDA, DOH hospitals) | scoped, auditable access to their feeds | the MCP tool boundary |

## The objective

**Minimize total severity-weighted unmet need, equitably and safely, under hard asset scarcity.**
Concretely, the system is judged on four levels (see [EVALUATION_PLAN.md](EVALUATION_PLAN.md)):

- **System:** severity-weighted people served, coverage of the *worst-served* barangay, SLA
  compliance (dispatch within a priority-dependent deadline), and the equity of coverage.

- **Interaction:** no double-commit, awards trace to valid bids, escalation fires when required.
- **Agent:** routing feasibility, bid validity, triage dedup correctness.
- **Human:** approval load, denial behaviour, escalation rate/count.

## Why the stakes make this hard

- **Scarcity is binding.** Nine assets cannot serve thousands of people in a 12-hour window.
  The system is *capacity-saturated by construction* — the realistic regime — so the job is to
  serve the *right* incidents in the *right* order, not to serve everyone.

- **Irreversibility.** Once a boat is on-scene, the commitment cannot be cleanly undone. Some
  actions (committing the last medical team, ordering a forced evacuation) are grave enough to
  require a human.

- **Partial observability and degradation.** No single agent sees the whole flood; a typhoon
  degrades power and comms and can knock out the command center or the river gauge. A single
  point of control is a single point of failure — which, in disaster response, is fatal.

- **Adversarial and noisy inputs.** Citizen reports duplicate, lag, and can be fabricated
  (Sybil). The system must fuse and filter them without dispatching twice or chasing ghosts.

These stakes are exactly why a *single* agent is insufficient and why coordination, governance,
and honest evaluation — not raw model capability — are the deliverable. The argument is made in
[DESIGN.md](DESIGN.md); the evidence that the system meets the stakes (and where it does not) is
in [EVALUATION_PLAN.md](EVALUATION_PLAN.md) and [REFLECTION.md](REFLECTION.md).

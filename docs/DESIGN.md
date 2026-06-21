# Design — bayanihan-net

This document defends the design end to end: why a multi-agent system, the agent roster, the
coordination mechanism (and why *this* one), the incentive structure, the emergent behaviours
we expect and guard against, and the interoperability boundaries. It maps each decision to the
course's multi-agent-systems material and to the code that implements it.

---

## 1. Why a multi-agent system (single agent insufficient)

A single agent — one model with one context and one tool-loop — is the wrong shape for this
problem for five concrete, non-rhetorical reasons:

1. **Partial observability.** No single vantage point sees the whole flood. Field scouts see
   their barangays; the river gauge sees the hydrograph; citizen reports see scattered
   incidents. The truth is *assembled* from slices. In code, each scout covers a disjoint node
   set ([`agents/sensing.py`](../src/bayanihan_net/agents/sensing.py)) and the routing agent
   acts on a *belief* of the network, not the true flood — partial observability is structural,
   not cosmetic.

2. **Genuinely specialized expertise.** Triage, medical mass-casualty judgement, asset
   logistics, route planning under flooding, and hydrology are different competencies with
   different state and different tools. Collapsing them into one prompt loses the separation of
   concerns that makes each testable.

3. **Parallel decomposition.** Many incidents across many barangays are concurrent. A single
   agent serializes; specialized agents bid and act in parallel within a tick.

4. **Resilience / graceful degradation.** A typhoon degrades power and comms and can knock out
   the command center or the river gauge. A single point of control is a single point of
   failure — fatal in disaster response. The hybrid degrades: lose the forecast feed and the
   COP goes stale but routing falls back on its last belief; lose assets and leases are
   reclaimed and work re-auctioned (demonstrated in the `agent_kill` / `forecast_outage` stress
   scenarios).

5. **Organizational alignment.** Real response *is* multi-agency — barangay → city → NDRRMC,
   plus DSWD relief, health, rescue, NGOs. The system's structure should mirror the
   institutions it coordinates, which is also where the A2A boundary lives (§6).

This is the course deck's "honest reasons for MAS" — we adopt MAS because the *problem* is
distributed, specialized, concurrent, failure-prone, and inter-organizational, not because MAS
is fashionable.

---

## 2. The agent roster

The full contract — roles, tools, permissions, never-dos, escalation, state — is in
[AGENTS.md](../AGENTS.md). In brief: **sensing scouts** and a **hydromet/forecast** agent
populate the COP; **triage** prioritizes and dedups; **logistics** and **medical** agents own
and bid scarce assets; a **routing** agent plans risk-aware routes on a belief network; a
**coordinator** supervises the auction, runs the HITL gate, commits atomically, and rolls back;
a **human approver** holds authority over irreversible actions; an **auditor** observes and
scores. Permissions are capability *scopes* on each agent's `SecurityContext`, checked by
[`governance/policy.py`](../src/bayanihan_net/governance/policy.py).

---

## 3. Coordination — a defended hybrid

The mechanism is **hybrid**: a blackboard for shared awareness, contract-net for scarce-asset
allocation, and a supervisor + HITL for arbitration and accountable escalation. Each leg is the
*right primitive* for one sub-problem, and the combination is what the course's
conflict-resolution map points to.

### 3a. Blackboard = the Common Operating Picture

Sensing and forecast agents post observations; everyone reads. A blackboard is the correct
primitive for **a shared situational truth that no single agent owns**, and it balances
global-truth against local-autonomy. Ours ([`blackboard.py`](../src/bayanihan_net/blackboard.py))
is not a passive store — it enforces **idempotency** (duplicate suppression + corroboration
fusion), **leases/freshness** (stale-COP guard), and **atomic commitment** (the single
no-double-commit chokepoint), with an append-only audit log.

### 3b. Contract-net = scarce-asset allocation

A prioritized incident becomes a *call-for-proposals*; capable agents **bid** their feasible
idle assets (ETA, capacity, route risk); the coordinator **awards**. Contract-net is the
textbook fit for **heterogeneous assets with different costs and availabilities** — exactly
boats vs. trucks vs. medical teams with different reach under flooding. See
[`coordination/contract_net.py`](../src/bayanihan_net/coordination/contract_net.py).

### 3c. Supervisor + HITL = arbitration and escalation

The coordinator resolves conflicts, and **irreversible / high-stakes actions are human-gated**
through a decision package. This is the deck's conflict-resolution ladder: policy rule →
negotiable? → arbitrator → high-risk? → human escalation.

### 3d. Why not a pure mechanism

We considered each option on the course's coordination menu (supervisor, market, contract-net,
consensus, blackboard, hybrid) and rejected every *pure* one:

- **Pure supervisor** = a single point of failure (fatal here) and a bottleneck.
- **Pure market / pure contract-net** = efficient allocation but *no shared situational
  awareness* and no accountable escalation path — a market alone has nowhere to post the COP and
  no one to gate the irreversible call.
- **Pure consensus** = too slow and too expensive for time-critical, life-safety decisions
  ("consensus is expensive; use it only where shared truth matters").
- **Pure blackboard** = shared awareness but *no decision-maker* — a COP that no one acts on; it
  needs an allocator (the auction) and an arbiter (the supervisor) bolted on, which is exactly
  the hybrid.

The hybrid composes the three primitives that each solve one sub-problem — blackboard for shared
awareness, contract-net for scarce-asset allocation, supervisor+HITL for accountable
arbitration — giving shared awareness *and* efficient allocation *and* accountable escalation,
and it **degrades gracefully**. The defensible, course-aligned answer.

### 3e. What the evidence says about coordination

Across 12 paired seeds ([EVALUATION_PLAN.md](EVALUATION_PLAN.md)), the one paired-significant service
result is that the hybrid **outserves a random allocator** (severity-weighted +0.0125 ★, raw served
+0.0136 ★): coordination beats no coordination. Against the other *reasonable* policies — greedy-nearest
selection, FIFO or fairness ordering — every delta is within the 2-SE screen. Under hard capacity
saturation the binding constraint is reachability and capacity, not the allocation rule, so the sensible
policies are statistically indistinguishable on service. The coordination value we can defend here is
therefore **a clear edge over no coordination** plus the safety, governance, and graceful-degradation
behaviour the stress battery exercises — *not* a significant separation between heuristics. We take the
capacity-bound regime seriously in §5 and [REFLECTION.md](REFLECTION.md).

---

## 4. Incentives — cooperation, competition, local vs. global

The incentive structure deliberately mixes **competition** and **cooperation**. Agents *compete*
in the contract-net auction — each logistics agent bids its assets against the others, which is
what surfaces the best option for each incident. But the competition is **channeled toward
cooperation** by *how the winner is chosen*: the coordinator scores bids on a shared global
objective, so winning a bid and advancing the collective good are the *same* thing. Competition
discovers options; the scoring rule aligns it with cooperation.

The central tension underneath is **local vs. global**: each logistics agent *locally* wants to
maximize its own asset utilization and minimize its own asset's risk; the **global** objective is
to minimize total severity-weighted unmet harm, equitably. The course's alignment lesson is
blunt — *agents optimize what you make visible; if local KPIs conflict with global values, the
local agents win, every time.*

We resolve it structurally: **the bidder does not choose the winner.** A logistics agent reports
a selfish `local_cost`, but the coordinator ignores it for ranking and instead maximizes a
**global social-welfare score** — severity-weighted people served per unit time-to-arrival,
discounted by flood risk ([`score_bid`](../src/bayanihan_net/coordination/contract_net.py)). Because
the auctioneer scores globally, a logistics agent **cannot hoard or cherry-pick easy work** to
flatter its own utilization; the only way to win is to be the globally-best option for the
incident. Precisely, this implements the **welfare-maximizing allocation rule** at the heart of a
VCG auction — *not* the full VCG mechanism, because there are no transfer payments (public rescue
agents share one objective and no currency, so incentive-compatible pricing is moot). We borrow
VCG's *allocative efficiency*, not its payments; the honest framing is "welfare-maximizing
allocation," with VCG/Shapley as the conceptual lineage.

Named incentive failure modes — the MAS deck's six (s79), each mapped to our mitigation (plus the
two domain-specific ones). The deck's toolkit (Shapley, VCG, reputation, commit-reveal) is the
menu we draw from:

| Deck incentive failure mode | Deck's mitigation | In this system |
|---|---|---|
| **Free-rider** | quota + reputation | only *feasible* bids score (infeasible = −∞); an idle asset that never bids wins nothing |
| **Collusion** | random audit agent + penalties | the read-only **auditor** + the red-team stress battery are the oversight; the sealed single-round auction gives no repeated channel to collude over |
| **Resource hog** | dynamic pricing | the welfare score already prices capacity by *marginal* harm reduction (severity-weighted served per asset-hour), so grabbing more than you can use lowers your score |
| **Front-running** | commit-reveal | N/A — bids are scored by a neutral auctioneer in one round with no public order book to front-run |
| **Local optimization** | global reward + arbitration | the signature case: **global welfare scoring + coordinator arbitration** (a bidder cannot win on local utilization) |
| **Hidden harm** | oversight agent + red-team scenarios | the auditor's coverage-Gini / worst-served metrics surface neglected barangays; the red-team battery (six stress injections + a baseline) probes for it |
| *Hoarding the last reserve* (domain) | — | HITL gate on the last unit of a scarce type |
| *Triage starvation* (domain) | — | severity-first ordering |

**Honest note on the fairness term.** We also built an *equity* incentive — re-ordering the
queue toward under-served barangays. Measured across 12 seeds it **does not help**: it is
directionally worse on worst-served coverage under saturation, though within the 2-SE screen so we
claim no significance (§5, EVALUATION_PLAN). We keep it as an evaluated, documented negative rather
than quietly deleting it, and default to the globally-aligned welfare scoring.

---

## 5. Emergence — named, with detection metrics and mitigations

Emergence is treated as an *engineering* concern: name the behaviour, give it a metric, give it
a guardrail. Metrics live in [`governance/metrics.py`](../src/bayanihan_net/governance/metrics.py).

**Useful (designed-for) emergence.** These are behaviours the design *enables*; of the three, only
load spreading carries a number in the worked run. We flag the other two as mechanisms rather than
measured surprises — keeping the emergence claims honest, since the *unwanted* behaviours below each
carry a detector.

- *Bayanihan effect:* efficient self-organized division of labour across specialized agents
  with no central script — the auction produces a sensible asset-to-incident matching.
- *Adaptive routing:* every dispatch is planned on the routing agent's current flood belief, so as the
  COP updates the chosen paths track the changing flood. A designed capability — the baseline worked run
  logged no mid-route re-routing, so we do not claim it as an *observed* emergent effect.
- *Load spreading:* served activity spreads across barangays (load-spread entropy ≈ 0.95+ — the one
  useful behaviour with a metric).

**Unwanted emergence — each with a detector and a mitigation.** Detectors are drawn from the MAS
deck's emergence-metric catalogue (s39: clustering coefficient, *Shannon entropy of actions*,
global reward variance, *spectral radius* → oscillation risk, average path length, *Gini
coefficient*). We implement the three that bind on this problem — **Gini**, **Shannon entropy**,
and an oscillation proxy in the spirit of the deck's spectral-radius signal — per the rule "if you
cannot measure emergence, you cannot responsibly tune it."

| Emergent hazard | Detector (deck metric) | Mitigation |
|---|---|---|
| Asset oscillation / thrashing | reassignment rate — our proxy for the deck's **spectral radius** (oscillation risk) | commitment hysteresis: idle-only bidding (a committed asset can't be poached) + lease TTL (released only on lapse/disable) |
| Triage inequity / starvation | **Gini coefficient** of unmet coverage; worst-served fraction | severity-first ordering; (fairness lever evaluated, see §4) |
| Report-storm amplification | message volume; **Shannon entropy** of per-barangay activity (load spread) | content-key dedup at the COP + transport idempotency at the bus |
| Stale-COP herding / cascade | observation freshness (lease age) — the deck's stale-state signal | leases + arrival-time validation against the *true* flood (strand, don't teleport) |
| Over-escalation to the human | escalation rate | edge-triggered escalations (one per overload episode), deadline-gated no-asset escalations |

Observed reassignment rate is ~0 at baseline (no thrashing), and the report-storm stress
scenario keeps service near baseline because dedup absorbs the duplicate flood — the guardrails
work.

---

## 6. Interoperability — A2A and MCP boundaries

Two distinct boundaries, both *real in code* with mocked upstreams ([`interop/`](../src/bayanihan_net/interop/)):

- **MCP (scoped tool/context).** The forecast agent sources the river reading through the PAGASA
  river-gauge adapter — the **live** MCP hop, invoked every tick (48 per run) with a `trace_id`
  propagated through it. Two further adapters (MMDA road closures, DOH hospital beds) are
  **registered and scope-declared at the same boundary** — demonstrating the discovery surface —
  but are not invoked in the current run loop. Every adapter declares its name, parameters, and
  the capability **scope** it requires, checked against the caller's `SecurityContext` before it
  runs; an under-scoped caller is refused (tested). This is the governed adapter a tool-using
  agent sits behind — the part that matters for safety — not an LLM tool-call layer.

- **A2A (cross-org work exchange).** When the medical agent declares a mass-casualty overload,
  the coordinator requests **medical mutual aid** from a neighbouring agency (Quezon City
  CDRRMC) over an A2A boundary that carries a typed task, a **local access policy owned by the
  receiving agency** (it decides whom it trusts and what it offers), **idempotency** (a retried
  request is not double-served), partial-progress/terminal states, and trace propagation. The
  partner systems are mocked; the contract and the trust decision are real.

Both are wired into the live run: a worked run logs 48 traced MCP calls and one idempotent A2A
mutual-aid request, completed by the partner agency.

### 6a. Why the live loop uses no LLM — and where one *is* used (the advisory perception layer)

The live coordination loop is **deliberately deterministic and uses no OpenAI Agents SDK**. This is
the correct, defended choice for this problem, not an omission: the decisions are irreversible and
life-safety-critical, and the deck's own guidance is *"governance requires deterministic decisions"*
(MAS s88) and *"start simple… earn the complexity."* A non-deterministic model choosing which
barangay gets the last boat is exactly what you do not want; determinism is also what makes the whole
evidence story — byte-identical, no-API-key, seed-reproducible — hold. So the SDK's *discipline*
(typed contracts, guardrails, approvals, tracing, structured outputs) is applied **natively** in the
loop (see [COURSE_ALIGNMENT.md, Deck 2](COURSE_ALIGNMENT.md)), and the SDK *runtime* is not needed
where there is no model to orchestrate.

There is, however, one place a real LLM genuinely helps and cannot cause harm: the **perception
edge**. Real disaster reports arrive as free text (hotline calls, SMS, social posts), not typed
records. The opt-in advisory layer ([`perception/`](../src/bayanihan_net/perception/), enabled with
`--llm-advisory`) renders each structured report back to realistic free text, has an Agents-SDK agent
extract the typed facts a citizen states (headcount + type), and **re-validates that extraction
against the deterministic ground truth** with an output guardrail (`evaluate_extraction`). A passing
extraction equals the truth (the run is unchanged); a failing one — a hallucinated headcount, a wrong
type — trips the guardrail and **falls back** to the deterministic facts, logged as evidence. The LLM
is therefore *observed and verified, never trusted*: it can never move a safety-relevant value, by
construction. It is **opt-in and offline-by-default** (no key ⇒ deterministic passthrough), so the
default pipeline imports no LLM library and is byte-identical. This mirrors the MARL safety boundary
(*RL informs; code and the human decide*, [SAFETY_GOVERNANCE.md §5](SAFETY_GOVERNANCE.md)) and is the
faithful Assignment-1 pattern — typed output + guardrail-against-truth + fallback + tracing — applied
at the one edge where a model adds value without touching commit authority.

---

## 7. Operations

Observability, evaluation, rollback, and HITL are first-class; they have their own documents:
[EVALUATION_PLAN.md](EVALUATION_PLAN.md) (four-level metrics, baselines, stress battery,
methodology) and [SAFETY_GOVERNANCE.md](SAFETY_GOVERNANCE.md) (HITL, audit, rollback, abuse
cases, the MARL safety boundary). The MAS golden signals (throughput, latency, errors,
saturation, cost, safety) are sampled every tick into the run's golden-signal log.

---

## 8. Deck-citation crosswalk

For the full, slide-numbered mapping across **all five decks** (Intro, Agents SDK, Harness
Engineering, Deep RL, MAS) — including the eight MAS design principles, the SDK pattern mapping, the
harness litmus tests, and an honest "where we extend beyond the decks" section — see
[COURSE_ALIGNMENT.md](COURSE_ALIGNMENT.md). The MAS-deck essentials:

| Course concept | Where realized |
|---|---|
| Honest reasons for MAS | §1 |
| Blackboard channel | §3a, `blackboard.py` |
| Contract-net protocol | §3b, `contract_net.py` |
| Conflict-resolution ladder → human escalation | §3c, `coordinator.py` + `escalation.py` |
| Incentive alignment (local vs global, VCG/Shapley nod) | §4, `score_bid` |
| Emergence: detect + mitigate | §5, `metrics.py` |
| Communication contract (typed envelope, idempotency, deadlines) | [COMMUNICATION_CONTRACT.md](COMMUNICATION_CONTRACT.md), `messages.py` |
| Failure modes (deadlock, storm, duplicates, stale state) | §5 + COMMUNICATION_CONTRACT |
| A2A vs MCP boundaries | §6, `interop/` |
| MAS golden signals + four-level evaluation | EVALUATION_PLAN, `metrics.py` |
| HITL taxonomy + decision package | SAFETY_GOVERNANCE, `policy.build_decision_package` |
| MARL appropriateness + CTDE | [MARL_BRIDGE.md](MARL_BRIDGE.md), `rl/` |

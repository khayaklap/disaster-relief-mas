# Course-Content Alignment

This document validates, deck by deck, that `bayanihan-net` *applies* the course's material rather
than name-dropping it — and is honest about where it **extends beyond** or **deliberately diverges
from** the decks. It covers all five: *Intro to Agentic AI*, *OpenAI Agents SDK*, *Harness
Engineering*, *Deep RL for Agentic AI*, and *Building Multi-Agent Systems*. Slide numbers (`sN`) cite
the deck where a concept appears.

A framing note up front, stated honestly: the agents in the **live coordination loop** are
**deterministic decision units, not LLM agents**. That is itself the course's guidance — the MAS
deck's *"governance requires deterministic decisions"* (s88) and the intro deck's *"start simple…
earn the complexity."* The agentic-design *discipline* (typed contracts, memory policy, guardrails,
traces, evals, HITL) applies fully and is what we demonstrate; a non-deterministic model is simply
not what you want choosing which barangay gets the last boat. A real LLM **is** used in exactly one
place — the opt-in, offline-by-default **advisory perception layer** (`--llm-advisory`,
[`perception/`](../src/bayanihan_net/perception/)) — where it is bounded by an output guardrail so it
can never move a safety-relevant value. See the Deck 2 section below for where, and why, the SDK
discipline is applied natively in the loop and the SDK itself only at that advisory edge.

---

## Deck 5 — Building Multi-Agent Systems (the backbone)

| Deck concept | Where applied | Faithfulness |
|---|---|---|
| **Coordination spectrum** (scheduler→hierarchy→market→gossip) chosen on *"the cost of being wrong"* (s65) | DESIGN §3 picks **hybrid**; rejects each pure option on cost-of-being-wrong grounds | exact criterion cited |
| **Contract-net** 5-step auction (s67) | `coordination/contract_net.py`: announce → bid → score → award → execute | textbook |
| **Communication envelope fields** (s53–54): `trace_id, agent_id, msg_type, deadline, schema_version, idempotency_key, correlation_id, security_context, priority, message_id` | `messages.py` `Envelope` carries **all** of them | complete |
| **Communication failure catalogue** (s59): deadlock / live-lock / message storm / clock drift / duplicate / stale state | COMMUNICATION_CONTRACT table maps **all six** to a mitigation | complete (clock-drift = the monotonic tick = Lamport stamp) |
| **Incentive failure modes** (s79) + toolkit (s78: Shapley/VCG/reputation/commit-reveal) | DESIGN §4 table maps all six; welfare scoring = VCG *allocation rule* (precisely scoped, no payments) | complete + precise |
| **Local-vs-global** — *"if local KPIs conflict with global values, local agents win, every time"* (s114) | the bidder cannot win on local cost; the auctioneer scores global welfare | the signature lesson, implemented |
| **Emergence metrics** (s39): Gini, Shannon entropy, spectral radius (oscillation)… | `governance/metrics.py`: Gini + Shannon entropy implemented; reassignment-rate as the spectral-radius/oscillation proxy | 3 of 6 implemented, rest cited |
| **Golden signals** (s122): throughput, **latency**, errors, saturation, **cost**, safety | `TickSignals` emits all six (latency = `mean_response_ticks`, cost = `asset_ticks_cost`) | exact six |
| **Multi-layer evaluation** (s129): agent / interaction / system / … | four-level grid emitted in `evidence/scenario_report.json → evaluation.*` | all levels emit numbers |
| **HITL taxonomy** (s137–138): approval gate, override; *"context, not raw agent confusion"* | `governance/policy` gate + `HumanApprover`; decision **package** (context/options/recommendation/risk) | faithful |
| **Conflict-resolution decision tree** (s116): policy rule → negotiable → arbitrator → high-risk → human | coordinator arbitration + HITL escalation | faithful |
| **A2A vs MCP** — *"A2A moves tasks. MCP exposes tools and context. Trace and policy keep both honest"* (s97) | `interop/a2a.py` (work exchange) + `interop/mcp_tools.py` (scoped tools); `trace_id` on both hops | exact distinction |
| *"Policy belongs outside the prompt"* / OPA-style (s99) | `governance/policy.py` is pure, prompt-independent, scope/gate rules | faithful |
| **Red-team** (s45): kill 10% of agents, delay/drop messages, corrupt shared memory, Sybil, demand spike → *"re-stabilize within SLA"* | six stress scenarios (`agent_kill`, `comms_blackout`, `report_storm`, `sybil_injection`, `forecast_outage`, `compound_crisis`) + the `cop:write` scope gate vs. corrupt writes (a unit test, not a battery scenario); the Tier-A **re-stabilization** invariant | covers the list |
| **MARL appropriateness** (s88) + **CTDE** (s86) + 4 hardness problems (s85) | MARL_BRIDGE analysis; offline study uses parameter-sharing CTDE | the CTDE source (see Deck 4 note) |
| **Simulation before production** (s43–44) | the whole system is a seeded discrete-event simulation (SimPy-style) | faithful |

### The eight design principles (s112) — a grader checklist

| # | Deck principle | In this system |
|---|---|---|
| 1 | **Single-responsibility agents** | each agent has one job + a `Never` list (AGENTS.md) |
| 2 | **Explicit contracts** | Pydantic `Envelope` + typed payloads, `extra="forbid"` |
| 3 | **Fail fast** | payloads validate at the boundary; infeasible bids score −∞; misroutes strand, not teleport |
| 4 | **Externalize state** | the blackboard COP + event log; agents hold almost no private state |
| 5 | **Observability first** | golden signals every tick; immutable audit log; `trace_id` end-to-end |
| 6 | **Loose coupling** | pub/sub blackboard bus; agents never call each other directly |
| 7 | **Semantic versioning of agent APIs** | `schema_version = "v1"` on every envelope |
| 8 | **Security by design** | capability scopes on every agent; bus + interop scope/trust gates |

---

## Deck 1 — Intro to Agentic AI

| Deck concept | Where applied |
|---|---|
| **Agent = model + memory + tools + policies + evaluation + loop** (s7) | the system is exactly this minus a learned model: typed tools, the 4-type memory below, the pure policy layer, the eval harness, the engine loop |
| **`perceive → reason → act → learn`** (s19) | each tick: agents *perceive* the COP → *reason* (triage/bid/score) → *act* (typed messages) → the eval/REFLECTION loop is the *learn* |
| **Side-effects test** — *"if something irreversible can happen, you have a system"* (s8) | irreversible commits are HITL-gated; reversible ones can roll back |
| **Four-type memory** (working/episodic/semantic/procedural) + *"store less, expire fast"* (s36) | mapped explicitly in [AGENTS.md → Memory model](../AGENTS.md); leases enforce expiry |
| **`AGENTS.md` as the repo-level contract** (s34) | present, with roster, per-agent May/Never, memory, scopes, invariants |
| **Building-block checklist** (s39): objective, actions, memory policy, tool permissions, eval, stop, escalation | covered across `config.py` + AGENTS.md + the HITL escalation path |
| **Staged autonomy** (s95): offline → shadow → assistive → … | the RL policy is **offline & advisory**; the live allocator is deterministic-governed — autonomy earned, not assumed |
| **Decision package, not a mystery** (s93) | `evidence/decision_package.json` (context, options, recommendation, risk) |
| **Untrusted content is data, never authority** (s91) | citizen reports are data; the Sybil heuristic + suppression + `cop:write` gate stop them from becoming authority |

---

## Deck 2 — OpenAI Agents SDK (patterns applied natively in the loop; the SDK itself at the advisory edge)

In the **live coordination loop** we do not use the SDK — its **patterns** are the discipline,
implemented in plain Python, because an LLM has no place choosing irreversible, life-safety actions
(the deck's own *"start with one focused agent… governance requires deterministic decisions"*). We
*do* use the real OpenAI Agents SDK in exactly **one** bounded place — the opt-in advisory
**perception layer** ([`perception/llm_extract.py`](../src/bayanihan_net/perception/llm_extract.py))
— where it earns its keep on a non-safety-critical sub-task and is fenced so it cannot cause harm.
That seam is the faithful Assignment-1 port: a typed `output_type` agent + an **output guardrail that
re-validates the model against ground truth** (`evaluate_extraction`) + a deterministic fallback +
tracing + an offline stub. It demonstrates the deck's hardest lesson — a confidently-wrong model
(a hallucinated headcount) is **caught and discarded** — without ever letting the model move a
decision. The native-pattern map (the live loop):

| SDK primitive / rule | Native equivalent here |
|---|---|
| **Agent** (typed job + tools + guardrails) | `agents/*` with `SecurityContext` scopes |
| **Runner / run loop** | `engine.py` discrete-event loop |
| **Tool = typed contract** (not stringly-typed) (s36) | MCP adapters declare typed params + scopes; messages are typed payloads |
| **Guardrails** input/output/tool + **tripwire** (s65) | input = Pydantic validation; tool = bus `cop:write` scope gate; output = the policy gate |
| **Handoff vs agent-as-tool** — *"who owns the answer?"* (s56) | the coordinator owns the award (handoff of *work* to the human via HITL; handoff of work across orgs via A2A) |
| **Approvals are paused runs with resumable state** (s68) | the HITL gate produces a decision package and an approve/deny that the coordinator resumes from |
| **Tracing on by default** (s83) | `trace_id` on every envelope; the append-only audit log; *inspect traces before tuning* |
| **Structured outputs** (`output_type`) (s30) | Pydantic typed payloads; `RunReport` structured evidence |
| **One state strategy per conversation** (s50) | one shared state strategy — the blackboard COP (no duplicate state) |
| **model ↔ local context split** (s32) | the COP (shared truth) vs. each agent's private state (routing belief) — never blurred |
| ***"distance from the action is distance from accountability"*** (s70) | the scope check sits **on the bus**, next to the COP write; the HITL gate sits next to the commit |
| **The 3 a.m. test** (s64) | every irreversible commit has an approval/gate/audit before it can fire |

---

## Deck 3 — Harness Engineering

The deck's litmus: agent work must be **legible, bounded, repeatable, reviewable** (s6). The repo is
itself a harness by that test.

| Harness property | Evidence here |
|---|---|
| **Legible** | a small `AGENTS.md` map + cross-linked docs; repo-as-source-of-truth |
| **Bounded** | capability scopes; the pure policy gate; the (optional) sandboxed RL; `pyproject` pins |
| **Repeatable** | one global `SEED`; deterministic IDs (no `uuid4`); separate world/engine RNGs; same seed → identical evidence |
| **Reviewable** | the **evidence packet** (`evidence/*` with provenance: seed, library versions, env fingerprint) — *"without evidence, success is just a story"* (s115) |
| **Eval the *process*, not just output** (s110) | interaction-level metrics: `no_double_commit`, `awards_trace_to_valid_bid_rate` |
| **Hard vs soft assertions** (s113) | Tier-A invariant gate (hard, `evals/run_evals.py`, nonzero exit) vs Tier-B findings |
| **Stable named events** (s117) | the audit log uses stable event names (`asset_committed`, `scope_denied`, `mcp_call`, …) |
| **Typed events make the harness testable** (s136) | typed payloads + `as_row()` golden signals |
| **Capstone dimensions** (s141): legibility, boundaries, workflow, observability, evals, cleanup, humanity | all present except scheduled *cleanup* (N/A — a short-lived simulation has no drift to garden; noted honestly) |

---

## Deck 4 — Deep RL for Agentic AI

Applied in `rl/`. Faithfulness is high on the RL mechanics; the precise attribution note matters
(see [MARL_BRIDGE.md](MARL_BRIDGE.md)).

| Deck concept | Where applied |
|---|---|
| **MDP formulation** (s30, s32) + *"routing choice among alternatives"* as a fit (s83) | `rl/routing_env.py`: state = (node, dest, flood-bucket), actions = adjacent nodes |
| **Tabular Q-learning** update, *"the tabular ancestor of DQN"* (s48) | `rl/marl.py` implements `Q ← Q + α(r + γ·maxₐQ′ − Q)` |
| **on/off-policy, model-free taxonomy** (s58) | the learner is correctly **model-free, off-policy, tabular** |
| **Reward design ≠ KPI; reward hacking** *"RL optimizes the reward you give it. Exactly. Always."* (s14, s147) | naive (proxy: travel time) vs risk-aware (aligned) reward; the proxy-flip on the true objective |
| **Baseline ladder** (s163): rule-based → … → tabular RL → DQN/PPO | the study sits at **rung 4**, benchmarked vs the **rung-1** hand-coded heuristic; *earn the complexity* |
| **Offline evaluation** (s150) + **state-leakage** caution (s144) | 240-route *fixed evaluation set*, deterministic greedy rollout; in-distribution (not held-out — tabular Q can't generalize, so a disjoint holdout would be uninformative), stated honestly |
| **Advisory / *"learn the recommendation long before it earns the right to act"*** (s160) | RL output is a *recommendation*; the deterministic heuristic remains the authority |
| **Hard constraints enforced, not learned** (s153); *"an explorer without guardrails… is a liability that learns"* (s56) | passability / no-double-commit / HITL are enforced by the control plane; the learner never holds commit authority |

---

## Where we extend beyond, or diverge from, the decks (honest)

- **Deterministic agents in the live loop; one real LLM at the advisory edge.** The decks are
  LLM/Codex-centric; our *control plane* is pure Python — a *deliberate* reading of the decks' own
  governance guidance (determinism for life-safety), not an omission. We do exercise the real Agents
  SDK literally, in the opt-in advisory perception layer (`--llm-advisory`), so the SDK discipline
  (typed output, output-guardrail-against-truth, deterministic fallback, tracing) is *demonstrated*,
  not only analogized. The remaining LLM-specific content we still only analogize is prompt-injection
  blast radius (the rendered free text is trusted-synthetic here), RLHF, and sandbox-for-codegen.
- **CTDE is sourced from the MAS deck (s86), not the Deep RL deck.** The Deep RL deck is single-agent
  and never mentions CTDE/MADDPG/QMIX; MARL_BRIDGE now attributes precisely and labels our model a
  *parameter-sharing* CTDE simplification.
- **No semantic (vector) memory** — by design, to keep the safety-critical plane inspectable; grounding
  is the deterministic scenario, not embeddings.
- **No scheduled cleanup / doc-gardening loop** (Harness deck s106) — a short-lived simulation has no
  accumulating drift to garden; flagged rather than faked.
- **Emergence metrics: 3 of the deck's 6** implemented (Gini, entropy, oscillation proxy); the others
  (clustering coefficient, average path length, global reward variance) are cited but not coded,
  because the binding hazards here are inequity, report-storms, and thrashing.
- **VCG is the allocation rule only** — no transfer payments, because public rescue agents share one
  objective and no currency. Stated precisely rather than as a buzzword.

These divergences are choices with reasons, which is the posture the course itself asks for: *"you own
the design, and you must be able to defend any decision in a one-minute review."*

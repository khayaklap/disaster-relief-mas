# MARL Bridge — is multi-agent RL appropriate here?

This document answers the assignment's bridge question honestly: **where multi-agent reinforcement
learning belongs in a system like this, where it does not, and a real (small) study on the part where
it does.** It connects back to Assignment 2's reward-design lesson.

---

## 1. Where MARL does *not* belong: the live, safety-critical system

Running the whole live coordination system as a learned multi-agent policy is the wrong call, for
reasons that are structural, not stylistic:

- **Non-stationarity.** If every agent is co-learning, each agent's environment is a moving target;
  convergence is fragile and brittle to distribution shift — and a typhoon *is* distribution shift.

- **Contested reward.** Whose objective does the joint policy optimize? Triage, medical, and logistics
  have different local incentives; encoding "the right" global reward is exactly the hard alignment
  problem, and getting it subtly wrong is invisible until it fails (see §3).

- **Partial observability + credit assignment.** Delayed, sparse, life-safety outcomes make credit
  assignment hard and the failure cost catastrophic.

- **Governance demands the opposite of a black box.** Disaster response must be *deterministic,
  auditable, and defensible*. "The policy network chose to skip your barangay" is not an acceptable
  account to a community. Safety constraints must be *enforced*, not *hoped for*.

So the live system is a **deterministic, inspectable hybrid** (DESIGN §3), and the course's guidance is
adopted directly: *most business multi-agent systems should start without MARL and earn it.*

---

## 2. Where MARL *does* belong: offline routing under uncertain flooding

The routing sub-problem is the opposite shape — and a textbook RL fit:

- **Sequential** (a path is a sequence of moves), **repeated** (every dispatch routes), **simulatable**
  (we have a flood model), with **delayed reward** (the cost of a risky edge is realized later).

- A learned policy can plausibly **beat a hand-coded heuristic** by internalizing the time/risk
  trade-off across many flood states.

- It can be **sandboxed and offline**, feeding the live system only as a *recommendation* with the
  heuristic as the safe fallback (SAFETY_GOVERNANCE §5).

So we implement a bounded RL **routing study** ([`rl/`](../src/bayanihan_net/rl/)) on exactly this
sub-problem — and nowhere near the irreversible decisions.

---

## 3. The study

**Setup.** A single boat routes from the staging hub to an incident barangay across the flooded graph;
each episode fixes a destination and a river level ([`rl/routing_env.py`](../src/bayanihan_net/rl/routing_env.py)).
We train a **tabular Q-policy** ([`rl/marl.py`](../src/bayanihan_net/rl/marl.py)) — applied to every boat,
i.e. **centralized training, decentralized execution (CTDE)** in its simplest parameter-sharing form.
The boats are only weakly coupled (they share the exogenous flood, not road contention), so a shared
policy is the honest model; a fully joint-action MARL formulation is noted as future work below.

> **Implementation note.** The shipped learner is pure-numpy tabular Q-learning — fully reproducible
> with the core dependencies, fast, and testable — so the *whole* study runs without the heavy stretch
> stack. `requirements-stretch.txt` (gymnasium / torch / stable-baselines3) is the path to function
> approximation when the state space grows; it is not needed to reproduce the result here. This is the
> "start simple, earn the complexity" stance applied to the tooling itself.

**Course grounding (precise attribution).** Two decks underwrite this study and it is worth being
exact about which:

- The **MARL appropriateness analysis** and the **CTDE** concept (centralized training, decentralized
  execution) come from the **Multi-Agent Systems deck** (s84–90), which teaches CTDE, the four
  MARL-hardness problems (non-stationarity, credit assignment, partial observability, incentive
  design), the MARL algorithm map (VDN/QMIX/COMA/MADDPG/MAPPO), and the verdict *"most business MAS
  should start without MARL — and earn it on the way."* Our parameter-sharing single-policy model is
  the *simplest* CTDE form per that deck.
- The **RL mechanics** — the **tabular Q-learning** update (`td_error = r + γ·maxₐ Q(s′,a) − Q(s,a)`,
  the deck's "tabular ancestor of DQN"), **reward design and reward hacking**, the **baseline ladder**,
  **offline evaluation**, and the **advisory / "recommend before it earns the right to act"** stance —
  come from the **Deep RL deck**. Note that deck is *single-agent* and does not itself cover CTDE, so
  the multi-vehicle framing is borrowed from the MAS deck, not over-claimed onto the RL deck.

By the Deep RL deck's taxonomy the learner is **model-free, off-policy, tabular** — and it sits at
**rung 4** of that deck's baseline ladder (rule-based → supervised → bandit → *tabular/linear RL* →
DQN/PPO → advanced), benchmarked against the hand-coded heuristic at **rung 1**. We climb exactly as
far as the problem demands, the deck's "earn the complexity" discipline.

**The reward-hacking contrast (the Assignment 2 bridge).** Straight from the Deep RL deck —
*"RL optimizes the reward you give it. Exactly. Always. Even when the reward is wrong"* — we reproduce
its proxy-vs-true reward flip (the deck's pricing example: `sold` vs `sold·margin − stockout − churn`).
We train *two* policies from the **same dynamics** but different reward signals:

- **naive** — reward is travel time only (the proxy);
- **risk-aware** — reward is travel time *plus* a flood-exposure penalty (the aligned reward).

Both are then scored on the same **fixed evaluation set** of routes by a **true objective** whose
danger term the naive learner never optimized: arrival, minus travel time, minus a real *danger*
cost for cumulative flood exposure ([`rl/evaluate.py`](../src/bayanihan_net/rl/evaluate.py)). The set
is *in-distribution*, not held-out (the state space is tiny — 6 destinations × 4 flood buckets — so
it shares support with training); its job is a fair, identical comparison across the three policies,
not a generalization claim. (A disjoint holdout would be uninformative: tabular Q has no
generalization, so it would have no value for unseen states.) The reward-hacking *direction* — the
naive policy taking more flood exposure — is a property of the learned policies and is robust to the
danger weight; that weight only sets the exchange rate converting exposure into the headline number.

**Result** (`evidence/rl_training.json`, seed 20260620, 6000 episodes, 240 fixed evaluation routes):

| Policy | true return | flood exposure | arrival |
|---|---|---|---|
| **risk-aware (RL)** | **2.71** | **0.617** | 1.00 |
| naive (RL) | 2.53 | 0.650 | 1.00 |
| heuristic (hand-coded) | 2.36 | 0.674 | 1.00 |

Two findings, both reproducible and tested:

1. **Reward hacking is real.** The naive policy optimizes the travel-time proxy and looks fine on it,
   but on the *true* objective it loses, because it routes through deep-but-fast water and pays the
   exposure cost it never saw. The risk-aware policy, trained on the aligned reward, takes less flood
   exposure and wins. This is precisely the proxy-vs-true reward gap from Assignment 2 — *you get what
   you reward, not what you want.*

2. **The learned policy beats the heuristic.** The risk-aware RL policy edges out the hand-coded
   risk-aware Dijkstra heuristic on the true objective — the value-add that justifies learning this
   sub-problem at all. (The effect is honestly modest: in much of the flooded basin there is *no* safe
   alternative — boats must cross deep water to reach riverside barangays — so the gain concentrates on
   the destinations where a safer detour exists.)

The training curve (`figures/rl_training_curve.png`) shows both policies' true-objective return rising
with training and the risk-aware curve pulling ahead.

---

## 4. How it plugs into the live system (and the safety stance)

The routing agent ([`agents/routing.py`](../src/bayanihan_net/agents/routing.py)) exposes `route_for`,
the exact seam a learned policy slots into. In the live system the **deterministic heuristic remains the
authority**; the learned policy would enter only as a *recommendation*, sandboxed and offline, with the
heuristic as fallback and all safety constraints enforced by the control plane. The learner never holds
commit authority, so it *cannot* violate no-double-commit or the HITL gate, by construction.

## 5. Honest limitations & future work

- **CTDE simplification.** Parameter-sharing across weakly-coupled boats, not joint-action MARL. With
  road contention or shared-asset competition the coupling would matter and a centralized critic
  (e.g. MADDPG / QMIX) would be the next step.

- **Tabular.** The state space is small by design (a teaching artifact). Function approximation (the
  stretch stack) is the path to richer state (continuous flood, traffic, multiple vehicles in-state).

- **Modest margin.** The learned-vs-heuristic gain is real but small and geography-limited; we report
  it as such rather than overselling it.

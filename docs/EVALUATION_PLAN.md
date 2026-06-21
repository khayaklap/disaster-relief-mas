# Evaluation Plan & Results

Evaluation is two-tier, mirroring the prior assignments' house style:

- **Tier A — invariants (a gate).** Deterministic safety/recovery properties that must *always*
  hold, on every policy and every shock. Run by [`evals/run_evals.py`](../evals/run_evals.py),
  which exits non-zero on any violation. No API key, no network.

- **Tier B — findings (results to interpret).** The comparative study: the hybrid vs. baselines
  across paired seeds, with means, standard errors, and paired significance. Run by
  `cli.py eval` → `evidence/eval_report.json` + `results.csv`.

This separation matters: Tier A is pass/fail and never "tunes to a number"; Tier B reports what
*is*, including negative results.

---

## The four levels of evaluation

Following the course's evaluation grid:

Every level emits **real numbers**, all written to `evidence/scenario_report.json` under
`evaluation.*` — the agent- and interaction-level panels are computed from the audit log and the
message wire-tap by [`auditor.quality_report`](../src/bayanihan_net/agents/auditor.py); the
system- and human-level panels by `outcome_report` / `emergence_report`.

| Level | Question | Emitted metrics (in `evaluation.*`) |
|---|---|---|
| **Agent** | Does each agent do its job? | `bid_feasibility_rate`, `dedup_fusion_rate`, `reports_fused_as_duplicates`, `suspected_false_suppressed` |
| **Interaction** | Do the protocols hold? | `no_double_commit` (bool), `awards_trace_to_valid_bid_rate`, `escalations_fired` |
| **System** | Is harm reduced, equitably, in time? | `severity_weighted_served_fraction`, `min_served_fraction`, `coverage_gini`, `sla_compliance`, `sybil_*` |
| **Human + emergence** | Is the human used well? Is it misbehaving? | `hitl_approvals`, `hitl_denied`, `escalations`, `reassignment_rate`, `load_spread_entropy`, `messages_dropped` |

> **Reading the agent level honestly.** `bid_feasibility_rate` is *low* (~0.18) **by design** — it
> measures the *environment's* difficulty, not an agent defect: trucks and medical teams are
> road-bound, and the flood blocks most riverside roads, so the majority of bid *attempts* across
> all asset types are correctly found infeasible. Agent *correctness* is the interaction level:
> `no_double_commit = true` and `awards_trace_to_valid_bid_rate = 1.0` (every award came from a
> real, feasible bid). `dedup_fusion_rate ≈ 0.30` shows triage correctly fusing duplicate reports.

The **headline system metrics** are *severity-weighted served fraction* (efficiency: did we serve
the people who most needed it?) and *worst-served-barangay coverage* + *coverage Gini* (equity: did
any community get left behind?). Absolute unmet headcount is reported but is **not** the headline —
it is dominated by demand size, so it rewards serving big barangays regardless of equity.

### Observability: the MAS golden signals

Sampled every tick into `evidence/scenario_report.json → golden_signals` (and plotted in
`figures/response_timeline.png`), covering all six signals from the course's operations material:

| Signal | Field(s) |
|---|---|
| **Throughput** | `resolved` (cumulative incidents served) |
| **Latency** | `mean_response_ticks` (report → on-scene arrival delay) |
| **Errors** | `rollbacks`, `drops` |
| **Saturation** | `open_incidents` (queue depth), `committed_assets`, `utilization` |
| **Cost** | `asset_ticks_cost` (cumulative asset-ticks consumed) |
| **Safety** | `escalations`, `approvals`, plus the emergence panel (`gini_unmet`) |

---

## Baselines (ablation: one ingredient removed at a time)

All run through the *identical* engine; only the winner-selector, ordering, and governance toggle
change ([`baselines.py`](../src/bayanihan_net/baselines.py)), so a difference is attributable to that
ingredient.

| Policy | Ordering | Asset selection | Governance | Isolates |
|---|---|---|---|---|
| **hybrid** | severity | global welfare | HITL on | the system |
| `fairness_weighted` | fairness (deficit) | global welfare | on | the equity ordering lever |
| `no_governance` | severity | global welfare | **off** | the HITL gate's cost |
| `greedy_nearest` | severity | **fastest ETA** | on | welfare vs. myopic selection |
| `fifo` | **arrival order** | global welfare | on | the value of triage |
| `random` | random | random | off | the comparison floor |

## Stress battery (red-team, Tier A)

Each scenario perturbs `EnvParams` and must satisfy three invariants — **no double-commit**, **serve
some need** (graceful degradation, not collapse), and **re-stabilize** (backlog at the horizon ≤
backlog at the shock peak). Defined in [`evals/scenarios.jsonl`](../evals/scenarios.jsonl).

`baseline`, `report_storm` (4× duplicate volume), `sybil_injection` (25% fabricated), `comms_blackout`
(40% message loss), `agent_kill` (25% of the fleet disabled at peak), `forecast_outage` (river gauge
out → stale COP), `compound_crisis` (storm + comms loss + asset loss together).

## Methodology / hygiene

- **Paired comparison.** Each policy runs on the *same* 12 seeds; deltas are computed per-seed
  (hybrid − baseline) so per-seed variance cancels.

- **What "significant" means here (stated precisely).** A delta is flagged when its magnitude
  exceeds **twice its standard error** — a 2-SE *normal-approximation screen*, **not** a formal
  paired t-test. With n = 12 (df = 11) a strict two-sided t-test would use ≈ 2.20·SE, so the screen
  is mildly anti-conservative; we therefore report it as a screen and lean on the *consistency of
  sign across seeds*, not on a p-value. We make no formal significance claim beyond this.

- **Reproducibility.** Everything is seeded; every artifact carries seed + library versions + an env
  fingerprint. The world RNG and the engine-side RNG (comms-drop / kill choices) are *separate*, so
  injecting stress never perturbs the underlying disaster realization. Determinism is **process-
  independent**: two runs under different `PYTHONHASHSEED` produce byte-identical evidence (no
  set-ordering leaks into output).

- **In-sample throughout (no out-of-sample claim).** The 12 seeds *are* the evaluation set — an
  in-sample comparison, not a held-out generalization test. The RL study likewise uses a **fixed,
  in-distribution evaluation set** of routes (same support as training; a disjoint holdout would be
  uninformative for a tabular policy that cannot generalize). We reserve "held-out / out-of-sample"
  for a genuinely disjoint test set, which neither study has.

---

## Results (12 paired seeds; `evidence/eval_report.json`)

Per-policy means (± standard error):

| Policy | sev-wt served | worst-served | coverage Gini | SLA (dispatch) |
|---|---|---|---|---|
| **hybrid** | 0.353 ± 0.018 | 0.220 | 0.109 | 0.606 |
| fairness_weighted | 0.350 ± 0.018 | 0.212 | 0.109 | 0.602 |
| no_governance | 0.355 ± 0.019 | 0.222 | 0.111 | 0.615 |
| greedy_nearest | 0.354 ± 0.019 | 0.217 | 0.118 | 0.606 |
| fifo | 0.348 ± 0.019 | 0.220 | 0.106 | 0.599 |
| random | 0.340 ± 0.020 | 0.203 | 0.113 | 0.597 |

(SLA = fraction of genuine incidents whose responder was *committed* by the priority deadline —
a dispatch target, judged independently of completion.)

Paired hybrid − baseline (★ = paired-significant, |Δ| > 2·SE):

| vs. baseline | Δ sev-wt served | Δ worst-served | Δ coverage Gini |
|---|---|---|---|
| greedy_nearest | −0.0015 | +0.0026 | −0.0085 (more equitable) |
| fifo | +0.0045 | +0.0001 | +0.0036 |
| random | **+0.0125 ★** | +0.0173 | −0.0037 |
| fairness_weighted | +0.0028 | +0.0080 | −0.0002 |
| no_governance | −0.0019 | −0.0017 | −0.0014 |

The only paired-significant *service* gain is **hybrid vs. random** (severity-weighted +0.0125 ★;
raw served fraction +0.0136 ★). Every delta against the other *reasonable* policies — greedy-nearest
selection, FIFO ordering, fairness ordering — is within the 2-SE screen. (The one other significant
delta in the report is a **cost**: hybrid's dispatch-SLA is −0.0086 ★ below `no_governance`, the gate's
reserve-protection price; point 3 below.)

### Interpretation

1. **Coordinating at all is the lever that moves the needle — not which sensible policy you pick.**
   The single robust service result is that the hybrid significantly outserves the **random** allocator
   (severity-weighted +0.0125 ★, raw served +0.0136 ★): you do need triage and welfare scoring rather
   than acting at random. But against every other *reasonable* policy the gap is within the 2-SE screen.
   Swapping welfare scoring for fastest-ETA selection (`greedy_nearest`), or severity ordering for FIFO
   or fairness ordering, does **not** significantly change served people or equity. We do not claim a
   significant edge where the data does not show one.

2. **Why allocation policy barely moves the outcome here.** Under hard capacity saturation the binding
   constraint is which barangays you can physically reach with the assets you have, not the order you
   serve them in or the rule you score bids by. Welfare scoring still *directionally* spreads assets
   toward higher harm-reduction (lower coverage Gini than greedy, −0.0085), and we default to it on that
   basis and on its safety properties — but in this regime the honest reading is that the reasonable
   policies are statistically indistinguishable on service, and only *no* coordination (random) is
   clearly worse.

3. **Governance is nearly free at baseline.** On the headline severity-weighted service,
   `no_governance` ≈ hybrid (within 0.2 pp). The one measurable price is a small, paired-significant
   **−0.9 pp dispatch-SLA** margin (0.606 vs. 0.615): the gate withholds the last medical reserve from
   sub-HIGH incidents, which delays a few dispatches by design. That is the reserve-protection trade —
   cheap at baseline, and it earns its keep under stress (below).

4. **The fairness ordering does not help — and we report it.** `fairness_weighted` is *directionally*
   worse than the hybrid on worst-served coverage (hybrid +0.0080), but the difference is **within the
   2-SE screen** — so we make no significance claim, only that the lever fails to help. The diagnosed
   mechanism still holds: re-ordering the queue toward currently-under-served barangays sends scarce
   assets to the hardest-to-reach places, where they are tied up without lifting that community's final
   coverage, because the binding constraint is **reachability and capacity, not scheduling order**. We
   keep it as an evaluated negative ([REFLECTION.md](REFLECTION.md)) and default to severity ordering.

### Stress results (`evidence/stress_report.json`) — **all 7 battery scenarios PASS** (6 stress injections + baseline)

Every scenario re-stabilizes with zero safety-invariant violations. Notable behaviours:
`report_storm` keeps service *near baseline* (content-key dedup absorbs the 4× duplicate flood);
`comms_blackout` drops ~130 non-critical messages but functions because critical traffic is
protected; `agent_kill` degrades *gracefully* (worst-served falls to ~0.08 — honest) as leases are
reclaimed and work re-auctioned; `compound_crisis` is the worst case (a barangay reaches 0 coverage)
yet still re-stabilizes and never double-commits. **This is where the hybrid's governance and
resilience design — invisible at baseline — is the whole point.**

### Why the effect sizes are modest

The scenario is **capacity-saturated** (nine assets, thousands at risk) — the realistic disaster
regime. When demand vastly exceeds capacity, *no* allocation policy can serve dramatically more
people; even welfare-vs-greedy and severity-vs-fairness come out within noise. That null result is
itself the finding: it is the cleanest evidence that **capacity and reachability, not the allocation
rule, are what bind** — and that the one choice which demonstrably matters is coordinating at all
(hybrid ≫ random). We chose not to de-saturate the scenario to manufacture larger gaps; the honest
result is that here coordination's payoff is *safety, governance, and graceful degradation under
stress* (above), plus a clear edge over no coordination — not a significant separation between
sensible policies. A de-saturation sweep, where those separations should widen, is future work
([REFLECTION.md](REFLECTION.md)).

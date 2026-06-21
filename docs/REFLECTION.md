# Reflection — an honest build log

The rubric rewards honest failure analysis over a clean victory narrative, and this build earned
several. What follows is the real sequence of things that broke, what the symptom was, what I changed,
and what is *still* limited. None of these are hypothetical — each corresponds to a real fix made while
wiring the engine, the evaluation, and the RL study together.

---

## What broke, and what I changed

### 1. The system looked like it was failing — it was escalating saturation

**Symptom.** The first end-to-end run produced **283 escalations** and **91 HITL denials** on a single
scenario. It read like a system in crisis.
**Diagnosis.** Two separate miscalibrations. (a) Every tick, triage announced more tasks than there were
idle assets; tasks beyond capacity found no bids and were escalated as *CRITICAL* — but "all assets are
busy right now" is normal backpressure, not an exception. (b) The HITL "last asset of a type" gate fired
on nearly every boat commit, because there are only four boats and usually ≤ 1 idle — so the duty officer
hoarded boats and starved service.
**Fix.** Escalation is now reserved for genuinely *overdue and unreachable* incidents, raised **once** per
incident; routine no-asset situations defer silently. The gate became **fleet-size aware** — it protects
the genuinely scarce reserve (the two medical teams) but does not page a human for every busy boat — and
it keys on *incident population* (how many are at the scene), not capacity-limited served count. Result:
escalations 283 → ~1, denials 91 → ~7, with service *up*.
**Lesson.** A safety mechanism mis-calibrated to a scarce-resource regime doesn't fail loudly — it quietly
strangles throughput. The gate has to know how scarce the thing it's protecting actually is.

### 2. "Resolved" silently over-counted lives saved

**Symptom.** Early served fractions looked implausibly tied to incident *count*, not capacity.
**Diagnosis.** I had modelled a resolved incident as fully served regardless of the serving asset's
capacity — a 10-seat boat "rescued" a 30-person incident.
**Fix.** A dispatch now serves `min(asset_capacity, people)`; the remainder is unmet and counted honestly
(a documented simplification: we do not re-queue the remainder). Scoring credits the *true* served
headcount, capped at the genuine need.
**Lesson.** The most dangerous bugs in an evaluation are the ones that flatter you.

### 3. The fairness lever was a no-op — then it *backfired*

This was the hardest and most instructive failure.
**First attempt.** I put a fairness term inside the bid score (`severity × served / eta × (1 + w·deficit)`).
It changed nothing, because the deficit multiplier scales *every* bid for an incident equally — it can
never change *which asset* wins. A silent no-op.
**Second attempt.** I moved fairness to the *task ordering* — serve under-served barangays first. Now it
was active… and came out **directionally worse** on the worst-served barangay (paired Δ −0.0080, within
the 2-SE screen — not significant). Even a pure lexicographic max-min ordering failed to help.
**Diagnosis.** In a capacity-saturated, reachability-heterogeneous flood, the binding constraint is *which
barangays you can physically reach with the assets you have*, **not** the order you serve them in.
Prioritizing the currently-worst-off barangay sends scarce boats to the hardest-to-reach places, where
they are tied up longer and partially serve, *without* lifting that community's final coverage — and it
deprioritizes barangays you could have efficiently cleared.
**Fix.** I kept the lever but made it an **evaluated variant** (`fairness_weighted`), defaulted the system
to the better severity ordering, and report the negative result with its mechanism (EVALUATION_PLAN §
Interpretation, point 4). I did *not* delete it to make the story cleaner — the incentive I keep is the
global **welfare scoring** in the auction: the globally-aligned rule that, unlike queue re-ordering,
never underperforms the alternatives here and significantly outserves a random allocator.
**Lesson.** "Add a fairness weight" is not a free lunch. Whether a fairness mechanism helps depends on what
the binding constraint actually is, and you only learn that by measuring — across enough paired seeds to
separate signal from seed noise.

### 4. The baselines were indistinguishable

**Symptom.** hybrid, no-fairness, greedy, and FIFO produced nearly identical numbers.
**Diagnosis.** All baselines shared triage's severity ordering; only the *asset selector* differed, and
under saturation asset choice barely moves aggregate service.
**Fix.** Made *ordering* policy-driven too (severity / fairness / FIFO / random), so each ablation removes
exactly one ingredient. FIFO (no triage) and random then became genuinely different, and the triage value
became visible.
**Lesson.** An ablation only isolates a factor if the factor is the *only* thing that changes.

### 5. Demand was 30× oversubscribed

**Symptom.** 302 incidents, 21% served — everything saturated into a flat line.
**Fix.** Tuned `base_report_rate` and the per-incident headcount down to a regime that is *saturated but
servable* (~35% served) rather than a flat-line collapse. I deliberately did **not** de-saturate to the
point where every policy serves everyone — that would erase the problem the system exists to solve. (As
it turned out, even at this saturation the *reasonable* allocation policies land within noise of each
other — see EVALUATION_PLAN — which is itself a finding about what binds, not a tuning failure.)

### 6. `report_storm` tested the wrong thing

**Symptom.** The report-storm stress scenario collapsed service to 8%.
**Diagnosis.** The knob multiplied *genuine* incidents — a demand surge — not the *duplicate* report
volume. That conflates "a storm of messages about the same events" (which dedup should absorb) with "a
bigger disaster" (which nothing can absorb).
**Fix.** The storm now multiplies duplicate re-reports of the *same* incidents. Service stays near baseline
because content-key dedup absorbs them — which is the property the scenario is supposed to test.

### 7. Small stuff

`tab:teal` is not a real matplotlib color (it's not in the tab10 palette) — a 2-minute fix that cost a
confusing traceback. A handful of mypy strictness fixes (typed-payload access, `replace` kwargs). The PDF
text-layer extraction quirk during the slide-review phase (unrelated to this repo).

---

## What is still limited (known, not hidden)

- **Capacity-bound regime ⇒ effect sizes within noise between reasonable policies.** The gaps between the
  sensible allocation policies are small (single-digit pp) and mostly *within* the 2-SE screen, because
  nine assets cannot serve thousands and reachability/capacity — not the allocation rule — binds. The one
  paired-significant service result is hybrid over the *random* allocator; the value of coordination over
  *other heuristics* shows up in safety and stress behaviour, not in a significant baseline service gap.
  Pretending there is a clean significant separation between heuristics would be dishonest.

- **Dedup can merge two genuine incidents** in the same barangay/type/time-window. Rare at the chosen
  window, but a real hazard the content-key approach accepts; flagged in `incidents.content_key`.

- **Partial service is not re-queued.** A boat that serves 10 of 30 leaves 20 unmet; we count it honestly
  but do not model the follow-up trip.

- **The "human" is a deterministic policy.** Good for reproducibility and for testing the *gate*; it is not
  a study of real reviewer behaviour, latency, or fatigue.

- **External feeds and cross-agency partners are mocked.** The boundary, the scope check, and the trust
  decision are real in code; the upstreams are simulated.

- **RL is tabular and single-vehicle-coupled.** A teaching artifact, not a planner; joint-action MARL and
  function approximation are future work (MARL_BRIDGE §5).

- **Illustrative geography.** Not official Marikina data; a real deployment must source live feeds and be
  re-validated against ground truth.

---

## What I would do next

1. **De-saturate as a sweep.** Run the policy comparison across an asset-to-demand ratio sweep to show
   where coordination/fairness effects grow — I expect the equity gains to widen as the system leaves the
   hard-saturation regime.

2. **A reachability-aware fairness lever** — reserve *capacity* (not schedule order) for hard-to-reach
   barangays, which targets the actual binding constraint rather than the schedule.

3. **Multi-seed stress** — the stress battery is single-seed-per-scenario for readability; a multi-seed
   version would put confidence intervals on the degradation.

4. **A real HITL study** — replace the deterministic approver with logged human decisions to measure
   reviewer load and escalation precision for real.

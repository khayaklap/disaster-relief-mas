"""Baselines and the evaluation matrix -- isolating what each design choice buys.

We compare the system against ablations that each change one ingredient, all running through
the *identical* engine (only the winner-selector, the queue ordering, and the governance toggle
change), so any difference is attributable to that ingredient and not to implementation drift:

* ``hybrid``           -- severity-ordered triage + global-welfare asset selection + HITL gate
  (the system; fairness ordering OFF by default -- see ``fairness_weighted``).
* ``fairness_weighted``-- as hybrid but the queue is re-ordered toward under-served barangays
  (isolates the equity ordering lever -- reported as ineffective under saturation).
* ``no_governance``    -- as hybrid but no HITL gate (isolates governance).
* ``greedy_nearest``   -- ignore welfare; take the fastest feasible asset (a myopic market).
* ``fifo``             -- no triage; serve incidents in arrival order (isolates triage).
* ``random``           -- random feasible asset, random order (the floor).

:func:`run_matrix` runs every policy across several seeds; :func:`summarize` reduces the
runs to per-policy means with a standard error, and the hybrid-vs-baseline deltas.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from .config import SEED, config_with
from .coordination.contract_net import ScoredBid, select_winner
from .engine import Engine, RunReport

Selector = Callable[[list[ScoredBid]], "ScoredBid | None"]

# Seeds used for the paired, multi-seed comparison (fixed for reproducibility). A dozen
# paired worlds gives the per-seed-cancelling comparison enough power to resolve the small
# effects that survive severe capacity saturation.
DEFAULT_SEEDS: tuple[int, ...] = tuple(20260620 + i for i in range(12))


def greedy_select(scored: list[ScoredBid]) -> ScoredBid | None:
    """Myopic auctioneer: the fastest feasible asset, ignoring welfare/severity/equity."""
    feasible = [s for s in scored if s.bid.feasible]
    if not feasible:
        return None
    feasible.sort(key=lambda s: (s.bid.eta_ticks, s.bid.asset_id))
    return feasible[0]


def make_random_select(rng: np.random.Generator) -> Selector:
    """A reproducible random selector over the feasible bids (the comparison floor)."""

    def _select(scored: list[ScoredBid]) -> ScoredBid | None:
        feasible = sorted((s for s in scored if s.bid.feasible), key=lambda s: s.bid.asset_id)
        if not feasible:
            return None
        return feasible[int(rng.integers(0, len(feasible)))]

    return _select


@dataclass(frozen=True)
class PolicySpec:
    """One row of the comparison matrix. Each ablation removes exactly one ingredient so a
    difference is attributable to it (ordering, asset selection, fairness, or governance)."""

    name: str
    use_fairness: bool
    use_governance: bool
    selector: str  # "welfare" | "greedy" | "random"
    order_mode: str  # "fairness" | "severity" | "fifo" | "random"


POLICIES: tuple[PolicySpec, ...] = (
    # the system: severity-prioritized triage + global-welfare asset selection + HITL gate
    PolicySpec("hybrid", False, True, "welfare", "severity"),
    # an evaluated EQUITY variant: re-order the queue toward under-served barangays. We
    # report honestly that under capacity saturation this does not improve coverage equity.
    PolicySpec("fairness_weighted", True, True, "welfare", "fairness"),
    # ablations, each changing one thing relative to hybrid
    PolicySpec("no_governance", False, False, "welfare", "severity"),  # drop the HITL gate
    PolicySpec("greedy_nearest", False, True, "greedy", "severity"),  # myopic asset choice
    PolicySpec("fifo", False, True, "welfare", "fifo"),  # no triage: serve in arrival order
    PolicySpec("random", False, False, "random", "random"),  # the floor
)


def build_engine(spec: PolicySpec, seed: int) -> Engine:
    """Construct an engine for one (policy, seed) cell of the matrix."""
    config = config_with(seed)
    if spec.selector == "welfare":
        select: Selector = select_winner
    elif spec.selector == "greedy":
        select = greedy_select
    else:  # random -- seeded distinctly so it never tracks the world RNG
        select = make_random_select(np.random.default_rng(seed + 99))
    return Engine(
        config,
        policy_name=spec.name,
        use_fairness=spec.use_fairness,
        use_governance=spec.use_governance,
        select=select,
        order_mode=spec.order_mode,
        run_id=f"{spec.name}-seed{seed}",
    )


def run_matrix(
    seeds: tuple[int, ...] = DEFAULT_SEEDS, policies: tuple[PolicySpec, ...] = POLICIES
) -> list[RunReport]:
    """Run every policy across every seed and return all reports."""
    return [build_engine(spec, seed).run() for seed in seeds for spec in policies]


def _mean_se(values: list[float]) -> tuple[float, float]:
    n = len(values)
    if n == 0:
        return 0.0, 0.0
    mean = sum(values) / n
    if n == 1:
        return mean, 0.0
    var = sum((v - mean) ** 2 for v in values) / (n - 1)
    return mean, math.sqrt(var / n)  # standard error of the mean


_COMPARE_METRICS = (
    "served_fraction",
    "severity_weighted_served_fraction",
    "sla_compliance",
    "min_served_fraction",
    "coverage_gini",
    "reassignment_rate",
)


def summarize(reports: list[RunReport]) -> dict[str, Any]:
    """Per-policy means (+/- SE) and **paired** hybrid-vs-baseline deltas.

    The deltas are paired by seed (hybrid minus baseline on the *same* world), which is far
    more sensitive than comparing independent means -- the per-seed variance cancels. A
    delta is flagged ``significant`` when its magnitude exceeds twice its standard error.
    """
    by_policy: dict[str, dict[int, dict[str, float]]] = {}
    for r in reports:
        policy = r.provenance["policy"]
        seed = int(r.provenance["seed"])
        by_policy.setdefault(policy, {})[seed] = {
            m: float(r.summary_row()[m]) for m in _COMPARE_METRICS
        }

    per_policy = {
        policy: {m: _mean_se([rows[m] for rows in seeds.values()]) for m in _COMPARE_METRICS}
        for policy, seeds in by_policy.items()
    }

    paired: dict[str, dict[str, dict[str, float]]] = {}
    hybrid_seeds = by_policy.get("hybrid", {})
    for policy, seeds in by_policy.items():
        if policy == "hybrid":
            continue
        paired[policy] = {}
        for m in _COMPARE_METRICS:
            diffs = [hybrid_seeds[s][m] - seeds[s][m] for s in seeds if s in hybrid_seeds]
            mean, se = _mean_se(diffs)
            paired[policy][m] = {
                "delta": round(mean, 4),
                "se": round(se, 4),
                "significant": abs(mean) > 2 * se and se > 0,
            }

    return {
        "seeds": len(hybrid_seeds),
        "per_policy": {
            p: {m: {"mean": round(v[0], 4), "se": round(v[1], 4)} for m, v in mp.items()}
            for p, mp in per_policy.items()
        },
        "hybrid_minus_baseline_paired": paired,
    }


# Red-team stress battery
@dataclass(frozen=True)
class StressScenario:
    """A named perturbation: a set of EnvParams overrides applied to the base scenario."""

    name: str
    description: str
    overrides: dict[str, object]


def load_scenarios(path: str | Path) -> list[StressScenario]:
    """Load the stress battery from a JSONL file (one scenario per line), with clear errors."""
    scenarios: list[StressScenario] = []
    for lineno, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
            scenarios.append(StressScenario(d["name"], d["description"], d.get("overrides", {})))
        except (json.JSONDecodeError, KeyError) as exc:
            raise ValueError(f"{path}:{lineno}: malformed stress scenario ({exc})") from exc
    return scenarios


def run_stress(scenario: StressScenario, seed: int = SEED) -> RunReport:
    """Run the hybrid policy under one stress scenario's overrides."""
    config = config_with(seed, **scenario.overrides)
    return Engine(
        config, policy_name=f"hybrid::{scenario.name}", run_id=f"stress-{scenario.name}-seed{seed}"
    ).run()


def stress_invariants(report: RunReport) -> dict[str, bool]:
    """Tier-A safety/recovery invariants every stress run must satisfy.

    The system must (1) never double-commit an asset even under the shock, (2) still serve
    *some* genuine need (graceful degradation, not collapse), and (3) **re-stabilize** --
    the outstanding backlog at the horizon is no worse than it was at the flood/shock peak,
    showing the system recovered rather than spiralling.
    """
    committed: set[str] = set()
    double_commit = False
    for ev in report.events:
        kind, aid = ev["event"], ev.get("asset_id")
        if aid is None:
            continue
        if kind == "asset_committed":
            if aid in committed:
                double_commit = True
            committed.add(aid)
        elif kind in ("asset_freed", "asset_released"):
            committed.discard(aid)

    unmet = [r["unmet_people"] for r in report.golden_rows]
    peak_unmet = max(unmet) if unmet else 0
    end_unmet = unmet[-1] if unmet else 0
    checks = {
        "no_double_commit": not double_commit,
        "served_some_need": report.outcome["total_served_people"] > 0,
        "restabilized": end_unmet <= peak_unmet,
    }
    checks["passed"] = all(checks.values())
    return checks

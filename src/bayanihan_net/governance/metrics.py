"""Observability -- MAS golden signals and emergence metrics.

Two families of measurement, both straight from the course's operations material:

* **Golden signals** (throughput, latency, errors, saturation, cost, safety) sampled each
  tick into :class:`TickSignals` -- the operator's dashboard view of the response.
* **Emergence metrics** -- the quantitative detectors for the *unwanted* emergent
  behaviours named in the design: :func:`gini` over unmet need (triage inequity /
  starvation), a reassignment rate (asset oscillation / thrashing), and
  :func:`shannon_entropy` over activity (report-storm amplification). A metric without a
  mitigation is just a number, so each pairs with a guardrail elsewhere (fairness term,
  min-commit dwell, dedup + rate limiting).

All functions here are pure and unit-tested; the engine samples them, the auditor
aggregates them.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


def gini(values: list[float]) -> float:
    """Gini coefficient of a distribution (0 = perfect equality, ->1 = maximal inequality).

    Returns 0 for an empty or all-zero distribution. Used over per-barangay unmet need:
    a high Gini means a few communities bear most of the unserved harm -- the inequity /
    starvation failure mode the fairness term and the rollback guardrail defend against.
    """
    xs = sorted(v for v in values if v >= 0.0)
    n = len(xs)
    if n == 0:
        return 0.0
    total = sum(xs)
    if total == 0.0:
        return 0.0
    weighted = sum((i + 1) * x for i, x in enumerate(xs))
    return (2.0 * weighted) / (n * total) - (n + 1.0) / n


def shannon_entropy(counts: list[float]) -> float:
    """Normalized Shannon entropy (0..1) of a non-negative count vector.

    0 when all activity is concentrated in one bucket (degenerate); 1 when spread evenly.
    Applied to per-barangay activity it flags report-storm concentration and load spread.
    """
    positive = [c for c in counts if c > 0.0]
    total = sum(positive)
    if total <= 0.0 or len(positive) <= 1:
        return 0.0
    probs = [c / total for c in positive]
    entropy = -sum(p * math.log(p) for p in probs)
    return entropy / math.log(len(positive))


@dataclass
class TickSignals:
    """One per-tick sample of the MAS golden signals (the dashboard row)."""

    tick: int
    river_m: float
    open_incidents: int  # saturation: queue depth
    in_progress: int
    resolved_cumulative: int  # throughput
    mean_response_ticks: float  # latency: mean report->on-scene (response) delay so far
    committed_assets: int  # saturation: assets in use
    utilization: float  # saturation: committed / fleet size
    committed_asset_ticks: int  # cost: cumulative asset-ticks consumed
    unmet_people: int  # outstanding severity-weighted demand (headcount proxy)
    gini_unmet: float  # emergence: inequity of unmet need so far
    escalations_cumulative: int  # safety
    approvals_cumulative: int  # safety (HITL load)
    rollbacks_cumulative: int  # errors / oscillation
    drops_cumulative: int  # errors (comms loss)

    def as_row(self) -> dict[str, float]:
        """Flatten this tick's signals to a JSON row (the MAS golden signals: throughput,
        latency, errors, saturation, cost, safety)."""
        return {
            "tick": self.tick,
            "river_m": round(self.river_m, 3),
            "open_incidents": self.open_incidents,  # saturation
            "in_progress": self.in_progress,
            "resolved": self.resolved_cumulative,  # throughput
            "mean_response_ticks": round(self.mean_response_ticks, 3),  # latency
            "committed_assets": self.committed_assets,  # saturation
            "utilization": round(self.utilization, 3),  # saturation
            "asset_ticks_cost": self.committed_asset_ticks,  # cost
            "unmet_people": self.unmet_people,
            "gini_unmet": round(self.gini_unmet, 4),
            "escalations": self.escalations_cumulative,  # safety
            "approvals": self.approvals_cumulative,  # safety
            "rollbacks": self.rollbacks_cumulative,  # errors
            "drops": self.drops_cumulative,  # errors
        }


def reassignment_rate(rollbacks: int, commitments: int) -> float:
    """Oscillation / thrashing proxy: rollbacks per commitment. 0 if none.

    This is *not* bounded to [0, 1] -- it is a ratio, and a wave of lease reclaims can in
    principle exceed the number of commitments. At baseline it is ~0 (no thrashing)."""
    if commitments <= 0:
        return 0.0
    return rollbacks / commitments

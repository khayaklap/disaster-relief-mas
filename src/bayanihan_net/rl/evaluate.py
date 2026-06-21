"""Evaluate routing policies on the TRUE objective -- and expose the reward-hacking gap.

Both learned policies and the hand-coded heuristic are scored on the same **fixed evaluation
set** (a fixed sample of destination x flood-level episodes) by a *true* objective whose danger
term the naive learner never optimized: arrival, minus normalized travel time, minus a real
**danger** cost for cumulative flood exposure. The naive policy optimizes the travel-time proxy
and looks fine on it -- but on the true objective it routes through deep water and loses, exactly
the proxy-vs-true reward gap from Assignment 2. The risk-aware policy, trained on a reward that
includes exposure, tracks the safe heuristic.

Methodological note: this is a *fixed, in-distribution* evaluation set, **not** a held-out /
out-of-sample one. The state space is tiny (6 destinations x 4 flood buckets), so the set shares
its support with training; its purpose is a *controlled, identical* comparison across the three
policies (and greedy rather than exploratory rollout), not a generalization claim. A state-space
disjoint holdout would in fact be uninformative here: a tabular Q-policy has no generalization, so
it would simply have no value for unseen states.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from ..config import Scenario, TravelMode
from .marl import QTable, train_qlearning
from .routing_env import FLOOD_LEVELS, FloodRoutingEnv

_DANGER = 8.0  # real-world cost weight on cumulative flood exposure (the "true" penalty)


def _true_return(arrived: bool, minutes: float, cum_risk: float) -> float:
    return 10.0 * float(arrived) - minutes / 10.0 - _DANGER * cum_risk


def _test_set(scenario: Scenario, n: int, seed: int) -> list[tuple[str, float]]:
    """A fixed (destination, river level) evaluation set, used identically for every policy.

    In-distribution by construction (the state space is tiny); the value is a *fair, controlled*
    comparison, not an out-of-sample generalization test (see module docstring)."""
    rng = np.random.default_rng(seed)
    dests = [b.node for b in scenario.barangays]
    return [
        (dests[int(rng.integers(0, len(dests)))], float(rng.choice(FLOOD_LEVELS))) for _ in range(n)
    ]


def evaluate_policy(
    q: QTable, scenario: Scenario, test_set: list[tuple[str, float]]
) -> dict[str, float]:
    """Greedily roll a learned Q-table over the fixed (destination, river-level) evaluation
    set and report mean return, arrival rate, flood exposure, and travel time."""
    env = FloodRoutingEnv(scenario)
    arrived_n = 0
    rets, risks, times = [], [], []
    for dest, river in test_set:
        arrived, minutes, cum_risk, _ = env.rollout(q, dest, river)
        arrived_n += int(arrived)
        rets.append(_true_return(arrived, minutes, cum_risk))
        risks.append(cum_risk)
        times.append(minutes)
    n = len(test_set)
    return {
        "true_return": round(float(np.mean(rets)), 4),
        "arrival_rate": round(arrived_n / n, 4),
        "mean_cumulative_risk": round(float(np.mean(risks)), 4),
        "mean_minutes": round(float(np.mean(times)), 4),
    }


def evaluate_heuristic(scenario: Scenario, test_set: list[tuple[str, float]]) -> dict[str, float]:
    """The deterministic risk-aware Dijkstra baseline the live system actually uses."""
    env = FloodRoutingEnv(scenario)
    arrived_n = 0
    rets, risks, times = [], [], []
    for dest, river in test_set:
        env.net.update_flood(river)
        route = env.net.route("SPORTS", dest, TravelMode.BOAT, risk_aware=True)
        if not route.feasible:
            rets.append(_true_return(False, 0.0, 0.0))
            risks.append(0.0)
            times.append(0.0)
            continue
        arrived, minutes, cum_risk = env.rollout_path(route.path, river)
        arrived_n += int(arrived)
        rets.append(_true_return(arrived, minutes, cum_risk))
        risks.append(cum_risk)
        times.append(minutes)
    n = len(test_set)
    return {
        "true_return": round(float(np.mean(rets)), 4),
        "arrival_rate": round(arrived_n / n, 4),
        "mean_cumulative_risk": round(float(np.mean(risks)), 4),
        "mean_minutes": round(float(np.mean(times)), 4),
    }


def run_study(
    scenario: Scenario, *, episodes: int = 6000, seed: int = 0, checkpoints: int = 12
) -> dict[str, Any]:
    """Train naive vs. risk-aware policies, sampling the true objective along the way, and
    compare both against the heuristic. Returns the full ``rl_training.json`` payload."""
    test_set = _test_set(scenario, n=240, seed=seed + 500)
    heuristic = evaluate_heuristic(scenario, test_set)

    history: list[dict[str, float]] = []
    step = max(1, episodes // checkpoints)
    for i in range(1, checkpoints + 1):
        budget = step * i
        q_naive = train_qlearning(scenario, "naive", episodes=budget, seed=seed)
        q_risk = train_qlearning(scenario, "risk_aware", episodes=budget, seed=seed)
        history.append(
            {
                "iteration": budget,
                "naive": evaluate_policy(q_naive, scenario, test_set)["true_return"],
                "risk_aware": evaluate_policy(q_risk, scenario, test_set)["true_return"],
            }
        )

    q_naive = train_qlearning(scenario, "naive", episodes=episodes, seed=seed)
    q_risk = train_qlearning(scenario, "risk_aware", episodes=episodes, seed=seed)
    final = {
        "naive": evaluate_policy(q_naive, scenario, test_set),
        "risk_aware": evaluate_policy(q_risk, scenario, test_set),
        "heuristic": heuristic,
    }
    return {
        "episodes": episodes,
        "seed": seed,
        "danger_weight": _DANGER,
        "test_episodes": len(test_set),
        "history": history,
        "final": final,
        "verdict": _verdict(final),
    }


def _verdict(final: dict[str, dict[str, float]]) -> dict[str, Any]:
    naive, risk, heur = final["naive"], final["risk_aware"], final["heuristic"]
    return {
        # the proxy-vs-true reward gap: the naive policy wins on travel time yet loses on the
        # true objective because it pays no attention to flood exposure
        "reward_hacking_demonstrated": risk["true_return"] > naive["true_return"]
        and naive["mean_cumulative_risk"] > risk["mean_cumulative_risk"],
        "risk_aware_true_return": risk["true_return"],
        "naive_true_return": naive["true_return"],
        "heuristic_true_return": heur["true_return"],
        # the RL value-add: the learned risk-aware policy can match or beat the hand-coded
        # risk-aware heuristic on the true objective
        "learned_beats_heuristic": risk["true_return"] > heur["true_return"],
        "risk_aware_minus_heuristic": round(risk["true_return"] - heur["true_return"], 4),
    }

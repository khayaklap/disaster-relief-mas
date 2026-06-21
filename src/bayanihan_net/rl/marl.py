"""Tabular Q-learning for the flood-routing policy (the offline, sandboxed learner).

We train a single state-action value table and apply it to every boat -- *centralized
training, decentralized execution* (CTDE) in its simplest, parameter-sharing form. The
boats are only weakly coupled (they share the exogenous flood, not road contention), so a
shared policy is the honest model; a fully joint-action MARL formulation is noted as future
work in MARL_BRIDGE.md. The learner is deliberately offline and advisory: nothing here ever
touches the live safety loop, where the deterministic heuristic remains the authority.

Two policies are trained from the *same* dynamics but different reward signals -- ``naive``
(travel time only) and ``risk_aware`` (time plus a flood-exposure penalty) -- so the study
can show one of them reward-hacking the proxy. Pure numpy; reproducible from a seed.
"""

from __future__ import annotations

import numpy as np

from ..config import Scenario
from .routing_env import FloodRoutingEnv

QTable = dict[tuple[int, int, int], np.ndarray]


def train_qlearning(
    scenario: Scenario,
    reward_mode: str,
    *,
    episodes: int = 6000,
    seed: int = 0,
    alpha: float = 0.5,
    gamma: float = 0.95,
    eps_start: float = 0.3,
    eps_end: float = 0.02,
) -> QTable:
    """Train one tabular Q-policy under the given reward mode. Epsilon decays linearly."""
    env = FloodRoutingEnv(scenario, seed=seed)
    rng = np.random.default_rng(seed + 1)
    q: QTable = {}

    def q_row(state: tuple[int, int, int]) -> np.ndarray:
        """Q-values for a state, lazily initialised to a zero row on first visit."""
        if state not in q:
            q[state] = np.zeros(env.n_nodes)
        return q[state]

    for ep in range(episodes):
        state = env.reset()
        eps = eps_start + (eps_end - eps_start) * (ep / max(1, episodes - 1))
        for _ in range(env.max_steps):
            valid = env.valid_actions(state)
            if not valid:
                break
            row = q_row(state)
            if rng.random() < eps:
                action = valid[int(rng.integers(0, len(valid)))]
            else:
                action = valid[int(np.argmax([row[a] for a in valid]))]
            res = env.step(action, reward_mode)
            next_valid = env.valid_actions(res.state)
            best_next = (
                max(q_row(res.state)[a] for a in next_valid)
                if (next_valid and not res.done)
                else 0.0
            )
            row[action] += alpha * (res.reward + gamma * best_next - row[action])
            state = res.state
            if res.done:
                break
    return q

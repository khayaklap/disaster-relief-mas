"""A flood-routing MDP -- the bounded, offline sub-problem the MARL study learns on.

A rescue boat must travel from the staging hub to an incident barangay across a flooded
road graph. Each *episode* fixes a destination and a river level; the agent picks the next
node to move to. Deep-but-passable edges are faster-looking yet dangerous; the whole point
of the study is whether the learned policy internalizes that danger or *reward-hacks* the
travel-time proxy and routes through deep water.

This is intentionally small and tabular (a few hundred states): it is a teaching artifact
that bridges to Assignment 2's reward-design lesson, not a production planner. It needs no
gymnasium/torch -- only numpy -- so the study is fully reproducible with the core deps; the
optional stretch stack (``requirements-stretch.txt``) is the path to function approximation.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..config import Scenario, TravelMode
from ..network import RoadNetwork

# River levels that index the flood "bucket" dimension of the state (illustrative).
FLOOD_LEVELS: tuple[float, ...] = (15.0, 16.5, 18.0, 19.2)
_STAGING = "SPORTS"


@dataclass(frozen=True)
class StepResult:
    """One environment transition: the resulting state, reward, terminal flags, and the
    minutes/flood-risk the move incurred (the latter two drive the reward shaping)."""

    state: tuple[int, int, int]
    reward: float
    done: bool
    arrived: bool
    minutes: float
    risk: float


class FloodRoutingEnv:
    """A single-boat routing MDP over the flooding network (BOAT travel mode)."""

    def __init__(
        self, scenario: Scenario, *, risk_weight: float = 6.0, max_steps: int = 16, seed: int = 0
    ) -> None:
        self.scenario = scenario
        self.params = scenario.params
        self.net = RoadNetwork(scenario.edges, scenario.params)
        self.nodes: list[str] = list(scenario.nodes)
        self.node_index = {n: i for i, n in enumerate(self.nodes)}
        self.n_nodes = len(self.nodes)
        self.dests = [b.node for b in scenario.barangays]
        self._adj: dict[int, list[int]] = {
            self.node_index[u]: [self.node_index[v] for v in self.net.G.neighbors(u)]
            for u in self.nodes
        }
        self.risk_weight = risk_weight
        self.max_steps = max_steps
        self.rng = np.random.default_rng(seed)
        self._cur = 0
        self._dest = 0
        self._flood_idx = 0
        self._steps = 0

    # -- episode control -------------------------------------------------------------
    def reset(self, dest: str | None = None, river_m: float | None = None) -> tuple[int, int, int]:
        """Begin a new episode and return the initial state. The destination and river level
        are drawn randomly unless pinned (the evaluator pins them for a fixed comparison set)."""
        d = dest if dest is not None else self.dests[int(self.rng.integers(0, len(self.dests)))]
        if river_m is None:
            self._flood_idx = int(self.rng.integers(0, len(FLOOD_LEVELS)))
        else:
            self._flood_idx = min(
                range(len(FLOOD_LEVELS)), key=lambda i: abs(FLOOD_LEVELS[i] - river_m)
            )
        self.net.update_flood(FLOOD_LEVELS[self._flood_idx])
        self._cur = self.node_index[_STAGING]
        self._dest = self.node_index[d]
        self._steps = 0
        return self._state()

    def _state(self) -> tuple[int, int, int]:
        return (self._cur, self._dest, self._flood_idx)

    def valid_actions(self, state: tuple[int, int, int]) -> list[int]:
        """Adjacent nodes reachable by boat at the current flood (passable edges only)."""
        cur = state[0]
        cur_name = self.nodes[cur]
        out = []
        for nb in self._adj[cur]:
            if self.net.passable(cur_name, self.nodes[nb], TravelMode.BOAT):
                out.append(nb)
        return out

    def step(self, action: int, reward_mode: str) -> StepResult:
        """Move to ``action`` (a node index). ``reward_mode`` is 'naive' or 'risk_aware'."""
        cur_name = self.nodes[self._cur]
        nxt_name = self.nodes[action]
        self._steps += 1
        minutes = self.net._edge_minutes(cur_name, nxt_name, risk_aware=False)
        risk = self.net._edge_risk(self.net.edge_depth(cur_name, nxt_name))
        self._cur = action
        # naive sees only travel time; risk-aware also pays for flood exposure
        reward = -minutes / 10.0
        if reward_mode == "risk_aware":
            reward -= self.risk_weight * risk
        arrived = self._cur == self._dest
        if arrived:
            reward += 10.0
        done = arrived or self._steps >= self.max_steps or not self.valid_actions(self._state())
        return StepResult(self._state(), reward, done, arrived, minutes, risk)

    # -- deterministic rollout for evaluation on the TRUE objective -------------------
    def rollout(
        self, policy: dict[tuple[int, int, int], np.ndarray], dest: str, river_m: float
    ) -> tuple[bool, float, float, int]:
        """Greedily follow a learned Q-policy; return (arrived, minutes, cum_risk, steps)."""
        state = self.reset(dest=dest, river_m=river_m)
        total_min = 0.0
        cum_risk = 0.0
        for _ in range(self.max_steps):
            valid = self.valid_actions(state)
            if not valid:
                return False, total_min, cum_risk, self._steps
            q = policy.get(state)
            action = valid[int(np.argmax([q[a] for a in valid]))] if q is not None else valid[0]
            res = self.step(action, "naive")  # reward mode irrelevant to dynamics
            total_min += res.minutes
            cum_risk += res.risk
            state = res.state
            if res.arrived:
                return True, total_min, cum_risk, self._steps
            if res.done:
                break
        return False, total_min, cum_risk, self._steps

    def rollout_path(self, path: tuple[str, ...], river_m: float) -> tuple[bool, float, float]:
        """Evaluate a fixed node path (the heuristic's route) on the true objective."""
        self.net.update_flood(
            FLOOD_LEVELS[
                min(range(len(FLOOD_LEVELS)), key=lambda i: abs(FLOOD_LEVELS[i] - river_m))
            ]
        )
        total_min = 0.0
        cum_risk = 0.0
        for u, v in zip(path[:-1], path[1:], strict=True):
            if not self.net.passable(u, v, TravelMode.BOAT):
                return False, total_min, cum_risk
            total_min += self.net._edge_minutes(u, v, risk_aware=False)
            cum_risk += self.net._edge_risk(self.net.edge_depth(u, v))
        # reaching here means every edge of the heuristic's path was passable -> arrived at dst
        return True, total_min, cum_risk

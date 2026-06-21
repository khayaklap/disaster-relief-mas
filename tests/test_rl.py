"""RL tests: the offline routing MDP, the Q-learner, and the reward-hacking result."""

from __future__ import annotations

from bayanihan_net.config import default_config
from bayanihan_net.rl.evaluate import evaluate_policy, run_study
from bayanihan_net.rl.marl import train_qlearning
from bayanihan_net.rl.routing_env import FloodRoutingEnv


def _scenario():
    return default_config().scenario


def test_env_reset_and_step_are_well_formed() -> None:
    env = FloodRoutingEnv(_scenario(), seed=0)
    state = env.reset(dest="T", river_m=18.0)
    assert state[0] == env.node_index["SPORTS"]  # boats stage at the sports center
    valid = env.valid_actions(state)
    assert valid, "the hub must have at least one passable move"
    res = env.step(valid[0], "risk_aware")
    assert res.minutes > 0 and 0.0 <= res.risk <= 1.0


def test_env_is_deterministic_from_seed() -> None:
    a = FloodRoutingEnv(_scenario(), seed=3)
    b = FloodRoutingEnv(_scenario(), seed=3)
    assert [a.reset() for _ in range(20)] == [b.reset() for _ in range(20)]


def test_qlearning_learns_a_usable_policy() -> None:
    scenario = _scenario()
    q = train_qlearning(scenario, "risk_aware", episodes=2000, seed=0)
    assert len(q) > 0
    # the learned greedy policy reaches the destinations it is tested on
    test_set = [("I", 16.5), ("C", 15.0), ("T", 18.0)]
    metrics = evaluate_policy(q, scenario, test_set)
    assert metrics["arrival_rate"] == 1.0


def test_reward_hacking_naive_pays_more_flood_exposure() -> None:
    # The headline study result: the risk-aware reward yields a safer policy and a higher
    # *true* return than the travel-time-only (naive) reward, which reward-hacks the proxy.
    study = run_study(_scenario(), episodes=4000, seed=0, checkpoints=3)
    f = study["final"]
    assert f["naive"]["mean_cumulative_risk"] >= f["risk_aware"]["mean_cumulative_risk"]
    assert study["verdict"]["reward_hacking_demonstrated"] is True


def test_study_is_reproducible() -> None:
    a = run_study(_scenario(), episodes=1500, seed=1, checkpoints=2)
    b = run_study(_scenario(), episodes=1500, seed=1, checkpoints=2)
    assert a["final"] == b["final"] and a["history"] == b["history"]

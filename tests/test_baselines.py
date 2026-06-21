"""Baseline tests: the baseline selectors, the comparison matrix, and the stress battery."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from bayanihan_net.baselines import (
    DEFAULT_SEEDS,
    POLICIES,
    StressScenario,
    greedy_select,
    load_scenarios,
    make_random_select,
    run_matrix,
    run_stress,
    stress_invariants,
    summarize,
)
from bayanihan_net.coordination.contract_net import ScoredBid
from bayanihan_net.messages import BidPayload, MsgType, SecurityContext, make_envelope

_SCENARIOS = Path(__file__).resolve().parents[1] / "evals" / "scenarios.jsonl"


def _scored(asset_id: str, eta: float, *, feasible: bool = True) -> ScoredBid:
    from bayanihan_net.config import AssetType

    bid = BidPayload(
        task_id="t",
        asset_id=asset_id,
        asset_type=AssetType.BOAT,
        eta_ticks=eta,
        capacity=10,
        route_risk=0.1,
        feasible=feasible,
        local_cost=eta,
    )
    env = make_envelope(
        agent_id="a",
        msg_type=MsgType.BID,
        payload=bid,
        trace_id="t",
        tick=0,
        security_context=SecurityContext(role="r", scopes=()),
        seq=0,
    )
    return ScoredBid(bid, env, 0.0)


def test_greedy_selects_fastest_feasible() -> None:
    bids = [_scored("BOAT-1", 5.0), _scored("BOAT-2", 2.0), _scored("BOAT-3", 9.0)]
    assert greedy_select(bids).bid.asset_id == "BOAT-2"
    assert greedy_select([_scored("X", 1.0, feasible=False)]) is None


def test_random_selector_is_reproducible_and_feasible() -> None:
    bids = [_scored("BOAT-1", 5.0), _scored("BOAT-2", 2.0), _scored("BOAT-3", 9.0)]
    pick1 = make_random_select(np.random.default_rng(0))(bids)
    pick2 = make_random_select(np.random.default_rng(0))(bids)
    assert pick1.bid.asset_id == pick2.bid.asset_id  # same seed -> same choice
    assert pick1.bid.feasible


def test_matrix_is_deterministic_and_covers_all_policies() -> None:
    seeds = DEFAULT_SEEDS[:2]
    a = summarize(run_matrix(seeds=seeds))
    b = summarize(run_matrix(seeds=seeds))
    assert a == b  # fully reproducible
    assert set(a["per_policy"]) == {p.name for p in POLICIES}
    assert "hybrid_minus_baseline_paired" in a


def test_stress_scenarios_load_and_pass_invariants() -> None:
    scenarios = load_scenarios(_SCENARIOS)
    assert any(s.name == "agent_kill" for s in scenarios)
    # every shipped stress scenario must satisfy the Tier-A safety/recovery invariants
    for sc in scenarios:
        checks = stress_invariants(run_stress(sc))
        assert checks["passed"], f"{sc.name} violated {checks}"


def test_fabricated_scenario_overrides_apply() -> None:
    sc = StressScenario("kill_half", "lose half the fleet", {"agent_kill_fraction": 0.5})
    report = run_stress(sc)
    # the kill is recorded in the audit log -> the override really took effect
    assert any(e["event"] == "asset_disabled" for e in report.events)

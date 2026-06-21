"""Tier-A evaluation harness -- deterministic safety invariants with a hard exit code.

This is the gate, not the analysis: it runs the full policy matrix and the stress battery
and asserts the invariants that must ALWAYS hold, on every policy and every shock --
reproducibility, no double-commit, served <= true need, bounded metrics, and
re-stabilization after each stress injection. It exits non-zero the moment any invariant is
violated, so it can run in CI with no API key and no network.

The *comparative* findings (hybrid vs. baselines, effect sizes, significance) live in
``cli.py eval`` / ``eval_report.json`` -- those are results to interpret, not pass/fail gates.

Usage:  uv run python evals/run_evals.py
"""

from __future__ import annotations

import sys

from bayanihan_net.baselines import (
    DEFAULT_SEEDS,
    POLICIES,
    build_engine,
    load_scenarios,
    run_stress,
    stress_invariants,
)
from bayanihan_net.config import default_config
from bayanihan_net.engine import Engine

_SCENARIOS = "evals/scenarios.jsonl"


def _no_double_commit(report: object) -> bool:
    committed: set[str] = set()
    for ev in report.events:  # type: ignore[attr-defined]
        kind, aid = ev["event"], ev.get("asset_id")
        if kind == "asset_committed":
            if aid in committed:
                return False
            committed.add(aid)
        elif kind in ("asset_freed", "asset_released"):
            committed.discard(aid)
    return True


def main() -> int:
    failures: list[str] = []

    def check(name: str, ok: bool) -> None:
        status = "PASS" if ok else "FAIL"
        print(f"  [{status}] {name}")
        if not ok:
            failures.append(name)

    print("Tier-A invariants -- policy matrix")
    # 1) determinism of the canonical run
    a = Engine(default_config()).run()
    b = Engine(default_config()).run()
    check("hybrid run is reproducible", a.outcome == b.outcome and a.golden_rows == b.golden_rows)

    # 2) every (policy, seed) cell: no double-commit, served <= need, bounded metrics
    seeds = DEFAULT_SEEDS[:4]
    for spec in POLICIES:
        ok = True
        for seed in seeds:
            rep = build_engine(spec, seed).run()
            o = rep.outcome
            ok = ok and _no_double_commit(rep)
            ok = ok and 0 <= o["total_served_people"] <= o["total_need_people"]
            ok = ok and 0.0 <= o["served_fraction"] <= 1.0
            ok = ok and 0.0 <= o["coverage_gini"] <= 1.0
            ok = ok and 0.0 <= o["min_served_fraction"] <= 1.0
        check(f"policy '{spec.name}': invariants hold across {len(seeds)} seeds", ok)

    print("Tier-A invariants -- stress battery")
    for sc in load_scenarios(_SCENARIOS):
        checks = stress_invariants(run_stress(sc))
        check(f"stress '{sc.name}': {_fmt(checks)}", checks["passed"])

    print()
    if failures:
        print(f"FAILED: {len(failures)} invariant(s) violated")
        return 1
    print("ALL TIER-A INVARIANTS PASSED")
    return 0


def _fmt(checks: dict[str, bool]) -> str:
    return ", ".join(f"{k}={v}" for k, v in checks.items() if k != "passed")


if __name__ == "__main__":
    sys.exit(main())

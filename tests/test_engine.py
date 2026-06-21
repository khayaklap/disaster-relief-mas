"""Engine tests: the end-to-end engine is reproducible and never breaks its invariants.

The most important check here is structural: replaying the entire audit log, an asset is
never committed while already committed -- the no-double-commit guarantee, verified at full
scenario scale rather than in isolation.
"""

from __future__ import annotations

from bayanihan_net.config import EnvParams, IncidentType, default_config
from bayanihan_net.engine import Engine
from bayanihan_net.incidents import Incident, IncidentStatus


def test_full_run_is_deterministic() -> None:
    a = Engine(default_config()).run()
    b = Engine(default_config()).run()
    assert a.outcome == b.outcome
    assert a.emergence == b.emergence
    assert a.golden_rows == b.golden_rows


def test_no_asset_is_ever_double_committed() -> None:
    rep = Engine(default_config()).run()
    committed: set[str] = set()
    for ev in rep.events:
        kind = ev["event"]
        aid = ev.get("asset_id")
        if kind == "asset_committed":
            assert aid not in committed, f"{aid} double-committed at tick {ev['tick']}"
            committed.add(aid)
        elif kind in ("asset_freed", "asset_released"):
            committed.discard(aid)


def test_served_never_exceeds_need_and_fractions_are_bounded() -> None:
    o = Engine(default_config()).run().outcome
    assert 0 <= o["total_served_people"] <= o["total_need_people"]
    assert 0.0 <= o["served_fraction"] <= 1.0
    assert 0.0 <= o["sla_compliance"] <= 1.0
    assert 0.0 <= o["gini_unmet"] <= 1.0
    # the base scenario should actually serve a meaningful share of need
    assert o["total_served_people"] > 0


def test_every_resolved_incident_was_committed_and_dispatched() -> None:
    eng = Engine(default_config())
    eng.run()
    for inc in eng.bb.incidents.values():
        if inc.status is IncidentStatus.RESOLVED:
            assert inc.assigned_asset is not None
            assert inc.assigned_tick is not None
            assert inc.resolved_tick is not None and inc.resolved_tick >= inc.assigned_tick


def test_report_is_fully_provenance_stamped() -> None:
    rep = Engine(default_config()).run()
    prov = rep.provenance
    for key in ("policy", "seed", "scenario", "python", "libraries", "env_fingerprint", "stress"):
        assert key in prov
    assert prov["seed"] == default_config().seed
    assert len(rep.golden_rows) == EnvParams().horizon_ticks


def test_fairness_ordering_prioritizes_the_more_underserved_barangay() -> None:
    # Mechanism check (deterministic): with the fairness ordering active, an equally-severe
    # incident in an under-served barangay outranks one in a well-served barangay; with the
    # default severity ordering the two tie. (Whether this *improves* the final equity
    # outcome is a separate, empirical question -- see EVALUATION_PLAN / REFLECTION.)
    eng = Engine(default_config(), use_fairness=True, order_mode="fairness")
    served = _seed_incident(eng, "C", "Concepcion", people=20, severity=0.6, resolved=True)
    starved = _seed_incident(eng, "T", "Tumana", people=20, severity=0.6, resolved=False)
    assert eng._task_priority(starved) > eng._task_priority(served)
    eng.order_mode = "severity"
    assert eng._task_priority(starved) == eng._task_priority(served)


def test_fairness_variant_is_active_on_some_seed() -> None:
    # The fairness lever genuinely affects allocation: across a handful of seeds it changes
    # the served distribution on at least one (it is not a global no-op). Whether the change
    # *helps* equity is the empirical question answered (negatively) in the eval.
    from bayanihan_net.config import config_with

    differs = False
    for s in (20260620, 20260621, 20260622, 20260623, 20260624):
        base = Engine(config_with(s), use_fairness=False, order_mode="severity").run()
        fair = Engine(config_with(s), use_fairness=True, order_mode="fairness").run()
        if (
            base.outcome["served_fraction_by_barangay"]
            != fair.outcome["served_fraction_by_barangay"]
        ):
            differs = True
            break
    assert differs


def _seed_incident(
    eng: Engine, node: str, barangay: str, *, people: int, severity: float, resolved: bool
) -> Incident:
    inc = Incident(
        incident_id=f"k:{node}",
        ground_truth_id=f"g:{node}",
        node=node,
        barangay=barangay,
        itype=IncidentType.RESCUE,
        people=people,
        severity=severity,
        reported_tick=0,
        deadline_tick=4,
        status=IncidentStatus.RESOLVED if resolved else IncidentStatus.OPEN,
        served_people=people if resolved else 0,
    )
    eng.bb.incidents[inc.incident_id] = inc
    return inc

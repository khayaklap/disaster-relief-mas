"""World tests: the world is reproducible and the blackboard's COP invariants hold.

These assert *behaviour and invariants*, not log strings: determinism from a seed, the
hydrograph shape, mode-dependent flood blocking, idempotent deduplication, the
no-double-commit guarantee, lease reclaim, and observation freshness.
"""

from __future__ import annotations

from bayanihan_net.blackboard import AssetStatus, Blackboard
from bayanihan_net.config import EnvParams, IncidentType, Scenario, TravelMode
from bayanihan_net.incidents import IncidentStatus, content_key
from bayanihan_net.messages import IncidentReportPayload
from bayanihan_net.network import RoadNetwork
from bayanihan_net.scenario import World


def _scenario() -> Scenario:
    return Scenario("test", EnvParams())


# -- determinism ---------------------------------------------------------------------
def test_world_is_deterministic_from_seed() -> None:
    def run() -> list[tuple[str, str, int, IncidentType, bool, bool]]:
        w = World(_scenario(), seed=12345)
        out = []
        for t in range(w.params.horizon_ticks):
            for r in w.step(t):
                out.append(
                    (r.report_id, r.ground_truth_id, r.people, r.itype, r.is_duplicate, r.is_false)
                )
        return out

    a, b = run(), run()
    assert a == b
    assert len(a) > 0  # the disaster actually generates demand


def test_different_seeds_differ() -> None:
    w1 = World(_scenario(), seed=1)
    w2 = World(_scenario(), seed=2)
    s1 = [r.people for t in range(48) for r in w1.step(t)]
    s2 = [r.people for t in range(48) for r in w2.step(t)]
    assert s1 != s2


# -- hydrograph ----------------------------------------------------------------------
def test_hydrograph_rises_to_peak_then_recedes() -> None:
    w = World(_scenario(), seed=0)
    p = w.params
    levels = [w.true_river_m(t) for t in range(p.horizon_ticks)]
    # strictly rising up to the peak tick, then receding
    assert levels[p.river_peak_tick] == p.river_peak_m
    assert all(levels[t] <= levels[t + 1] for t in range(p.river_peak_tick))
    assert all(levels[t] >= levels[t + 1] for t in range(p.river_peak_tick, p.horizon_ticks - 1))


# -- flood / routing -----------------------------------------------------------------
def test_flood_blocks_trucks_before_boats() -> None:
    params = EnvParams()
    net = RoadNetwork(_scenario().edges, params)
    net.update_flood(18.5)  # deep flood: riverside streets ~4 m
    # Tumana ("T") sits on high-exposure edges only.
    truck = net.route("SPORTS", "T", TravelMode.ROAD)
    boat = net.route("SPORTS", "T", TravelMode.BOAT)
    assert truck.feasible is False  # trucks cut off from the riverside barangay
    assert boat.feasible is True  # boats still get through (the flood asset)
    assert boat.risk > 0.5  # ...but it is a high-risk crossing


def test_risk_aware_routing_can_prefer_a_safer_path() -> None:
    params = EnvParams()
    net = RoadNetwork(_scenario().edges, params)
    net.update_flood(15.0)  # mild flood so both corridors stay passable
    naive = net.route("SPORTS", "I", TravelMode.ROAD, risk_aware=False)
    safe = net.route("SPORTS", "I", TravelMode.ROAD, risk_aware=True)
    assert naive.feasible and safe.feasible
    # the risk-aware route never accepts more flood risk than the naive one
    assert safe.risk <= naive.risk + 1e-9


# -- blackboard: idempotent dedup ----------------------------------------------------
def _report(node: str, people: int, tick: int, *, suspected: bool = False) -> IncidentReportPayload:
    return IncidentReportPayload(
        incident_id=f"GT-{node}-{tick}",
        node=node,
        barangay=node,
        itype=IncidentType.RESCUE,
        people=people,
        severity=0.8,
        reported_tick=tick,
        is_suspected_false=suspected,
    )


def test_duplicate_reports_fuse_into_one_incident() -> None:
    bb = Blackboard(EnvParams())
    key = content_key("T", IncidentType.RESCUE, tick=4, window_ticks=EnvParams().dedup_window_ticks)
    inc1, created1 = bb.ingest_report(_report("T", 30, 4), key, 4)
    inc2, created2 = bb.ingest_report(_report("T", 42, 5), key, 5)  # re-report, more people
    assert created1 is True and created2 is False
    assert inc1 is inc2  # same COP incident
    assert len(bb.incidents) == 1
    assert inc1.report_count == 2
    assert inc1.people == 42  # keeps the worst-case headcount


# -- blackboard: no double-commit ----------------------------------------------------
def test_no_double_commit() -> None:
    bb = Blackboard(EnvParams())
    bb.register_fleet(_scenario().fleet)
    first = bb.try_commit("BOAT-1", "incA", "taskA", tick=1)
    second = bb.try_commit("BOAT-1", "incB", "taskB", tick=1)  # same asset, different task
    assert first is True and second is False
    assert bb.assets["BOAT-1"].status is AssetStatus.EN_ROUTE
    assert bb.assets["BOAT-1"].incident_id == "incA"  # the first commit stands


def test_lease_expiry_then_release_frees_asset() -> None:
    params = EnvParams()
    bb = Blackboard(params)
    bb.register_fleet(_scenario().fleet)
    # open the incident so release can revert it
    inc, _ = bb.ingest_report(_report("T", 30, 0), "incA", 0)
    bb.try_commit("BOAT-1", "incA", "taskA", tick=0)
    inc.status = IncidentStatus.ASSIGNED
    # within the lease: not expired
    assert bb.expired_leases(params.lease_ttl_ticks) == []
    # past the lease (owner stopped renewing): reclaimable
    late = params.lease_ttl_ticks + 1
    assert "BOAT-1" in bb.expired_leases(late)
    bb.release_asset("BOAT-1", late, reason="lease_expired")
    assert bb.assets["BOAT-1"].status is AssetStatus.IDLE
    assert inc.status is IncidentStatus.OPEN  # reverted for re-auction


# -- blackboard: observation freshness ----------------------------------------------
def test_observation_freshness_lease() -> None:
    params = EnvParams()
    bb = Blackboard(params)
    bb.post_river(17.0, tick=10)
    val, stale = bb.latest_river(now=10)
    assert val == 17.0 and stale is False
    _, stale_now = bb.latest_river(now=10 + params.lease_ttl_ticks + 1)
    assert stale_now is True

"""Coordination tests: the coordination layer's contracts and safety invariants.

Covers the auction's incentive alignment (global welfare beats local cost; a flood-risky
route is discounted), the HITL policy gate and the duty-officer's approval policy,
transport idempotency on the bus vs. corroborating fusion at the blackboard, triage
suppression of uncorroborated Sybil reports, and the end-to-end no-double-commit and
no-feasible-asset escalation paths through the coordinator.
"""

from __future__ import annotations

from bayanihan_net.agents.coordinator import CoordinatorAgent
from bayanihan_net.agents.routing import RoutingAgent
from bayanihan_net.agents.triage import TriageAgent
from bayanihan_net.blackboard import AssetStatus, Blackboard
from bayanihan_net.config import AssetType, EnvParams, IncidentType, Scenario
from bayanihan_net.coordination.blackboard_bus import BlackboardBus
from bayanihan_net.coordination.contract_net import ScoredBid, score_bid, select_winner
from bayanihan_net.coordination.escalation import HumanApprover, should_auto_rollback
from bayanihan_net.governance import policy
from bayanihan_net.incidents import Incident, IncidentStatus, content_key
from bayanihan_net.messages import (
    BidPayload,
    IncidentReportPayload,
    MsgType,
    Priority,
    SecurityContext,
    TaskAnnouncePayload,
    make_envelope,
)


def _scenario() -> Scenario:
    return Scenario("test", EnvParams())


def _incident(node: str = "T", people: int = 40, severity: float = 0.8) -> Incident:
    return Incident(
        incident_id=f"k:{node}",
        ground_truth_id=f"g:{node}",
        node=node,
        barangay=node,
        itype=IncidentType.RESCUE,
        people=people,
        severity=severity,
        reported_tick=0,
        deadline_tick=4,
    )


def _bid(
    asset_id: str,
    *,
    eta: float = 2.0,
    cap: int = 10,
    risk: float = 0.1,
    feasible: bool = True,
    atype: AssetType = AssetType.BOAT,
) -> BidPayload:
    return BidPayload(
        task_id="task",
        asset_id=asset_id,
        asset_type=atype,
        eta_ticks=eta,
        capacity=cap,
        route_risk=risk,
        feasible=feasible,
        local_cost=eta + risk,
    )


# -- incentive alignment in the bid score --------------------------------------------
def test_global_welfare_prefers_serving_more_people_than_local_cost() -> None:
    inc = _incident(people=40, severity=0.8)
    # A small/fast asset (lower local_cost) vs a large/slower one that serves far more.
    small = _bid("BOAT-3", eta=1.0, cap=5)
    large = _bid("BOAT-1", eta=2.0, cap=20)
    assert large.local_cost > small.local_cost  # large is selfishly worse...
    assert score_bid(large, inc) > score_bid(small, inc)  # ...globally better


def test_risk_discount_lowers_welfare() -> None:
    inc = _incident()
    safe = _bid("BOAT-1", risk=0.0)
    risky = _bid("BOAT-1", risk=0.9)
    assert score_bid(safe, inc) > score_bid(risky, inc)  # a flooded route is worth less


def test_infeasible_bids_never_win() -> None:
    inc = _incident()
    feasible = ScoredBid(_bid("BOAT-1"), _dummy_env(), score_bid(_bid("BOAT-1"), inc))
    infeasible_bid = _bid("BOAT-2", feasible=False)
    infeasible = ScoredBid(infeasible_bid, _dummy_env(), score_bid(infeasible_bid, inc))
    assert select_winner([infeasible, feasible]) is feasible
    assert select_winner([infeasible]) is None


# -- the HITL policy gate ------------------------------------------------------------
def test_gate_fires_on_last_asset_and_large_commit() -> None:
    p = EnvParams()
    # last ready unit of a SCARCE type (medical, fleet 2) -> gated...
    last = policy.evaluate_commit_gate(
        action="commit_asset",
        people=6,
        asset_type=AssetType.MEDICAL,
        idle_of_type_after=0,
        fleet_of_type=2,
        river_m=16.0,
        params=p,
    )
    assert last.requires_approval and "commit_last_medical_team" in last.reasons
    # ...but the last idle BOAT of a plentiful type (fleet 4) does NOT page a human
    last_boat = policy.evaluate_commit_gate(
        action="commit_asset",
        people=6,
        asset_type=AssetType.BOAT,
        idle_of_type_after=0,
        fleet_of_type=4,
        river_m=16.0,
        params=p,
    )
    assert last_boat.requires_approval is False
    big = policy.evaluate_commit_gate(
        action="commit_asset",
        people=p.large_commit_people,
        asset_type=AssetType.TRUCK,
        idle_of_type_after=3,
        fleet_of_type=3,
        river_m=16.0,
        params=p,
    )
    assert big.requires_approval and "large_commit" in big.reasons
    routine = policy.evaluate_commit_gate(
        action="commit_asset",
        people=10,
        asset_type=AssetType.BOAT,
        idle_of_type_after=2,
        fleet_of_type=4,
        river_m=16.0,
        params=p,
    )
    assert routine.requires_approval is False


def test_gate_fires_on_forced_evacuation_at_third_alarm() -> None:
    # The forced-evacuation gate reason is defined and tested though the current run loop never
    # raises it (it only issues commit_asset). At/above 3rd-alarm river level it gates; below, not.
    p = EnvParams()
    evac = policy.evaluate_commit_gate(
        action="forced_evacuation",
        people=5,
        asset_type=AssetType.BOAT,
        idle_of_type_after=3,
        fleet_of_type=4,
        river_m=p.forced_evacuation_alarm_m,
        params=p,
    )
    assert evac.requires_approval and "forced_evacuation" in evac.reasons
    below = policy.evaluate_commit_gate(
        action="forced_evacuation",
        people=5,
        asset_type=AssetType.BOAT,
        idle_of_type_after=3,
        fleet_of_type=4,
        river_m=p.forced_evacuation_alarm_m - 1.0,
        params=p,
    )
    assert below.requires_approval is False


def test_duty_officer_holds_last_asset_for_noncritical_but_releases_for_critical() -> None:
    p = EnvParams()
    approver = HumanApprover()
    gate = policy.evaluate_commit_gate(
        action="commit_asset",
        people=6,
        asset_type=AssetType.MEDICAL,
        idle_of_type_after=0,
        fleet_of_type=2,
        river_m=16.0,
        params=p,
    )
    inc = _incident(severity=0.5)
    pkg = policy.build_decision_package(incident=inc, recommendation={}, alternatives=[], gate=gate)
    req = _approval_req(pkg)
    assert approver.decide(req, severity=0.5).approved is False  # held in reserve
    assert approver.decide(req, severity=0.9).approved is True  # released for a critical call


def test_auto_rollback_triggers_past_gini_threshold() -> None:
    p = EnvParams()
    assert should_auto_rollback(p.gini_rollback_threshold + 0.01, p) is True
    assert should_auto_rollback(p.gini_rollback_threshold - 0.01, p) is False


# -- the bus: transport idempotency vs. corroborating fusion -------------------------
def test_bus_drops_exact_duplicate_but_fuses_distinct_corroborations() -> None:
    bb = Blackboard(EnvParams())
    bus = BlackboardBus(bb)
    key = content_key("T", IncidentType.RESCUE, 0, EnvParams().dedup_window_ticks)
    e1 = _report_env("sensing-1", 0, key, people=30)
    assert bus.publish(e1, 0) is True
    assert bus.publish(e1, 0) is False  # exact same message_id -> dropped at transport
    # a DIFFERENT scout reports the same event (distinct message_id, same content key)
    e2 = _report_env("sensing-2", 0, key, people=45)
    assert bus.publish(e2, 0) is True
    assert len(bb.incidents) == 1  # fused onto one COP incident...
    assert bb.incidents[key].report_count == 2  # ...with corroboration counted
    assert bb.incidents[key].people == 45


def test_bus_enforces_cop_write_scope() -> None:
    # Least privilege at the transport layer: a COP write from a sender lacking `cop:write`
    # is refused in code (not merely discouraged in docs) and the denial is audited.
    bb = Blackboard(EnvParams())
    bus = BlackboardBus(bb)
    key = content_key("T", IncidentType.RESCUE, 0, EnvParams().dedup_window_ticks)
    unscoped = _report_env("intruder", 0, key, people=30, scopes=())
    assert bus.publish(unscoped, 0) is False  # refused
    assert len(bb.incidents) == 0  # nothing written to the COP
    assert any(e["event"] == "scope_denied" for e in bb.events)


# -- triage suppression of uncorroborated Sybil reports ------------------------------
def test_triage_suppresses_uncorroborated_suspected_false() -> None:
    bb = Blackboard(EnvParams())
    fake = _report_payload("C", suspected=True)
    bb.ingest_report(fake, "fake-key", 0)
    triage = TriageAgent("triage", _scenario())
    tasks = triage.plan(bb, tick=0, max_tasks=5)
    assert bb.incidents["fake-key"].status is IncidentStatus.SUPPRESSED
    assert all(t.typed_payload().incident_id != "fake-key" for t in tasks)  # never announced


# -- end-to-end: no double commit + escalation through the coordinator ----------------
def test_coordinator_awards_then_blocks_double_commit() -> None:
    scenario = _scenario()
    bb = Blackboard(scenario.params)
    bb.register_fleet(scenario.fleet)
    routing = RoutingAgent("routing", scenario)
    routing.update_belief(13.0)  # low river: everything passable
    coord = CoordinatorAgent("coordinator", scenario)
    approver = HumanApprover()
    incA, incB = _incident("T"), _incident("M")
    bb.incidents[incA.incident_id] = incA
    bb.incidents[incB.incident_id] = incB

    outA = coord.decide_award(
        task=_task(incA),
        incident=incA,
        bid_envs=[_bid_env("BOAT-1", incA)],
        bb=bb,
        routing=routing,
        cop_river_m=13.0,
        tick=0,
        approver=approver,
    )
    assert outA.awarded and outA.asset_id == "BOAT-1"
    assert bb.assets["BOAT-1"].status is AssetStatus.EN_ROUTE

    # a second task bids the SAME asset (a stale-snapshot race) -> atomic guard refuses it
    outB = coord.decide_award(
        task=_task(incB),
        incident=incB,
        bid_envs=[_bid_env("BOAT-1", incB)],
        bb=bb,
        routing=routing,
        cop_river_m=13.0,
        tick=0,
        approver=approver,
    )
    assert outB.awarded is False
    assert incB.status is IncidentStatus.OPEN  # left for re-auction


def test_coordinator_escalates_when_no_feasible_asset() -> None:
    scenario = _scenario()
    bb = Blackboard(scenario.params)
    bb.register_fleet(scenario.fleet)
    routing = RoutingAgent("routing", scenario)
    coord = CoordinatorAgent("coordinator", scenario)
    inc = _incident("T")
    bb.incidents[inc.incident_id] = inc
    out = coord.decide_award(
        task=_task(inc),
        incident=inc,
        bid_envs=[],  # no bids
        bb=bb,
        routing=routing,
        cop_river_m=19.0,
        tick=5,
        approver=HumanApprover(),
    )
    assert out.awarded is False and out.escalated is True
    assert any(e.msg_type is MsgType.ESCALATION_RAISED for e in out.envelopes)


def test_coordinator_rollback_releases_reversible_award() -> None:
    scenario = _scenario()
    bb = Blackboard(scenario.params)
    bb.register_fleet(scenario.fleet)
    coord = CoordinatorAgent("coordinator", scenario)
    inc = _incident("T")
    bb.incidents[inc.incident_id] = inc
    bb.try_commit("BOAT-1", inc.incident_id, "task", 0)
    inc.status = IncidentStatus.ASSIGNED
    coord.rollback_award("BOAT-1", bb, tick=1, reason="equity_guardrail")
    assert bb.assets["BOAT-1"].status is AssetStatus.IDLE
    assert inc.status is IncidentStatus.OPEN


def test_coordinator_rollback_refuses_on_scene_asset() -> None:
    # Irreversibility guard: once a crew is ON_SCENE the work cannot be yanked, so rollback
    # is a no-op that leaves the asset and its incident untouched and emits no record.
    scenario = _scenario()
    bb = Blackboard(scenario.params)
    bb.register_fleet(scenario.fleet)
    coord = CoordinatorAgent("coordinator", scenario)
    inc = _incident("T")
    bb.incidents[inc.incident_id] = inc
    bb.try_commit("BOAT-1", inc.incident_id, "task", 0)
    inc.status = IncidentStatus.ASSIGNED
    bb.assets["BOAT-1"].status = AssetStatus.ON_SCENE
    envs = coord.rollback_award("BOAT-1", bb, tick=1, reason="equity_guardrail")
    assert envs == []
    assert bb.assets["BOAT-1"].status is AssetStatus.ON_SCENE
    assert inc.status is IncidentStatus.ASSIGNED


# -- small builders -------------------------------------------------------------------
def _ctx() -> SecurityContext:
    return SecurityContext(role="t", scopes=())


def _dummy_env():
    return make_envelope(
        agent_id="a",
        msg_type=MsgType.BID,
        payload=_bid("BOAT-1"),
        trace_id="t",
        tick=0,
        security_context=_ctx(),
        seq=0,
    )


def _bid_env(asset_id: str, inc: Incident):
    payload = _bid(asset_id)
    return make_envelope(
        agent_id="logistics-rescue",
        msg_type=MsgType.BID,
        payload=BidPayload(**{**payload.model_dump(), "task_id": f"task:{inc.incident_id}"}),
        trace_id=f"trace:{inc.incident_id}",
        tick=0,
        security_context=_ctx(),
        seq=0,
        correlation_id=f"task:{inc.incident_id}",
    )


def _task(inc: Incident) -> TaskAnnouncePayload:
    return TaskAnnouncePayload(
        task_id=f"task:{inc.incident_id}",
        incident_id=inc.incident_id,
        node=inc.node,
        itype=inc.itype,
        people=inc.people,
        severity=inc.severity,
        priority=inc.priority,
        deadline_tick=inc.deadline_tick,
    )


def _report_payload(node: str, *, suspected: bool = False) -> IncidentReportPayload:
    return IncidentReportPayload(
        incident_id=f"GT-{node}",
        node=node,
        barangay=node,
        itype=IncidentType.RESCUE,
        people=30,
        severity=0.8,
        reported_tick=0,
        is_suspected_false=suspected,
    )


def _report_env(
    agent_id: str, tick: int, key: str, *, people: int, scopes: tuple[str, ...] = ("cop:write",)
):
    payload = IncidentReportPayload(
        incident_id="GT",
        node="T",
        barangay="Tumana",
        itype=IncidentType.RESCUE,
        people=people,
        severity=0.8,
        reported_tick=tick,
    )
    return make_envelope(
        agent_id=agent_id,
        msg_type=MsgType.INCIDENT_REPORT,
        payload=payload,
        trace_id=f"trace:{key}",
        tick=tick,
        security_context=SecurityContext(role="sensing", scopes=scopes),
        seq=0,
        idempotency_key=key,
        priority=Priority.HIGH,
    )


def _approval_req(pkg: dict):
    from bayanihan_net.messages import ApprovalRequestPayload

    return ApprovalRequestPayload(
        request_id="appr:1",
        action="commit_asset",
        incident_id="k:T",
        asset_id="BOAT-1",
        decision_package=pkg,
    )

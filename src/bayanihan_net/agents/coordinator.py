"""Coordinator -- the command-center supervisor that arbitrates the auction.

The coordinator is the accountable decision-maker in the hybrid design. For each announced
task it: (1) re-scores all bids on the **global** welfare objective (not the bidders' local
costs); (2) selects the winner, or escalates if nothing is feasible; (3) runs the action
through the **HITL policy gate** -- routing a decision package to the human approver when
the commitment is high-stakes; and only then (4) **atomically commits** the asset (the
single no-double-commit chokepoint) and records the award, route, and commitment. It also
owns **rollback** via :meth:`rollback_award`: releasing a still-reversible award and emitting a
ROLLBACK_ISSUED record. In the live loop this is driven by lease reclaim (an owner gone dark);
the equity-guardrail trigger (:func:`escalation.should_auto_rollback`) is a tested pure hook
that is not wired into the runtime.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from ..blackboard import AssetStatus, Blackboard
from ..config import ASSET_TRAVEL_MODE
from ..coordination.contract_net import ScoredBid, score_bids, select_winner
from ..coordination.escalation import HumanApprover
from ..governance import policy
from ..incidents import Incident, IncidentStatus
from ..messages import (
    ApprovalRequestPayload,
    AssetCommittedPayload,
    AwardPayload,
    Envelope,
    EscalationRaisedPayload,
    MsgType,
    Priority,
    RollbackIssuedPayload,
    RouteComputedPayload,
    TaskAnnouncePayload,
)
from .base import Agent
from .routing import RoutingAgent


@dataclass
class AwardOutcome:
    """The result of arbitrating one task (and the speech-acts it produced)."""

    awarded: bool
    asset_id: str | None
    envelopes: list[Envelope] = field(default_factory=list)
    escalated: bool = False
    approval_denied: bool = False


class CoordinatorAgent(Agent):
    """Scores bids globally, gates high-stakes actions, commits, and rolls back."""

    role = "coordinator"
    default_scopes = ("asset:award", "asset:commit", "escalation:raise", "rollback:issue")

    def decide_award(
        self,
        *,
        task: TaskAnnouncePayload,
        incident: Incident,
        bid_envs: list[Envelope],
        bb: Blackboard,
        routing: RoutingAgent,
        cop_river_m: float | None,
        tick: int,
        approver: HumanApprover,
        select: Callable[[list[ScoredBid]], ScoredBid | None] = select_winner,
        gate_enabled: bool = True,
    ) -> AwardOutcome:
        """Arbitrate one task. ``select`` chooses the winner from the scored bids (the
        hybrid default maximizes global welfare; baselines inject greedy/random selectors)
        and ``gate_enabled`` toggles the HITL governance gate -- so the very same award
        path is reused for the ablation baselines, keeping the comparison apples-to-apples.

        Equity is handled upstream by the engine's task ordering, not here: the auction's
        job is to find the *best asset for this incident*, not to choose between incidents."""
        envs: list[Envelope] = []
        scored = score_bids(bid_envs, incident)
        winner = select(scored)

        # (a) nothing feasible. Before the deadline this is normal backpressure (a blocked
        # road may reopen as the river recedes) -> defer silently. Once the incident is
        # overdue AND still unreachable it is a genuine failure -> escalate, but only once.
        if winner is None:
            if tick > incident.deadline_tick and not incident.escalated:
                incident.escalated = True
                esc = EscalationRaisedPayload(
                    incident_id=incident.incident_id,
                    reason="overdue and no feasible asset can reach it",
                    severity=incident.severity,
                )
                envs.append(
                    self._emit(
                        msg_type=MsgType.ESCALATION_RAISED,
                        payload=esc,
                        trace_id=f"trace:{incident.incident_id}",
                        tick=tick,
                        priority=Priority.CRITICAL,
                    )
                )
                bb.record(tick, "escalation_no_asset", incident_id=incident.incident_id)
                return AwardOutcome(False, None, envs, escalated=True)
            bb.record(tick, "deferred_no_feasible", incident_id=incident.incident_id)
            return AwardOutcome(False, None, envs)

        win = winner.bid
        served = min(win.capacity, incident.people)
        idle_after = bb.count_idle_of_type(win.asset_type) - 1
        fleet_of_type = sum(1 for a in bb.assets.values() if a.asset_type is win.asset_type)

        # (b) HITL gate -- is this commitment high-stakes enough to need a human? The size
        # signal is the incident's population (how many are at risk at the scene), not how
        # many a single scarce asset can carry away in one trip.
        gate = policy.evaluate_commit_gate(
            action="commit_asset",
            people=incident.people,
            asset_type=win.asset_type,
            idle_of_type_after=idle_after,
            fleet_of_type=fleet_of_type,
            river_m=cop_river_m if cop_river_m is not None else 0.0,
            params=self.params,
        )
        if gate_enabled and gate.requires_approval:
            pkg = policy.build_decision_package(
                incident=incident,
                recommendation={
                    "asset_id": win.asset_id,
                    "eta_ticks": round(win.eta_ticks, 2),
                    "welfare_score": round(winner.score, 4),
                    "served": served,
                },
                alternatives=[
                    {"asset_id": s.bid.asset_id, "welfare_score": round(s.score, 4)}
                    for s in sorted(scored, key=lambda s: -s.score)
                    if s.bid.asset_id != win.asset_id and s.bid.feasible
                ][:3],
                gate=gate,
            )
            req = ApprovalRequestPayload(
                request_id=f"appr:{incident.incident_id}:{tick}",
                action="commit_asset",
                incident_id=incident.incident_id,
                asset_id=win.asset_id,
                decision_package=pkg,
            )
            envs.append(
                self._emit(
                    msg_type=MsgType.APPROVAL_REQUESTED,
                    payload=req,
                    trace_id=f"trace:{incident.incident_id}",
                    tick=tick,
                    priority=Priority.CRITICAL,
                )
            )
            decision = approver.decide(req, severity=incident.severity)
            envs.append(
                self._emit(
                    msg_type=MsgType.APPROVAL_DECISION,
                    payload=decision,
                    trace_id=f"trace:{incident.incident_id}",
                    tick=tick,
                    correlation_id=req.request_id,
                )
            )
            bb.record(
                tick,
                "hitl_decision",
                incident_id=incident.incident_id,
                approved=decision.approved,
                reasons=list(gate.reasons),
            )
            if not decision.approved:
                return AwardOutcome(False, None, envs, approval_denied=True)

        # (c) atomic commit -- the single guarantee that no asset is double-committed
        if not bb.try_commit(win.asset_id, incident.incident_id, task.task_id, tick):
            bb.record(tick, "commit_failed_taken", asset_id=win.asset_id, task_id=task.task_id)
            return AwardOutcome(False, None, envs)
        incident.status = IncidentStatus.ASSIGNED
        incident.assigned_asset = win.asset_id
        incident.assigned_tick = tick

        # (d) record award, route, and commitment
        asset = bb.assets[win.asset_id]
        route = routing.route_for(asset, incident.node)
        arrive_tick = tick + max(1, round(route.eta_ticks))
        bb.set_route(win.asset_id, route.path, incident.node, arrive_tick)
        envs.append(
            self._emit(
                msg_type=MsgType.AWARD,
                payload=AwardPayload(
                    task_id=task.task_id,
                    incident_id=incident.incident_id,
                    asset_id=win.asset_id,
                    route=route.path,
                    eta_ticks=route.eta_ticks,
                    welfare_score=winner.score,
                    requires_approval=gate.requires_approval,
                ),
                trace_id=f"trace:{incident.incident_id}",
                tick=tick,
                correlation_id=task.task_id,
                priority=incident.priority,
            )
        )
        envs.append(
            self._emit(
                msg_type=MsgType.ROUTE_COMPUTED,
                payload=RouteComputedPayload(
                    asset_id=win.asset_id,
                    path=route.path,
                    eta_ticks=route.eta_ticks,
                    risk=route.risk,
                    mode=ASSET_TRAVEL_MODE[asset.asset_type].value,
                ),
                trace_id=f"trace:{incident.incident_id}",
                tick=tick,
            )
        )
        envs.append(
            self._emit(
                msg_type=MsgType.ASSET_COMMITTED,
                payload=AssetCommittedPayload(
                    asset_id=win.asset_id,
                    task_id=task.task_id,
                    incident_id=incident.incident_id,
                    commit_tick=tick,
                ),
                trace_id=f"trace:{incident.incident_id}",
                tick=tick,
            )
        )
        bb.record(
            tick,
            "awarded",
            asset_id=win.asset_id,
            incident_id=incident.incident_id,
            welfare=round(winner.score, 4),
            gated=gate.requires_approval,
        )
        return AwardOutcome(True, win.asset_id, envs)

    def rollback_award(
        self, asset_id: str, bb: Blackboard, tick: int, reason: str
    ) -> list[Envelope]:
        """Reverse a still-reversible award: release the asset and emit a rollback record.

        Reversal is refused once the asset is ON_SCENE -- the crew is mid-rescue and that
        work is irreversible, so an on-scene asset is never yanked. The guard lives here (not
        only in the lease-reclaim caller) so the invariant holds for any future trigger."""
        if bb.assets[asset_id].status is not AssetStatus.EN_ROUTE:
            return []
        bb.release_asset(asset_id, tick, reason=reason)
        return [
            self._emit(
                msg_type=MsgType.ROLLBACK_ISSUED,
                payload=RollbackIssuedPayload(target="commit", asset_id=asset_id, reason=reason),
                trace_id=f"rollback:{asset_id}:{tick}",
                tick=tick,
                priority=Priority.HIGH,
            )
        ]

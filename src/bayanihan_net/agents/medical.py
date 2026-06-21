"""Medical agent -- a logistics agent specialized for casualties, with escalation duty.

It bids medical teams like any logistics agent, but adds the one judgement the medical
domain demands: a **mass-casualty** check. When open medical demand outstrips total team
capacity, it raises a CRITICAL escalation so the command center (and, through it, the
human) can request mutual aid -- exactly the A2A cross-agency path in ``interop/``.
"""

from __future__ import annotations

from ..blackboard import AssetStatus, Blackboard
from ..config import AssetType, IncidentType, Scenario
from ..incidents import IncidentStatus
from ..messages import Envelope, EscalationRaisedPayload, MsgType, Priority
from .logistics import LogisticsAgent


class MedicalAgent(LogisticsAgent):
    """Bids medical teams and watches for mass-casualty overload."""

    role = "medical"
    default_scopes = ("asset:bid", "asset:commit", "escalation:raise")

    def __init__(self, agent_id: str, scenario: Scenario, owner: str = "logistics-medical") -> None:
        super().__init__(agent_id, scenario, owner)
        self._in_overload = False  # edge-trigger state so we escalate once per episode

    def mass_casualty_check(self, bb: Blackboard, tick: int) -> list[Envelope]:
        """Escalate when open medical demand first exceeds total medical-team capacity.

        Edge-triggered: one escalation when the system *enters* overload, not one every
        tick it stays overloaded -- a sustained alarm is one decision for the human, not a
        storm of identical pages (the over-escalation failure mode, mitigated)."""
        open_med = [
            i
            for i in bb.incidents.values()
            if i.status is IncidentStatus.OPEN and i.itype is IncidentType.MEDICAL
        ]
        demand = sum(i.people for i in open_med)
        # Count only IN-SERVICE teams: a team knocked out by the agent-kill stress is not
        # capacity, and including it would understate overload exactly when it matters most.
        capacity = sum(
            a.capacity
            for a in bb.assets.values()
            if a.asset_type is AssetType.MEDICAL and a.status is not AssetStatus.DISABLED
        )
        if demand <= capacity or not open_med:
            self._in_overload = False  # cleared -> re-arm for the next episode
            return []
        if self._in_overload:
            return []  # already alarmed this episode
        self._in_overload = True
        worst = max(open_med, key=lambda i: i.severity)
        payload = EscalationRaisedPayload(
            incident_id=worst.incident_id,
            reason=f"mass-casualty: medical demand {demand} exceeds team capacity {capacity}",
            severity=worst.severity,
        )
        bb.record(tick, "mass_casualty_escalation", demand=demand, capacity=capacity)
        return [
            self._emit(
                msg_type=MsgType.ESCALATION_RAISED,
                payload=payload,
                trace_id=f"trace:{worst.incident_id}",
                tick=tick,
                priority=Priority.CRITICAL,
            )
        ]
